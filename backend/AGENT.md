# LangGraph Agent 与统一工具网关

可选单 Agent 串行编排，复用 AnswerService、现有普通回答模型和知识库核验。没有注册真实写入工具；审批和写入结果未知的行为使用公开合成工具验证。真实模型、用户 MCP 服务与真人语音尚未验收本轮升级。

## 调用链与边界

文字、图片观察或 ASR 最终转写 → AnswerService 创建正式问题/尝试 → 固定聊天类型与原检索链 → LangGraph 模型决策 → 网关校验与执行 → 有界工具结果返回模型 → AnswerService 核验并提交 → 界面/语音 TTS。

普通聊天只用自己的上下文和 `scope=any` 工具，不访问知识库；固定库聊天保持原两级路由、库修订/活动空间及事实支持检查。工具输出与知识库原文引用分开。Agent 模式下生成草稿先缓冲，回答保存且状态为 `answered` 后才交给语音播报；补参或审批等待期间不播报草稿。关闭 Agent 时原文字 SSE 保留，普通语音回答可分段提前播报，知识库回答仍须核验并保存后才播报。

| 限制 | 当前实现 |
| --- | --- |
| 并发 | 唯一 API owner，最多两个执行任务，每聊天一个活动任务 |
| 循环 | 每任务最多 6 次决策生成、4 次工具尝试；重复无进展调用停止 |
| 时间 | 60 秒活动预算、30 秒执行租约，10 秒续租；等待不占执行槽 |
| 恢复 | 补参/审批等待 24 小时；恢复重查图版本、绑定、附件、权限、参数和工具策略 |
| 幂等 | 稳定任务/步骤/调用键和持久结果；重复恢复不能重做副作用 |
| 失败 | 只读失败可由模型明确处理；写入取消/超时记 `unknown`，禁止盲重试 |
| 取消 | 实际取消生成/工具任务；语音沿用停止播放、清空缓冲与 generation 门禁 |

6 次指 Agent 决策生成；知识库路由、图片观察、摘要和支持核验可能另有模型请求，不能把它表述为整轮供应商总请求数限制。模型上下文沿用 Token 预算，工具结果总输入限约 3000 估算 Token、每结果最多 4000 字符、参数最多 2048 字符。重启不重置持久计数；崩溃后未记账时间保守补扣，可能包含部分停机时间。租约失效会取消实际任务。

## 持久数据与恢复

`agent_runs/agent_steps/agent_events` 保存状态、决定、事件序号和预算；`tool_calls` 保存版本、参数指纹、审批及结果。`agent_checkpoints` 独立 schema 保存 LangGraph 检查点，不是第二套聊天历史。等待态在旧消息接口投影为 `running` 加等待 `phase`，不伪造已保存回答。

重启将执行中任务中断，等待对象保留；用户必须显式恢复，不自动调用模型。已提交结果复用；执行已开始但无持久结果的写入保持未知。恢复只支持相同图版本和仍有效的绑定。执行门禁在加锁后刷新数据库值，预读的 ORM 对象不能掩盖失效租约、执行轮次、执行器归属或取消状态。GET 任务 SSE 断开只解除订阅，显式取消才终止任务；旧 POST 回答 SSE 语义保留。

终结任务的检查点、临时上下文和模型决定 7 天后清理；工具记录/业务事件随所属聊天保留，普通聊天至主动删除，知识库聊天仍为原 180 天策略，图片期限不变。日常日志不输出正文或凭证；有界参数与工具结果属于私有业务记录，不能发布这些数据库或检查点。

## 安装和启动

先核对端口与进程归属。以下只适用于新空库或另行授权迁移的环境。敏感连接信息由受控后端进程环境注入，不写入 Git。

```powershell
# backend 目录；可选依赖按需要安装
uv sync --locked --extra rag --extra voice --extra agent --extra mcp
# 先将 CITERAG_DATABASE_URL 指向独立空库，再显式执行
uv run --locked --extra agent --no-env-file alembic upgrade head
uv run --locked --extra agent --no-env-file python -m app.agent.checkpoints
```

完成受控模型配置和适用调用授权后，按需要设置 `CITERAG_ANSWER_ENABLED=true`、`CITERAG_AGENT_ENABLED=true`。MCP 仍默认关闭。启动命令：

