# 本地工具与 MCP 接入

工具共用现有 ToolGateway 和 `tool_calls`，不新增数据库 schema、模型或聊天历史。Agent 模式使用现有模型决定下一步；手动调用不调用模型。工具结果始终标为 `source_type=tool`，不是知识库引用。

## 当前工具

| ID | 范围 | 实际执行与边界 |
| --- | --- | --- |
| `local.time` | 两类聊天 | 读取本机 UTC 时间，无外发 |
| `local.calculate` | 两类聊天 | 本地有界十进制四则运算，无外发 |
| `kb.documents` | 固定知识库聊天 | 只读当前绑定库的资料名称和状态；普通聊天不提供 |
| `weather.city_search/current/forecast` | 两类聊天，可选 | 默认关闭；显式配置后的三项只读查询免逐次审批，地点参数外发至和风天气 HTTPS，每次最多 1 次请求且可能计费，见[配置](QWEATHER.md) |
| `mcp.local.calculate` | 两类聊天，审查登记后 | 本文参考服务；真实 Streamable HTTP，默认示例要求审批 |

计算器接受 `{"expression":"(0.1 + 0.2) * 3"}`。只支持数字、小数点、括号、`+ - * /`；不支持函数、幂、科学计数法、变量或代码。表达式最多 256 字符、括号最多 32 层、AST 最多 64 节点、数字字面量最多 64 字符。按 28 位有效数字计算，返回十进制字符串；`rounded=true` 表示发生近似，不能称为任意精度计算。除零和结果超限返回安全失败原因，失败记录无成功结果。

## 启动本地参考 MCP

这是需要显式启动的只读参考服务，不是任意代码、文件或外部写入工具。无需模型密钥或数据库。沿用锁文件中的 MCP SDK 2.2.0（MIT）与已有 FastAPI/Uvicorn 依赖，见[许可](vendor/agent/README.md)。

在 `backend` 目录运行，先检查 8765 的占用及进程归属；占用时选择另一个空闲端口，不停止未知服务。

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
uv sync --locked --extra mcp
uv run --locked --extra mcp --no-env-file python -m app.tools.local_mcp --port 8765
```

服务仅绑定 `127.0.0.1`，地址 `http://127.0.0.1:8765/mcp`；端口已占用会失败。关闭在该终端按 Ctrl+C。API 不自动启动该进程。

另开终端输出本示例的审查模板（离线读取自己的 descriptor，不联系远端）：

```powershell
uv run --locked --extra mcp --no-env-file python -m app.tools.local_mcp --port 8765 --print-registry
```

核对输出中的地址、版本、完整输入/输出 schema 和工具效果，再将该数组中的服务条目合并到**仓库根目录的 `.local/runtime/tools/registry.json`**（从 `backend/` 目录访问时为 `../.local/runtime/tools/registry.json`）。已有登记须保留，不能用示例覆盖；没有文件时才新建数组。不要加入凭证或私人内容。模板中的工具 ID 为 `mcp.local.calculate`，`scope=any`、`approval_required=true`；`descriptor` 必须使用命令输出的完整对象，不能手工缩写。命令输出使用 UTF-8。

在受控 API 环境设置 `CITERAG_MCP_ENABLED=true`，按照 [Agent 启动说明](AGENT.md)启动唯一 API owner。Agent 自动选择工具还需明确启用 Agent/回答、完成受控模型配置及适用付费授权；手动入口无需模型。实际业务库迁移仍需另行授权，本示例不迁移数据库。

## 审批与兼容

每项 MCP 工具可设置 `approval_required` 布尔值。显式 `true` 始终走手动/任务审批；省略或旧登记中的 `false` **不再单独获得免审批资格**，没有有效审核记录时同样逐次审批。`null`、数字或字符串不是布尔值，会拒绝登记。服务自报的 `readOnly` annotation 也不能证明实际行为。内置工具及天气工具的审批策略不受此项改变。

首版免审批仅面向维护者控制部署、可核实版本、无凭据、免费且实际只读的 MCP 服务。维护者须对每个工具审查来源、实际行为、接收方、权限和公开参数范围，再在该工具条目登记完整的 `unattended_read_review` 对象：

| 字段 | 审核内容 |
| --- | --- |
| `reviewed_by`, `reviewed_at`, `evidence_ref` | 审核人、带时区的审核时间、无秘密的证据引用 |
| `source`, `deployment_id`, `behavior` | 可控来源、已核实的发布/部署标识、实际读行为与副作用 |
| `url`, `server_version`, `descriptor_sha256` | 与服务登记完全一致的固定目标、版本和完整 descriptor 规范化 SHA-256 |
| `data_destination`, `allowed_data` | 与登记 URL 相同的接收方；首版只允许 `public` |
| `permissions`, `cost` | 首版分别必须是空数组 `[]` 和 `free` |
| `allowed_arguments_schema` | 限定顶层公开参数：字符串仅有限枚举，数字必须有上下界；不得包含自由文本、嵌套对象/数组或额外字段 |

缺少审核对象时逐次审批；有审核对象但字段缺失、未知、格式不符或与目标/版本/descriptor 不匹配时，登记直接失败，须修正后再启动 API。符合工具输入 schema 但超出审核的公开参数范围时逐次审批；不符合工具输入 schema 时直接拒绝。完整 descriptor 的摘要按 UTF-8 规范化 JSON（`sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False`）计算。证据必须来自维护者实际核查，不能仅复制远端声明；参考 CLI 模板保留 `approval_required=true`，不预填虚构审核结论。

