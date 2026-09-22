# M0 本地开发

首版为本地单用户：直接进入工作台，自主管理知识库，无管理员、注册、登录或网页密码。当前提供页面壳、本地归属与聊天绑定基础、请求边界及引擎生命周期门禁；建库、问答、上传入库、语音与图片功能尚未开放。完整检查与真实接入缺项见 [M0 验证记录](M0-VALIDATION.md)。

## 本机已配置的验收环境（2026-09-22）

本节记录维护者现有工作站，不是克隆后的默认环境。`.local/` 与 `scripts/` 中的本机辅助工具不随仓库发布；新克隆用户跳到“先选择当前场景”，按后文通用开发与数据库初始化步骤操作，并将示例目录改为实际克隆路径。

当前 `D:\code\CiteRAG` 已建立持久的项目专用 PostgreSQL 17.9 实例，执行业务库迁移到 `0002_local_single_user`。前端使用 `http://127.0.0.1:5174`，API 使用 `127.0.0.1:8000`，专用数据库仅监听 `127.0.0.1:55432`；没有改动原有 5432 数据库服务。

**当前只需刷新前端页面验收，不要再执行下面的无数据库启动命令。** 重启电脑或服务停止后，在 PowerShell 执行本机启动脚本：

```powershell
powershell.exe -NoProfile -File D:\code\CiteRAG\.local\runtime\start.ps1
```

脚本检查并复用本项目实例，缺少时在后台启动，成功后返回页面地址；遇到不能确认归属的端口占用会停止操作。已实测重复运行及数据库/API 停止后的恢复。当前没有注册开机自启。

数据、诊断日志和本机脚本位于 Git 忽略的 `.local/runtime/`。新生成的数据库凭证使用 Windows DPAPI 当前用户保护，目录仅允许当前 Windows 用户和 SYSTEM 访问，启动时只向子进程注入环境变量。不要删除此目录、上传其内容，或复制凭证给其他 Windows 用户；本机配置不随 Git 分发，新机器仍需按后文初始化。

验收应看到：工作台“还没有知识库”与“暂无聊天”，系统状态“业务数据库：可用”。知识引擎和模型服务继续显示“尚未配置”，建库、上传与问答仍未开放；本次配置不代表 M0 整体通过。

## 先选择当前场景

本文按使用场景分段，不需要每次从头执行所有命令。前端和后端各运行一份即可；启动成功后终端持续显示日志、没有返回 `PS ...>` 提示符是正常现象，保留该窗口。

- **服务尚未启动**：执行“不配置数据库也能启动”一节，在两个窗口分别启动后端和前端。
- **服务已经运行，只想打开页面**：直接打开前端地址，默认是 `http://127.0.0.1:5173`，使用备用端口时是 `http://127.0.0.1:5174`；不要重复启动。
- **工作台显示数据库尚未配置**：跳到“初始化业务数据库”一节。先在原后端窗口按 `Ctrl+C`，等待返回 PowerShell 提示符；前端窗口保持运行。数据库配置、迁移和后端重启在同一个终端完成。

