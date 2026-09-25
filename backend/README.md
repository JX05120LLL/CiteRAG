# CiteRAG 后端

2026-09-25 补记：新提问可用 `mode=auto`，由 `qwen-flash` 识别检索意图，服务端转发到语义路径、已确认文档属性等值路径或当前知识库原文块的完整编号/短语字面定位。模型只可引用服务端候选 ID，或逐字复制问题中的定位词；服务端校验并保存路由供同消息重试，回答视图仅附可选 `route` 类型。多份资料命中或结果过多时澄清，不以 Top-K 充当精确结果。旧 `semantic`/`exact` 请求兼容，默认问答关闭。每次启用后的新自动提问最多增加一次路由模型请求；本轮仅在隔离 PostgreSQL 和本地替身中验证，真实模型请求 0。订单号是知识库原文定位示例，不涉及实时业务连接器。详情见 [M1 验证](../docs/development/M1-VALIDATION.md)。下文分片版本与接口说明保留原时点快照。

Python 3.12、FastAPI、SQLAlchemy 2、Alembic 与 PostgreSQL 17。本地单用户，无管理员、注册或登录。M1-1 至 M1-4 的受管资料、问答来源、聊天摘要及资料生命周期主链路已在本机实现；另有核验并提交后才发送正文的 SSE、180 天聊天保留与可选的每日双库加私有文件备份。SSE 不承诺模型 token 级首响。入库、问答和自动备份默认关闭；图片和语音留后续。M0 超长 Embedding 输入边界暂缓未通过，M0 与完整 M1 均未完成。

本轮只在隔离测试库升级到 `0006_m1_lifecycle`。实际业务库本轮不可达；上轮只读核查为 `0002_local_single_user`，旧 API 无新接口，不能当作当前在线证据。未迁移业务或重启旧 API。默认关闭模型入库与问答；新接口需停写备份业务库、引擎库及私有原文后显式迁移并重启，进度见 [M1 记录](../docs/development/M1-VALIDATION.md)。

上轮 `0002` 库版本与旧接口为当时实查；本轮接手及收尾 API、前端和业务库不可达，引擎库仍可连接，停止原因未定位。不要直接将新代码启动到旧 schema，详见 [交接](../docs/development/HANDOFF.md)。

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
| `CITERAG_INGESTION_ENABLED` | 默认 `false`；允许原文受理和解析，完成后等待。设置 `true` 会让受管队列调用模型入库，须先完成本次真实请求范围/预算授权；保存凭证或已有成功报告不等于授权。 |
| `CITERAG_ANSWER_ENABLED` | 默认 `false`；关闭模型检索与回答。设置 `true` 会发送问题及检索证据给模型，须先说明类别、次数上限、费用和数据影响并另获授权。 |
| `CITERAG_BACKUP_ENABLED` | 默认 `false`；启用每日停写备份，需先确认两库和私有文件同源及可恢复。不会自动补齐旧业务库迁移。 |
| `CITERAG_BACKUP_PG_BIN` | 默认无；启用备份时指向含 `pg_dump`、`pg_restore` 的 PostgreSQL 客户端目录。备份固定保存在私有 `.local/backups/`，只轮换成功的最近七日。 |

配置独立业务库后显式迁移，再用上面的命令启动：

```powershell
uv run --no-env-file alembic upgrade head
```

源码当前 head 为 `0006_m1_lifecycle`：`0004` 增加受管资料与任务，`0005` 增加确认属性、消息和回答尝试，`0006` 增加 revision/空间绑定摘要及删除/替换任务状态、未删除资料唯一索引。旧账号、知识库、聊天和归属全部保留，不认领旧资料。迁移只管理业务库，不管理 LightRAG 表；实际业务库本轮不可达，上轮 `0002` 仅是历史核查。

已有数据升级前先停写，完整备份业务库、引擎库、私有原文、本地配置及版本清单；恢复时保持维护，成套恢复并核对任务、原文和 active_workspace，不能只回退 schema。`0006` 有摘要、删除/替换或清理历史时拒绝 downgrade；`0004` 有受管记录、非零修订或历史遮蔽水位时也拒绝。`0003` 拒绝丢弃已使用的创建键，`0002` 拒绝丢失本地归属。数据库仍需有效连接凭证，它不等于网页账号。

配置数据库后，启动必须持有独占 PostgreSQL owner 会话锁；第二个 API 进程启动失败。取得锁后检查 Alembic 版本，缺失、旧版或不兼容版本拒绝启动，不自动升级。持锁连接丢失后停止受理并终止进程。启动也拒绝全局 `POSTGRES_WORKSPACE` 或当前目录的 `config.ini`，不读取其可能包含的凭证。