目标、服务版本、descriptor、参数范围、权限、去向、收费状态或审核记录变化后，旧豁免不再适用，须重新审核。API 仍会在每次 MCP 调用前复核服务版本和完整 descriptor；这无法证明服务在同一版本和 descriptor 下没有被原地替换，因此不可核实部署的第三方服务始终逐次审批。审批不是信任证明，也不取代登记审查。

登记在 API 启动时加载；更改后先结束活动调用，再有序重启自己的 API。等待任务恢复时会重新检查工具策略，策略变更不会沿用旧批准；手动调用也继续检查当前登记、绑定、归属和版本。没有新增公共注册 URL/命令接口，仍只支持已审查的 Streamable HTTP 只读服务；认证凭证加载、stdio、写入及 sampling/elicitation 尚未接入。

## 失败、取消和记录

| 情况 | 行为 |
| --- | --- |
| 输入不符合 schema | 执行前拒绝，不创建可执行记录或联系 MCP |
| HTTP 401 / 403 / 429 | 安全区分认证、权限、限流；不展示供应商响应或凭证 |
| 连接失败 / 超时 | 保留失败记录，提示检查登记和服务状态 |
| 服务版本 / 工具 descriptor 变化 | 拒绝，须重新审查登记 |
| `isError` / 输出 schema 不符 / 不支持的内容 | 失败，不充当成功结果 |
| 用户拒绝审批 | 保存拒绝，无工具执行结果 |
| 用户取消 | 取消本地在途任务，不承诺远端已完成的操作可撤销 |

调用 ID、归属聊天、参数指纹、审批状态、安全错误码和有界结果复用现有持久记录。MCP 客户端 SDK 的传输/会话日志只保留级别、模块、时间和通用诊断提示，删除远端正文、参数、会话标识及异常堆栈；具体安全原因从调用记录查看。相同请求重放读取原结果；没有自动无限重试或静默改用其他工具。Agent 仍限 6 次决策、4 次工具尝试和 60 秒活动预算；单工具最多 15 秒。等待、恢复、清理及保留策略见 [Agent 说明](AGENT.md)。业务记录属于私有数据，不提交到 Git。

MCP 不证明服务可信；远端内容不能充当指令或扩大权限。参见官方[工具协议](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)与[取消协议](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)。

## 控件与实际调用

| 操作 | 前端方法 | 服务端 / MCP |
| --- | --- | --- |
| 工具与调用记录 | `conversationTools` / `toolCalls` | GET `/api/conversations/{chat}/tools` 与 `/tools/calls` |
| 手动参数与调用 | `ToolInvocation` → `runTool` → `invokeTool` | POST `/api/conversations/{chat}/tools/calls` → 网关 |
| 手动确认 / 拒绝 | `decideTool` | POST `/tools/calls/{call}/decision`，待审批后才可执行 |
| 模型请求工具 | `startAgent` | POST `/api/conversations/{chat}/agent-runs` → 同一网关 |
| 任务批准 / 拒绝 | `resumeAgent` | POST `/agent-runs/{run}/resume`，重新核对 generation 与策略 |
| 取消任务 | `cancelAgent` | POST `/agent-runs/{run}/cancel`，实际取消在途任务 |
| MCP 计算 | `MCPAdapter.call`（后端） | `initialize` → 版本检查 → `list_tools` 契约检查 → `call_tool` |

表中缩写路径均继续以 `/api/conversations/{chat}` 开头。审批接口和业务 schema 保持原样；语音继续使用当前通话的控制端凭证与同一 AnswerService。Agent 语音回答在保存且状态为 `answered` 后才播报，补参或审批等待期间不播报草稿。关闭 Agent 时，普通语音回答可分段提前播报；知识库回答仍须核验并保存后才播报。

## 集中验收

1. 在独立空库按 Agent 说明准备环境，启动本地参考 MCP 和 API。先用普通聊天的“工具与调用记录”计算 `0.1 + 0.2`，预期真实结果 `0.3`；计算 `1 / 0`，预期除零原因及失败记录。
2. 调用 MCP 计算器，审批前只显示待确认参数；拒绝后无结果，批准后保存真实 `0.3`，刷新仍可查看。
3. 已获模型调用授权并启用 Agent 后，提问“用计算器计算 0.1 加 0.2”。是否选择工具取决于真实模型，不以合成模型结果替代这项验收。若请求本文默认登记的 MCP 计算器，须先审批；只有有效审核记录覆盖本次公开参数时才可豁免，显式 `approval_required=true` 始终审批。保存回答不生成知识库引用。
4. 停止自己的 MCP 服务后重新调用并批准，预期安全连接失败；恢复服务后仅主动重试，不自动重做旧调用。

隔离 PostgreSQL、替身模型编排、真实本地 MCP TCP/HTTP/SDK 握手及浏览器操作可分别验证。真实模型规划、用户外部 MCP、供应商鉴权/额度、真人语音工具审批和外部写入均未完成集中验收；历史 M0—M3 未完成项不因此改变，付费调用须另获授权。