```powershell
uv run --locked --extra rag --extra voice --extra agent --extra mcp --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

前端在 `frontend` 运行 `pnpm dev`；默认 5173 代理 8000，两端端口必须先确认空闲。启动检查 schema 与已初始化检查点版本，不自动建表。关闭前取消活动任务、挂断通话，再在各自终端 Ctrl+C；等待任务保留，下次由用户显式决定恢复。不要停止未知服务。

已有业务库升级：停写并停止唯一 API owner → 备份业务库（含整个检查点 schema）、引擎库、原文/图片与受控配置 → 验证恢复到新隔离环境 → 获得迁移授权 → 显式升级与初始化 → 再启动。回退优先关闭 Agent/MCP 并保留新 schema；新增记录或检查点非空时 downgrade 拒绝。完整恢复也不能撤销外部系统已经发生的写入。

## 公共协议

下列任务路径基于 `/api/conversations/{chat}/agent-runs`；沿用 Host/Origin、本地 owner 和聊天归属门禁。

| 操作 | 方法/路径 | 内容 |
| --- | --- | --- |
| 能力 | GET `/api/agent/capability` | 实际开关与限额，不调用模型 |
| 开始 | POST 基路径 | `client_message_id,text,mode=auto,image_ids` |
| 重试 | POST `/retry` | 原 `message_id` 与新 `request_id` |
| 最近任务 | GET 基路径 | 最新 20 条，界面折叠历史 |
| 快照 | GET `/{run}` | 状态、等待对象、generation、seq、计数 |
| 补参/审批/恢复 | POST `/{run}/resume` | `request_id,generation,input`；审批只能确切 `approve` 布尔值 |
| 停止 | POST `/{run}/cancel` | 实际取消任务；不承诺撤销外部副作用 |
| 事件 | GET `/{run}/events?after=N` | Last-Event-ID、序号重放、心跳与终态；旧引用仍检查当前可见性 |

任务审批不使用手动工具的 decision 接口。通话任务恢复/取消还需当前 `voice_session_id` 和仅驻留控制标签页内存的 `control_token`；ASR 的“好的”不能批准工具操作。

文字任务面板执行中每秒读取，等待或无活动时每 5 秒读取；断线后以 2/4/8/16/30 秒退避，保留最后确认的状态，重新连通不自动批准。页面卸载释放轮询计时器。

## MCP 审查范围

仅提供受控 Streamable HTTP 适配器，`CITERAG_MCP_ENABLED=true` 时读取 `.local/runtime/tools/registry.json`。没有公共新增 URL/命令接口、自动注册发现或任意 stdio 子进程。固定 HTTPS 或回环 HTTP 端点，不允许 URL 内凭证/查询参数、跨端点跳转或系统代理；请求参数会发送到登记服务。

维护者必须审查服务版本、完整工具 descriptor、输入/输出 schema、目标、效果、归属范围和数据外发。登记格式是数组：服务具有 `url,version,tools`；每项工具具有 `id`（`mcp.` 前缀）、`title,scope,descriptor`，可选布尔 `approval_required`。省略或旧 `false` 本身不授予豁免，没有有效审核记录时逐次审批。只有有效的维护者 `unattended_read_review` 且本次参数处于限定的公开范围内才可免逐次审批；显式 `true` 始终审批，审批不能替代审查。目前 MCP 只读；最多 8 个服务、每服务 8 个已审查工具，运行目录不得超过 50 条或分页。每次调用重查版本和契约，变化即拒绝。登记文件位于仓库根目录的 `.local/runtime/tools/registry.json`，从 `backend/` 目录访问时为 `../.local/runtime/tools/registry.json`；审核字段与具体示例见[工具与 MCP](TOOLS.md)。

`isError` 不算成功；只接文字/结构化结果，输入在创建可执行记录前校验，输出按已审查 schema 校验、限长，并禁止外部 schema 引用。HTTP 401/403/429 保留安全的认证/权限/限流原因，不输出远端诊断内容。图片资源、任意嵌入资源、sampling/elicitation 和 MCP 凭证加载未接入。远端返回内容不是指令，不能扩大工具权限或充当知识库引用。MCP 协议本身不证明服务可信，真实服务须逐个审查和独立验收。

## 外部 HTTP 天气工具

天气默认关闭；显式启用且 Host/Key 齐全后可用 `weather.city_search/current/forecast`。使用本地 Python 执行器与受控 QWeather HTTPS 请求，`backend=local` 不表示没有外发；`destination` 明确标为 QWeather。两类聊天均可使用，三项限定参数的只读查询免逐次审批；每次最多 1 次供应商请求，将地点参数发往和风天气且可能计费，不发送聊天正文、知识库正文或文件，也不把结果变成知识库引用。手动入口支持简单文字/数字参数，复杂 MCP 参数仍明确禁用手动入口。关闭开关/缺配置时不加入目录。配置及真实验收步骤见 [天气工具](QWEATHER.md)。

## 依赖与验证

版本锁在 `uv.lock`；LangGraph/checkpoint/MCP 是可选 extras。许可见 [依赖说明](vendor/agent/README.md)。公开 CI 使用隔离 PostgreSQL、模型替身和本地合成 MCP，不用真实密钥。隔离、替身与本地 MCP 验证不替代真实模型规划、供应商、用户外部 MCP 或真人设备验收；付费调用须另获授权。