## 本地请求边界

API 仅监听回环地址，并验证真实连接来源、Host、Origin 和跨站请求。`--no-proxy-headers` 禁用 Uvicorn 对代理头的信任；应用拒绝 `Forwarded`/`X-Forwarded-*`，不接受客户端伪造来源。Vite 的同源代理不添加这些头，不放开跨域 CORS。写请求必须提供配置允许的 Origin；浏览器跨站读取同样应被拒绝。

当前没有登录 Cookie、会话 token 或 CSRF token 流程，也不读取客户端提交的 owner 身份。不同浏览器共享同一本地安装的资料；服务端仍检查资源归属、聊天与知识库绑定以及库状态。此模式不用于局域网或公网，也不保证对能够访问本机文件和数据库的人员隔离资料。

## 当前 HTTP 契约

- `GET /api/health`：`{status, database_configured}`，进程存活与是否配置数据库，不证明知识引擎已就绪。
- `GET /api/status`：`{status, mode, database, rag, models, backup, retention}`；`mode` 为 `local_single_user`，不返回连接信息。备份失败只显示类别码。
- `GET /api/knowledge-bases`：`{items:[{id,name,status}]}`，列出本地归属下全部库状态，不自动创建演示库。
- `POST /api/knowledge-bases`：`{name,client_request_id}` → 201 `{id,name,status}`，客户端创建键必须为 UUID。名称去首尾空白后为 1–120 字符，拒绝控制字符及额外字段。新库为 `empty`，创建不初始化引擎或发模型请求。同一本地归属、同键、同初始名称重放返回已有库；不同名称使用同键返回 `409 idempotency_conflict`。数据库事务串行去重和容量检查，最多 5 库，超额 `409 capacity_exceeded`；满额不影响已有请求重放。
- `PATCH /api/knowledge-bases/{id}`：`{name}` → 200 `{id,name,status}`，只修改本地归属下的显示名称；不改变状态、空间或创建幂等记录。改名后的原始创建请求重放返回当前名称，不恢复旧名。不存在或未归属资料返回 404。
- `POST /api/conversations`：`{kb_id,title}` → 201，只允许本地归属下已就绪的知识库，聊天固定该库。
- `GET /api/conversations?limit=20&offset=0`：返回当前本地归属的聊天列表。
- `GET /api/conversations/{id}`：只读取本地归属及正确知识库绑定的聊天，未归属旧记录返回 404。
- `PATCH /api/conversations/{id}`：修改本人聊天标题；`DELETE`：拒绝仍有活动回答的聊天，并删除消息、尝试和摘要。
- `POST /api/conversations/{id}/messages`：同一聊天固定知识库，返回经本轮来源核验的回答；默认 `answer_disabled`。`GET` 读取历史，删除/替换后遮蔽旧知识回答与来源。
- `POST /api/conversations/{id}/messages/stream`：SSE `accepted`、`delta`、`saved`、`pending`、`error` 与心跳。`accepted` 表示用户输入已持久化；`delta` 只发送经来源校验且已提交的回答片段，`saved` 为最终保存确认。浏览器断流后按原消息键读取已保存结果，不自动重发模型请求。聊天超过最后一条用户输入 180 天（空聊天从创建算）后不再可访问，后台定时批量清理。
- `POST /api/conversations/{id}/messages/{message_id}/retry`：`{attempt_id}`，仅失败/中断且库 revision/活动空间未变时，在原消息下创建新尝试；同键重放不重复生成。
- `POST /api/knowledge-bases/{id}/documents`：multipart `files`（1–5 份）和 UUID `client_request_id`，可靠保存后返回 202 任务；202 不表示解析或索引成功。同键同载荷重放同一任务，同库相同内容拒绝。
- `GET /api/knowledge-bases/{id}/documents`、`GET /api/knowledge-bases/{id}/jobs`、`GET /api/jobs/{id}`：持久资料/任务状态，区分受理、解析、索引、核验及失败；不暴露存储路径或引擎空间。
- `GET /api/documents/{id}/original`、`GET /api/documents/{id}/blocks`：私有下载和实际解析位置，校验归属与维护状态；磁盘/数据库读取后重新校验。原文下载校验哈希并以附件响应。
- `POST /api/jobs/{id}/retry`、`POST /api/knowledge-bases/{id}/rebuild`（后者接收 `client_request_id`）：显式修复，过期任务不得修改已切换空间。重建核验后切换数据库 active_workspace，旧空间以 cleanup_pending 隔离保留。
- `POST /api/documents/{id}/delete`、`POST /api/documents/{id}/replacement`：受管删除/替换任务；`POST /api/jobs/{id}/cleanup` 重试旧空间清理。旧空间、原文和解析数据清理核验成功前库不恢复就绪。

