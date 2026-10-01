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

工具入口为 `GET /api/conversations/{id}/tools`、`GET/POST /api/conversations/{id}/tools/calls` 和 `POST /api/conversations/{id}/tools/calls/{call_id}/decision`。调用带客户端 UUID 幂等键，服务端固定注册表，普通聊天不可调用知识库工具；历史结果标记为 `source_type=tool`，不进入 AnswerService 的引用。审批只对已持久记录的确切参数生效，拒绝和异常也写入状态。内置生产工具仍为只读工具；没有注册真实外部写入工具。

可选 Agent 自动选择工具、返回结果后继续回答，支持补参和审批暂停。安装、显式检查点初始化、协议、回滚与 MCP 审查规则见 [Agent 与工具网关](AGENT.md)。`CITERAG_AGENT_ENABLED`、`CITERAG_MCP_ENABLED` 均默认 `false`；自动任务不另建 RAG 或聊天记录，最终回答仍经 AnswerService 保存与核验。新迁移只在隔离环境验证，实际业务库未迁移。

可选和风天气提供城市搜索、实时天气与 1—7 天预报，默认关闭。每次向外发送地点参数前沿用确切参数审批；手动表单也可调用，无需模型生成。配置、错误、供应商归属与高德官方 MCP 的接入限制见 [天气工具说明](QWEATHER.md)。真实天气/高德尚未验收。

## 检查

```powershell
uv run --no-env-file ruff check .
uv run --no-env-file python -m compileall -q app migrations
uv run --no-env-file alembic upgrade head --sql
uv run --no-env-file pytest -q
```

PostgreSQL 用例需要独立可丢弃测试库；未配置时跳过，不能说数据库测试通过。GitHub CI 的临时数据库和合成模型替身不验证用户业务数据、真实模型或真人语音。公开测试文件与检查命令见[工作流](../.github/workflows/ci.yml)。
