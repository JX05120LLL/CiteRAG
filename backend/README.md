# CiteRAG 后端

Python 3.12、FastAPI、SQLAlchemy/Alembic、PostgreSQL 和可选的 LightRAG/语音依赖。首版仅供回环地址上的本地单用户使用，无注册、登录或管理员。业务库、LightRAG 引擎库及私有文件目录相互独立。

## 启动与迁移

在本目录运行以下命令，可启动无数据库配置的 API 并查看真实缺项：

```powershell
uv sync --locked
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

`GET /api/health` 只表明进程存活；`GET /api/status` 分别报告数据库、引擎、模型、备份等状态。持久服务需单独准备 PostgreSQL 业务库，将敏感的 CITERAG_DATABASE_URL 注入受控进程环境，并在**新建空库**上显式执行 `uv run --no-env-file alembic upgrade head`。当前源码要求 `0012_agent_runs`。已有业务库升级前要停写并成套备份业务库、引擎库、私有原文/图片与配置；启动不会自动迁移或认领旧资料。未完成显式升级的旧业务库不能用本版本启动。

入库、问答、备份、LiveKit 传输和语音助手默认关闭；安装可选依赖或配置 Key 不会自动启用。可选依赖使用 `uv sync --locked --extra rag --extra voice` 安装，配置字段见 [app/config.py](app/config.py)。不要把本地服务开放到局域网或公网，应用拒绝代理来源身份并核对 Host、Origin 与资源归属。

系统状态页的功能检测独立于上述业务开关：进入页面会通过 `POST /api/status/functional/auto` 自动检测已配置的模型、ASR 和 TTS。每轮模型检测至多一次 DashScope 请求；TTS 至多一次 MiniMax 请求；ASR 至多一次 MiniMax 合成和一次火山引擎识别请求。发送的是固定短提示或合成语音，不读取聊天正文、用户录音或知识库资料，但可能产生供应商费用。结果按配置指纹缓存一小时；重复进入页面复用有效结果，“刷新系统状态”使用 `force=true` 重新检测，运行中的同项检测复用原任务。

`GET /api/status/functional` 只读取检测记录。模型检测使用固定 `qwen-flash` 文字请求，不逐一验证图片模型、Embedding 或重排能力。当前知识检索检测保持 `not_checked`，不执行 LightRAG 检索；单项模型或语音检测成功不代表知识库、真人设备或完整业务链路验收通过。

## 主要模块

| 位置 | 职责 |
| --- | --- |
| `app/api/` | HTTP/SSE 协议、输入校验与本地访问边界 |
| `app/services/` | 知识库、受管资料、聊天、AnswerService、摘要和保留期 |
| `app/rag/` | 固定 LightRAG SDK、检索、来源映射与核验 |
| `app/ingestion/` | 原文私有保存、受限解析与持久任务 |
| `app/images/` | 图片私有附件与观察 |
| `app/voice/` | 会话租约、RTC 音轨、ASR/VAD、TTS 与取消 |
| `app/tools/` | 统一工具目录、范围、审批与持久结果；本机工具、可选和风 HTTP 工具及已审查 MCP |
| `app/agent/` | 可选 LangGraph 串行任务、补参/审批、预算、租约和持久检查点 |
| `app/providers/` | 后端模型适配与安全错误分类 |
| `migrations/` | 业务库的显式、追加式 Alembic 迁移 |

`POST /api/conversations` 须显式提供空或非空 `kb_id`。普通聊天不调用知识库路由或 LightRAG；知识库聊天固定一个库，资料回答仍需当前库原文证据。语音最终转写、文字和图片观察后的问题复用 AnswerService。公开产品边界见[README](../README.md)。

普通聊天保留至使用者手动删除。知识库聊天按最后一次用户输入计算 180 天保留期；空聊天按创建时间计算。过期聊天不可访问，由保留任务分批清理；摘要不会替换原始聊天记录，同库共享摘要也不能作为本轮资料证据。

工具入口为 `GET /api/conversations/{id}/tools`、`GET/POST /api/conversations/{id}/tools/calls` 和 `POST /api/conversations/{id}/tools/calls/{call_id}/decision`。调用带客户端 UUID 幂等键，服务端固定注册表，普通聊天不可调用知识库工具；历史结果标记为 `source_type=tool`，不进入 AnswerService 的引用。审批只对已持久记录的确切参数生效，拒绝和异常也写入状态。内置生产工具仍为只读工具；没有注册真实外部写入工具。

可选 Agent 自动选择工具、返回结果后继续回答，支持补参和审批暂停。安装、显式检查点初始化、协议、回滚与 MCP 审查规则见 [Agent 与工具网关](AGENT.md)。`CITERAG_AGENT_ENABLED`、`CITERAG_MCP_ENABLED` 均默认 `false`；自动任务不另建 RAG 或聊天记录，最终回答仍经 AnswerService 保存与核验。公开迁移验证仅使用隔离环境，不能据此判断已有业务库是否完成升级。

可选和风天气提供城市搜索、实时天气与 1—7 天预报，默认关闭。内置三项只读天气查询免逐次审批，每次调用最多一次供应商 GET 请求，无自动重试；地点参数会外发，可能产生费用。手动表单也可调用，无需模型生成。配置、错误、供应商归属与高德官方 MCP 的接入限制见 [天气工具说明](QWEATHER.md)。合成验证不代表真实天气账户、模型规划或高德接入验收通过。

## 检查

```powershell
uv sync --locked --extra rag --extra voice --extra agent --extra mcp
$publicBackendTests = @(Get-Content -LiteralPath ../.github/public-backend-tests.txt)
uv run --locked --extra rag --extra voice --extra agent --extra mcp --no-env-file ruff check app migrations @publicBackendTests
uv run --locked --extra rag --extra voice --extra agent --extra mcp --no-env-file python -m compileall -q app migrations
uv run --locked --extra rag --extra voice --extra agent --extra mcp --no-env-file alembic upgrade head --sql
uv run --locked --extra rag --extra voice --extra agent --extra mcp --no-env-file pytest -q @publicBackendTests
```

公开后端测试的唯一清单为 [.github/public-backend-tests.txt](../.github/public-backend-tests.txt)，每行是相对于 `backend/` 的 `tests/test_*.py` 路径；CI 的边界检查、Ruff 和 pytest 共用该清单。以上命令须在 `backend/` 执行；运行 PostgreSQL 用例前，将 `CITERAG_TEST_DATABASE_URL` 指向独立可丢弃测试库，用例会创建和删除测试 schema，不能使用实际业务库。未配置时跳过，不能说数据库测试通过。GitHub CI 的临时数据库和合成模型替身不验证用户业务数据、真实模型或真人语音。其余检查见[工作流](../.github/workflows/ci.yml)。