## 受管解析与引擎边界

原文固定在私有 `.local/runtime/sources/`，随机存储键与 SHA-256；单文件 20 MiB。UTF-8 TXT/MD 使用实际行号，文字 PDF 最多 100 页并保留实际页码，DOCX 限普通段落/简单表格行。解析在 20 秒/384 MiB 子进程中运行，输出最多 500 万字符/5 万块；拒绝扫描/加密、乱码、危险 ZIP、合并表格、嵌入对象、字段及非空页眉页脚等可能丢失正文的结构。新增依赖锁定 pypdf 6.19.0、python-docx 1.2.0、python-multipart 0.0.32。

同库仅一个活动任务、全安装串行修改引擎；进入修改前提交 maintaining/revision。预检失败保留原 ready，修改后失败或中断保持 blocked。只有核对引擎文档状态、正文、片段和检索来源后才 ready；重启不自动重放不确定引擎写入。删除/替换在新空间核验并切换后，清理旧 LightRAG 存储和原文；清理失败保持 blocked，可显式重试或重建。SSE 只流经核验的保存结果，真实模型端到端仍未交付。

## 成套备份与恢复

当前没有数据库迁移新增，旧业务数据不被自动清空或认领。实际环境启用前，先停写，分别备份业务库、引擎库、私有 `.local/runtime/` 和配置，再校验备份可读；恢复使用**两个全新空库和空私有目录**。离线工具 `python -m app.maintenance.backup backup|verify|restore` 在 API owner 锁释放后操作，需从私密环境提供两库连接信息及 PostgreSQL 工具目录，不在命令行或文档写连接串。恢复会将受管库保持 `blocked`：先比对快照后的删除记录和原文，再显式重建或完成清理，不能直接开放旧空间问答。自动备份仅在显式开启时按日执行，停写门禁等待活动请求与任务完成；失败显示 `unavailable`，不把旧快照标为当天成功。备份同日已成功时不会再取新快照，升级前应使用独立离线备份目录。私有本机目录无法防护可访问同一 OS 账户的人员，异地副本和真正的灾难恢复演练仍需另行落实。

提交结果不确定的原文先保留；启动持有 owner 后按数据库引用清理超过 24 小时的孤立 `.source/.partial`。保留已提交、近期和非受管文件。Windows 已验证文件刷新/原子重命名，不声称目录 fsync 或隔离同一 OS 用户。

旧 `/api/auth/*`、`/api/me`、`/api/admin/*` 及管理员初始化 CLI 不再属于有效入口。错误使用 `{detail:{code,message}}`：403 本地来源边界拒绝，404 不存在或不属于本地归属，409 状态冲突，422 参数不合规，503 数据库未配置、持久层或 owner 不可用。未配置业务库的 code 为 `database_not_configured`；连接故障不能伪报成功，也不返回连接信息。

## 验证

```powershell
uv run --no-env-file ruff check .
uv run --no-env-file python -m compileall -q app migrations
uv run --no-env-file alembic upgrade head --sql
```

GitHub CI 使用独立临时 PostgreSQL 17.9，验证新库迁移、重复迁移、真实 API 响应，以及公开的路由/原文定位回归测试，详见 [CI 说明](../docs/development/CI.md)。上述 `--sql` 命令只生成迁移 SQL，不连接数据库；实际数据库行为由 CI 冒烟或本地集成验证确认。

选定的八个 `backend/tests/` 文件现随仓库发布，CI 全部运行；其他专用测试仍在维护者本机。公开测试命令以 [CI 工作流](../.github/workflows/ci.yml) 为准；未设置 `CITERAG_TEST_DATABASE_URL` 时 PostgreSQL 用例明确跳过，不用 SQLite 或内存库替代。独立测试实例准备与完整本机测试方式见[本地开发的运行检查](../docs/development/LOCAL-DEVELOPMENT.md#运行检查)。禁止把真实业务库或共享实例作为测试目标。

M1-1 历史测试覆盖建库幂等/容量/事务；M1-2 增加受限解析、真实隔离 PostgreSQL 生命周期、SDK＋隔离引擎库＋本地模型替身和真实浏览器。三者不等于真实供应商验证；每轮结果见 [M1 验证记录](../docs/development/M1-VALIDATION.md)。

LightRAG 为锁定提交的可选 `rag` 依赖，`uv sync --locked --extra rag` 才安装。安装成功不代表四类 PG 存储、双库真实检索或模型验证通过。当前行为结果与缺项见 [M0 验证记录](../docs/development/M0-VALIDATION.md)，旧账号方案计数只作历史证据。