`http://127.0.0.1:8000` 是后端地址，没有网页首页；访问 `/` 或 `/favicon.ico` 返回 404 不表示后端启动失败。可打开 [API 状态](http://127.0.0.1:8000/api/status) 检查模式和数据库配置；能正常返回状态时，无需再运行一份后端。

## 环境与依赖

- Node.js ≥22.12，pnpm **10.33.0**；本轮使用 Node 24.14.0。
- Python **3.12**，uv；项目通过 `backend/.python-version` 选择版本。本轮使用 3.12.13。
- 业务库使用 PostgreSQL **17**；引擎数据库另需 pgvector **0.8** 系列扩展。Python 的 `pgvector` 包只是驱动适配，不能代替服务器扩展。
- 前端依赖为 TypeScript、Vite、Vitest、jsdom 和 Node 类型，用于构建、类型及 DOM 测试。没有运行时框架；媒体、Markdown 库等在对应功能需要时再引入。
- 后端采用 FastAPI/Pydantic/Uvicorn、SQLAlchemy/Alembic/asyncpg；pytest/httpx/ruff 用于检查。LightRAG 及其 Python PG 向量适配是可选 `rag` 安装组，固定到指定 Git 提交。锁文件不复制参考项目。打开工作台与初始化业务库不需要先配置模型或 pgvector。

## 模型服务准备（2026-09-22 确认）

模型选型按 LiveRAG 固定版本默认值执行，完整分工见 [架构模型基线](../多模态知识助手_技术选型与架构设计_v0.1.md#12-模型基线与配置)。目前是准备服务权限与凭证，真实模型适配和调用尚未完成。

| 申请顺序 | 平台与能力 | 模型及需准备的信息 |
|---|---|---|
| 先准备，供 M0/M1 | 阿里云百炼：文本与向量 | `qwen-plus`（LightRAG 引擎）、`text-embedding-v4`（目标 1024 维）、`qwen-flash`（最终回答）、`qwen-max`（会话摘要）；准备对应地域和空间的 API Key，核对模型访问权限 |
| M2 前准备 | 火山引擎豆包语音：流式识别 | LiveRAG 默认 `BigModelSTT` / `bigmodel`；该接法需要语音应用 App ID、Access Token，核对服务资源及鉴权形式 |
| M2 前准备 | MiniMax：语音合成 | `speech-02-turbo`；准备语音合成服务 API Key，并在接入时核对音色、账号区域与接口 |
| 无需云 Key | 本地 VAD | Silero VAD，语音阶段安装及验证 |

百炼可以用同一地域/空间下具有对应权限的 API Key 调用多个模型，无须按模型分别申请 Key。若沿用 LiveRAG 的默认百炼接入域名，选择华北2（北京）；已有其他地域账号时先记录地域，接入时匹配端点，不混用跨地域 Key。参见 [百炼 API Key 官方说明](https://help.aliyun.com/zh/model-studio/get-api-key) 和 [地域说明](https://help.aliyun.com/zh/model-studio/regions/)。只需申请模型 API 能力，知识库与检索仍由本地 CiteRAG/LightRAG 管理。

火山语音沿用参考项目的语音应用鉴权路线，开通的是流式语音识别服务；实际接入按账号的控制台与官方协议核对，参见 [语音流式识别说明](https://www.volcengine.com/docs/6561/1395846?lang=zh)。不要把普通文本模型服务的凭证当作语音凭证。

LiveRAG 本版未接入具体重排模型和 VLM。CiteRAG 原候选 `gte-rerank-v2` 与 Qwen3-VL 暂列待确认，当前不要求额外开通；重排验收仍是 M0 条件，图片仍按 M3 范围推进。

申请完成后只告知平台、地域、可用模型、服务是否开通及本轮测试预算上限。Key、Access Token 等凭证留在本机，不发到聊天、源码、文档或截图。当前后台仅接通业务数据库，模型适配器和从凭证存储加载模型的链路尚未完成，保存 Key 不会自动完成模型接入；不要复制历史 LiveRAG 的 `.env`。

### 维护者工作站：在本机录入模型凭证

以下命令仅适用于已有 `D:\code\CiteRAG\scripts\configure_models.ps1` 的维护者工作站。整个 `scripts/` 目录属于本机辅助工具，不随仓库发布；新克隆用户跳过本节，当前通用启动和业务数据库初始化均不需要模型凭证。

在上述工作站打开新的 PowerShell 窗口，执行百炼录入命令（不需要停止现有服务）：

```powershell
powershell.exe -NoProfile -File D:\code\CiteRAG\scripts\configure_models.ps1
```

看到“粘贴 API Key 后按回车”后输入 Key；输入隐藏，不会写入命令历史。默认地域为华北2北京 `cn-beijing`。其他地域在命令末尾添加对应的 `-Region` 参数，例如 `-Region ap-southeast-1`；脚本只记录地域，端点和账号可用性在接入阶段核对。

火山语音和 MiniMax 准备好后分别执行：

```powershell
powershell.exe -NoProfile -File D:\code\CiteRAG\scripts\configure_models.ps1 -Provider volcengine
powershell.exe -NoProfile -File D:\code\CiteRAG\scripts\configure_models.ps1 -Provider minimax
```

火山会先询问语音应用 App ID，再隐藏输入 Access Token。重复配置同一平台时，明确输入 `REPLACE` 才替换旧文件；直接回车取消，空 Key/Token 不保存。脚本使用 Windows PowerShell 5.1，请保留命令中的 `powershell.exe`，不要改用 `pwsh`。

凭证通过 Windows DPAPI 加密存储于 `.local/runtime/models/`，只供同一 Windows 用户在本机使用。目录与文件 ACL 仅允许当前用户和 SYSTEM，存储目录被 Git 忽略。脚本不会读取旧 Key、调用模型或重启服务；保存成功显示 `saved (not verified)`，表示已保存但未验证，网页“模型服务”状态仍不会自动变成可用。磁盘或替换失败时会保留可恢复的加密文件，不要公开这些文件。

只检查保存状态，不显示或解密 Key：

```powershell
powershell.exe -NoProfile -File D:\code\CiteRAG\scripts\configure_models.ps1 -Status
```

此本机脚本的测试使用临时项目与虚构凭证，不访问本机实际凭证；脚本与其测试均不属于公开仓库的检查入口。

## 不配置数据库也能启动

仅在服务尚未运行时，在两个 PowerShell 终端分别运行。以下第一段启动后端，第二段启动前端；每段运行一次：

```powershell
Set-Location D:\code\CiteRAG\backend
uv sync --locked
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

```powershell
Set-Location D:\code\CiteRAG\frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`，直接进入工作台。Vite 将 `/api` 代理到本机 8000；数据库未配置时页面显示明确状态，数据库相关列表返回可辨识的 503，不显示登录表单或模拟资料。`/api/health` 只表示进程存活，`/api/status` 显示本地模式及数据库、引擎、模型的分项状态。

启动前确认端口未被其他程序占用。服务仅用于本机回环地址；不要绑定 `0.0.0.0`、增加 API worker/副本或把本地模式公开到网络。API 拒绝 `Forwarded`/`X-Forwarded-*`；Uvicorn 禁用代理头处理，Vite 代理不添加此类头。

### 后端报 WinError 10048，8000 端口已被占用

如果报错包含 `attempting to bind on address ('127.0.0.1', 8000)` 和 `[WinError 10048]`，表示已有进程占用 8000，不是依赖安装失败。

先打开上面的 API 状态地址。如果返回 CiteRAG 的 `mode: local_single_user` 和数据库状态，说明之前的后端仍在运行，保留它并直接使用前端。只有需要更新配置或代码时，才在原后端窗口按 `Ctrl+C`，等待退出后重新启动。不要连续打开新窗口运行相同启动命令，也不要为了绕过冲突随意更换后端端口，前端仍代理到 8000。

如果无法确认占用者，仅查看监听进程，不停止未知程序：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess
```

### 5173 端口已被占用

出现 `Port 5173 is already in use` 时，保留未知占用进程，确认备用端口空闲。下面以 5174 为例；前端地址改变时，后端允许的 Origin 也要同步。

在原后端终端按 `Ctrl+C` 停止本项目 API，再执行：

```powershell
Set-Location D:\code\CiteRAG\backend
$env:CITERAG_ALLOWED_ORIGINS = 'http://127.0.0.1:5174'
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

在前端终端执行：

```powershell
Set-Location D:\code\CiteRAG\frontend
pnpm dev --port 5174
```

打开 `http://127.0.0.1:5174`，不要混用 `localhost`。前端仍代理到后端 8000，无需修改源码；环境变量仅作用于当前终端。这里没有登录 Cookie，不需要设置 `CITERAG_COOKIE_SECURE`。

如果健康接口显示 `database_configured: false`，可进入工作台查看状态；要使用持久数据，仍需按下一节准备业务数据库并迁移。进程启动成功不表示数据库和知识引擎已就绪。

## 初始化业务数据库

如果后端已经启动，先回到原后端窗口按 `Ctrl+C`，等待返回 `PS ...>`；前端继续运行。以下操作使用一个可输入命令的 PowerShell，最终也在该窗口启动后端，以便继承数据库环境变量。无需重新执行上面的无配置启动段。

数据库初始化与网页账号无关：现在不需要创建管理员或输入网页密码。仍须为 CiteRAG 准备独立 PostgreSQL 业务库及数据库连接凭证，不复用其他项目的数据。迁移只管理业务表，不初始化 LightRAG 引擎表；应用启动不自动迁移。配置数据库时会检查迁移版本，缺表、空版本或旧版本拒绝启动。

已有独立业务库时跳到环境配置。以下首次初始化会新建专用 PostgreSQL 角色 `citerag_app` 和数据库 `assistant_app`，不会操作其他库。若同名角色或库已存在，先核对用途，不删除或重置不明资源。这里的数据库角色不是产品中的账号。

在 PowerShell 打开本机 PostgreSQL 终端（程序安装位置不同时修改路径）：

```powershell
& 'D:\PostgreSQL\bin\psql.exe' -X -W -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

提示时输入安装 PostgreSQL 时设置的 `postgres` 密码。进入 `postgres=#` 后，先执行：

```sql
CREATE ROLE citerag_app LOGIN;
```

然后单独执行下面一行，按提示两次输入新的业务数据库密码；输入时不会显示密码：

```text
\password citerag_app
```

完成密码提示后再执行建库并退出；`\password` 和 `\q` 后不加分号：

```sql
CREATE DATABASE assistant_app OWNER citerag_app;
\q
```

出现 `PS ...>` 后才继续下面的 PowerShell 配置。上述密码都只在本机终端输入，不发送到聊天或写入文件。

进程环境变量：

| 变量 | 含义 |
|---|---|
| `CITERAG_DATABASE_URL` | `postgresql+asyncpg` 连接串，业务账号与业务数据库；作为敏感值处理 |
| `CITERAG_ALLOWED_ORIGINS` | 允许的精确回环浏览器 Origin，逗号分隔；默认含 `http://127.0.0.1:5173` 和 `http://localhost:5173` |
| `CITERAG_API_WORKERS` | 只能为 `1`，`WEB_CONCURRENCY` 同样不能大于 `1` |

连接串使用 `postgresql+asyncpg` 协议，必须包含主机和库名，数据库角色密码中的特殊字符按 URL 规则编码。应用不自动读取 `.env`，以免意外加载历史项目凭证；`uv run` 示例显式禁用 env 文件。密钥和连接串不放进源码、文档、聊天、URL 查询参数或命令行参数。

若按上面的默认主机、端口、角色和库名创建，可通过隐藏输入业务库密码构造连接串，不把密码写进命令历史。若使用已有独立数据库，应先将示例中的非敏感连接参数改为该库参数：

**执行前先核对浏览器端口**：下面代码使用默认前端 `5173`。如果当前打开的是 `http://127.0.0.1:5174`，先把代码中的 `CITERAG_ALLOWED_ORIGINS` 改为 `http://127.0.0.1:5174`，再执行整段。

```powershell
Set-Location D:\code\CiteRAG\backend
$citeragDbSecret = Read-Host '输入 citerag_app 的业务数据库密码' -AsSecureString
$citeragDbEncoded = [Uri]::EscapeDataString([System.Net.NetworkCredential]::new('', $citeragDbSecret).Password)
$env:CITERAG_DATABASE_URL = 'postgresql+asyncpg://citerag_app:{0}@127.0.0.1:5432/assistant_app' -f $citeragDbEncoded
Remove-Variable citeragDbSecret, citeragDbEncoded
$env:CITERAG_ALLOWED_ORIGINS = 'http://127.0.0.1:5173'
uv run --no-env-file alembic upgrade head
```

迁移成功后在**同一个终端**启动：

```powershell
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

追加迁移 `0002_local_single_user` 创建内部本地归属，保留旧账号、会话与业务记录，不自动认领旧知识库或聊天。已有 CiteRAG 数据库升级前先备份；不要运行 downgrade 或改写历史迁移。不同浏览器访问同一份本地资料，内部归属不是可登录账号。

重开 PowerShell 后需重新注入连接串和 Origin，无须重复创建数据库。迁移成功不等于 LightRAG 或模型就绪，工作台继续显示真实缺项。当前 API 不发登录 Cookie，来源校验与服务端归属检查仍然生效。完整契约见 [后端说明](../../backend/README.md)。

## 运行检查

### 新克隆可运行的检查

安装前后端依赖后执行：

```powershell
Set-Location D:\code\CiteRAG\backend
uv run --no-env-file ruff check .
uv run --no-env-file python -m compileall -q app migrations
uv run --no-env-file alembic upgrade head --sql
Set-Location D:\code\CiteRAG\frontend
pnpm typecheck
pnpm build
Set-Location D:\code\CiteRAG
git diff --check
```

`--sql` 仅生成迁移 SQL，不连接数据库。公开仓库通过 [GitHub CI](CI.md) 自动完成前端构建产物、独立临时 PostgreSQL 迁移和真实 API 冒烟检查，不依赖维护者工作站或真实模型凭证。CI 的检查直接写在 workflow 中，没有单独发布测试文件。

### 维护者本机的额外测试

以下命令仅适用于仍保留 `backend/tests/` 与前端专用测试文件的维护者工作站。这些文件被 Git 忽略、不随仓库发布；新克隆跳过本节，不把缺少测试文件当作安装失败。依赖配置保留 pytest/Vitest，供本机额外验证使用。

在 `backend/` 可执行 `uv run --no-env-file pytest -q`。未提供专用测试库时，真实 PostgreSQL 测试明确跳过；这不是数据库验收通过。要运行这些测试，先准备独立且可丢弃的 PostgreSQL 17 实例及空测试库，测试角色需能建表、创建 schema 和终止自己的连接。不要使用业务库、共享实例或正在运行 CiteRAG API 的实例。

测试会创建并清理随机 schema，还会迁移测试库的 `public` schema、启动测试 API 并终止测试 owner 连接；`public` schema 中的测试表不会自动清理。确认目标仅含可丢弃的测试数据后，在 `backend/` 终端通过隐藏输入注入完整的 `postgresql+asyncpg` 测试连接串：

```powershell
Set-Location D:\code\CiteRAG\backend
$citeragTestSecret = Read-Host '输入独立临时 PostgreSQL 测试库连接串' -AsSecureString
$env:CITERAG_TEST_DATABASE_URL = [System.Net.NetworkCredential]::new('', $citeragTestSecret).Password
Remove-Variable citeragTestSecret
try {
    uv run --no-env-file pytest -m postgres -q
} finally {
    Remove-Item Env:CITERAG_TEST_DATABASE_URL
}
```

该命令不安装 PostgreSQL 或服务器扩展，也不调用模型；测试实例由运行者准备并在验收结束后自行处理。本机辅助测试脚本不随仓库发布。

```powershell
Set-Location D:\code\CiteRAG\frontend
pnpm test
```

本地前端 DOM 测试使用显式测试响应，不证明真实模型、数据库或端到端业务就绪。历史测试计数仅记录当时的本机验证；专用测试源码不随仓库发布，GitHub CI 不运行此测试集。浏览器核对截图和流程类别见验证记录；旧账号方案测试属于历史记录，不作为当前单用户行为的验收证据。

## 引擎准备与门禁

需要检查固定 SDK 时，在 backend 运行 `uv sync --locked --extra rag`。这只安装依赖，不授权连接模型。`app/rag/sdk.py` 检查安装来源提交，显式选择 PG 四类存储与 1200/100 分块基线，并要求注入 LLM、Embedding、重排适配器。当前未把该工厂接成可用问答或入库接口。

API 拒绝非空 `POSTGRES_WORKSPACE`，并拒绝工作目录下的 `config.ini`，以防旧配置覆盖按库分配的空间；出于凭证保护，本轮采用“文件存在即拒绝”的更严格实现，不读取其内容。库 UUID 由服务端生成，工作目录不可由客户端指定。

配置业务库后 API 使用独立数据库连接持有 owner advisory lock；第二个实例不能启动。锁连接丢失后拒绝成功响应并退出进程；这不是自动主备切换。接管前确认旧进程结束。真实模型/pgvector/双库检索及可靠来源验证完成前，M0 保持未通过。
