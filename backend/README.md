# CiteRAG M0 后端

Python 3.12、FastAPI、SQLAlchemy 2、Alembic 与 PostgreSQL 17。首版为本地单用户，无管理员、注册、登录或密码流程；服务端维护一个内部本地归属，关联自有知识库及聊天。尚无建库入库、问答、图片、语音和真实模型连接。

## 安装与无配置启动

在 `backend/` 执行：

```powershell
uv sync --locked
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

无数据库配置时也能直接打开前端工作台；`GET /api/health` 只说明进程存活，`GET /api/status` 显示未配置，数据库相关接口返回 503。应用不会自动建表、升级迁移或生成演示资料，也不自动读取真实 `.env` 文件。

## 业务库与配置

使用独立 PostgreSQL 业务库，连接串必须为 `postgresql+asyncpg` 协议并包含主机与库名。引擎数据库由后续接入管理，Alembic 不管理引擎表。真实连接串只从受控进程环境注入，不写仓库、命令行参数、截图或聊天；隐藏输入方式见 [本地开发](../docs/development/LOCAL-DEVELOPMENT.md)。

| 环境变量 | 默认与说明 |
|---|---|
| `CITERAG_DATABASE_URL` | 无；业务数据库连接串，作为敏感值处理。 |
| `CITERAG_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173`；逗号分隔的精确回环浏览器 Origin，禁止通配符和路径。前端换到 5174 时同步修改。 |
| `CITERAG_API_WORKERS` | 只能为 `1`；`WEB_CONCURRENCY` 也不能大于 1。 |

配置独立业务库后显式迁移，再用上面的命令启动：

```powershell
uv run --no-env-file alembic upgrade head
```

当前 head 为 `0002_local_single_user`。新库先经过原有 `0001_m0_accounts`，再建立单例 `local_profiles` 并为知识库和聊天新增 `local_owner_id`。旧 `users`、`auth_sessions`、知识库和聊天记录保留；聊天旧 `owner_id` 外键列保留且允许新本地聊天不填写。迁移不认领旧资料，不把已有账号变成默认用户，不需要执行管理员初始化。

已有 CiteRAG 数据库升级前先备份，确认只作用于本项目业务库。正常运行不执行 downgrade；`0002` 在存在已归属本地的知识库或聊天时拒绝回退，避免丢失归属。数据库本身仍需有效 PostgreSQL 连接凭证，它不等于网页账号。

配置数据库后，启动必须持有独占 PostgreSQL owner 会话锁；第二个 API 进程启动失败。取得锁后检查 Alembic 版本，缺失、旧版或不兼容版本拒绝启动，不自动升级。持锁连接丢失后停止受理并终止进程。启动也拒绝全局 `POSTGRES_WORKSPACE` 或当前目录的 `config.ini`，不读取其可能包含的凭证。

## 本地请求边界

API 仅监听回环地址，并验证真实连接来源、Host、Origin 和跨站请求。`--no-proxy-headers` 禁用 Uvicorn 对代理头的信任；应用拒绝 `Forwarded`/`X-Forwarded-*`，不接受客户端伪造来源。Vite 的同源代理不添加这些头，不放开跨域 CORS。写请求必须提供配置允许的 Origin；浏览器跨站读取同样应被拒绝。

当前没有登录 Cookie、会话 token 或 CSRF token 流程，也不读取客户端提交的 owner 身份。不同浏览器共享同一本地安装的资料；服务端仍检查资源归属、聊天与知识库绑定以及库状态。此模式不用于局域网或公网，也不保证对能够访问本机文件和数据库的人员隔离资料。

## 当前 HTTP 契约

- `GET /api/health`：`{status, database_configured}`，进程存活与是否配置数据库，不证明知识引擎已就绪。
- `GET /api/status`：`{status, mode, database, rag, models}`；`mode` 为 `local_single_user`，不返回连接信息。
- `GET /api/knowledge-bases`：`{items:[{id,name,status}]}`，列出本地归属下全部库状态。未实现建库或将库标为 ready 的接口，不自动创建演示库。
- `POST /api/conversations`：`{kb_id,title}` → 201，只允许本地归属下已就绪的知识库，聊天固定该库。
- `GET /api/conversations?limit=20&offset=0`：返回当前本地归属的聊天列表。
- `GET /api/conversations/{id}`：只读取本地归属及正确知识库绑定的聊天，未归属旧记录返回 404。

旧 `/api/auth/*`、`/api/me`、`/api/admin/*` 及管理员初始化 CLI 不再属于有效入口。错误使用 `{detail:{code,message}}`：403 本地来源边界拒绝，404 不存在或不属于本地归属，409 状态冲突，422 参数不合规，503 数据库未配置、持久层或 owner 不可用。未配置业务库的 code 为 `database_not_configured`；连接故障不能伪报成功，也不返回连接信息。

## 验证

```powershell
uv run --no-env-file ruff check .
uv run --no-env-file python -m compileall -q app migrations
uv run --no-env-file alembic upgrade head --sql
```

GitHub CI 使用独立临时 PostgreSQL 17.9，验证新库迁移、重复迁移与真实 API 响应，详见 [CI 说明](../docs/development/CI.md)。上述 `--sql` 命令只生成迁移 SQL，不连接数据库；实际数据库行为由 CI 冒烟或本地集成验证确认。

`backend/tests/` 仅在维护者本机保留，不随仓库发布，新克隆无需运行 `pytest`。已有本地测试文件时，可运行 `uv run --no-env-file pytest -q`；未设置 `CITERAG_TEST_DATABASE_URL` 时真实 PostgreSQL 测试明确跳过，不用 SQLite 或内存库替代。独立测试实例准备、隐藏输入与执行方式见[本地开发的运行检查](../docs/development/LOCAL-DEVELOPMENT.md#运行检查)。本地测试还覆盖旧库升级保留、本地归属、知识库绑定及 owner 生命周期，不能用较小范围的 CI 冒烟代替。禁止把真实业务库或共享实例作为测试目标。

LightRAG 为锁定提交的可选 `rag` 依赖，`uv sync --locked --extra rag` 才安装。安装成功不代表四类 PG 存储、双库真实检索或模型验证通过。当前行为结果与缺项见 [M0 验证记录](../docs/development/M0-VALIDATION.md)，旧账号方案计数只作历史证据。
