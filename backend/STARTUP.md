# 启动与配置：首版文字工作台

日常启动不安装依赖、不迁移数据库、不启动 PostgreSQL，也不要求语音、Agent 或 MCP。先准备数据库与受控配置；API 和前端分别运行在 `127.0.0.1:8000`、`127.0.0.1:5173`。不要按端口停止来源不明的进程。

## 1. 首次安装依赖

需要 Python 3.12、uv、Node.js 22.12+、pnpm 10.33.0。在仓库根目录执行：

```powershell
cd backend
uv sync --locked --extra rag
cd ../frontend
pnpm install --frozen-lockfile
cd ..
```

`rag` 是知识库检索所需的可选依赖；纯界面预览可用 `uv sync --locked`。语音、Agent、MCP 的安装分别见对应说明。**日常启动直接使用已安装的 `.venv`**，避免普通 `uv run` 自动同步时移除先前安装的可选依赖。需要使用 uv 时加 `--no-sync --no-env-file`。

## 2. 数据库边界与准备

一个 PostgreSQL 服务可以同时承载两个逻辑数据库：

| 数据库 | 数据与配置 |
| --- | --- |
| `assistant_app` | 聊天、资料、任务及归属记录；应用角色须能访问当前 `0012_agent_runs` 业务表。 |
| `assistant_rag` | LightRAG 索引和向量；与业务库及角色分开配置，不能当成聊天库使用。 |

本机 Docker 场景使用已有 `citerag-rag-postgres` 容器，回环映射为 `55433:5432`。先在 Docker Desktop 启动它，也可在核对容器归属后执行：

```powershell
docker start citerag-rag-postgres
docker port citerag-rag-postgres
```

这不是创建容器的指令。新环境需要自行准备 PostgreSQL/pgvector、业务库和最小权限角色。**只有新建空业务库或已确认备份、停写与恢复方案的升级才能执行迁移**。启动器不会创建库、执行 Alembic、`stamp` 版本或导入旧测试记录。

业务连接支持显式 `CITERAG_DATABASE_URL`（`postgresql+asyncpg`、回环地址），优先于 Windows 受控文件。不要把 URL/密码写在命令参数、README、Git 或聊天中；由受控后端环境注入。Windows 本机入口也可加载已准备的 `.local/runtime/business-postgres/credential.xml`：DPAPI 加密、仅当前用户和 SYSTEM 可读，固定核对 `citerag-rag-postgres` / `127.0.0.1:55433` / `assistant_app` / `citerag_app`，不回退旧测试库。

新环境在受控后端已注入业务连接后，显式迁移命令是：

```powershell
cd backend
uv run --no-sync --no-env-file alembic upgrade head
```

不要在只想启动项目时运行这条命令。业务库备份必须与对应索引库、原文及图片成套保留；索引库无法恢复聊天或原文件。空业务库不能自动认领旧私有文件，先可逆封存，恢复时按成套数据方案处理。

## 3. 模型配置

Windows 首次配置百炼模型使用 [configure-model.ps1](configure-model.ps1)。它交互读取 SecureString，直接写现有格式 3 的 DPAPI 记录，不接受明文 Key 参数、不覆盖既有记录。`WorkspaceId` 必须填写百炼北京地域工作空间的真实 ID，供应商地址由它构成；它不是 API Key，也不是可自选的 LightRAG 索引空间名：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\configure-model.ps1 -WorkspaceId YOUR_DASHSCOPE_WORKSPACE_ID
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\configure-model.ps1 -CheckOnly
```

`-CheckOnly` 只检查文件元信息，不解密或联系供应商。已有模型记录无需重新配置；当前公开模型加载器依赖 Windows DPAPI，其他系统尚无完整的公开模型配置入口，可运行无模型 API/界面预览。字段与加载格式见 [模型配置](app/credentials.py)。知识库模式还需要独立的引擎数据库配置，见 [后端说明](README.md)。凭证不得进入前端、日志或 Git。

在未提升权限的当前用户 PowerShell 中完成首次配置。提升的管理员终端可能把新文件归属设为 Administrators，正式配置入口和加载器会拒绝此类记录，不会自动接管既有文件；不要放宽归属或 ACL 门禁。Windows CI 仅将合成 PowerShell 子进程的默认 `TokenOwner` 设为当前用户，仍使用真实 DPAPI 和 DACL，并校验新建文件归属；它不修改现有文件、用户身份、父进程或正式配置。

入库和问答默认关闭。只有已确认发送内容、费用及操作范围后，在**启动 API 的终端**显式启用：

```powershell
$env:CITERAG_ANSWER_ENABLED = 'true'
$env:CITERAG_INGESTION_ENABLED = 'true'
```

普通聊天只需要业务库和回答模型；知识库聊天还需要资料真实入库、当前库就绪及检索配置。没有这些条件时显示缺项或安全失败，不提供伪造来源。

## 4. 每次启动

仓库根目录打开两个 PowerShell 终端。先运行只读检查：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1 -CheckOnly
```

它连接业务库检查 schema、唯一归属和未完成入库任务，**不恢复任务、不清理文件、不调用模型**。模型文件存在仅代表文件存在，不代表密钥或模型能力可用。

终端一，普通文字工作台：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1
```

知识库文字工作台使用 `-Profile knowledge`，额外检查 `rag` 依赖及引擎配置文件。两种模式均关闭语音、Agent、MCP、天气和备份后台任务，保持单 API worker；不要用这个文字启动入口验收这些可选能力。

终端二：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1 -Role frontend
```

打开 [工作台](http://127.0.0.1:5173/)。窗口留开；修改环境变量后重启 API 才生效。API 的实际启动会执行已有的中断恢复、保留期限及孤儿清理，**正式启动不是只读操作**；已有索引配置时，还会在索引库创建并清除临时 schema/table 进行读写探测，这不是业务迁移或只读诊断。有待处理入库任务时预检拒绝，先审查任务与费用范围。

仅预览界面、尚未配置业务库时，在未配置供应商的新环境使用：

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.doctor --profile ui
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

无业务库时创建/发送聊天不可用。已有本机配置仍会被应用读取，这不是忽略配置的沙箱。**系统状态页对已配置能力的自动检测可能产生供应商费用**，独立于入库/问答开关；范围见 [主 README](../README.md#-快速开始)。没有费用授权时不要触发该检测。

## 5. 关闭与缺项定位

退出前等待当前文字回答完成（语音应先挂断），再分别在前端、API 窗口按 `Ctrl+C`。当前普通文字流没有独立的“停止回答”按钮；刷新/关闭页面造成断线后，重开并核对持久状态，再决定是否重试。只关闭这两个已知窗口，不停 Docker 或未知 PostgreSQL，不按端口批量杀进程。进程意外退出后旧回答会标记中断，重启读取历史，不自动重发模型请求。

| 诊断阶段/错误码 | 操作 |
| --- | --- |
| `dependencies` / `backend_dependencies` | 首次显式安装锁定依赖；不在每日启动里自动安装。 |
| `frontend-port` / `api_port` | 核对端口 PID 与项目窗口；换端口须同时修改前端代理，不停止未知服务。 |
| `business-configuration` / `database_not_configured` | 准备正式业务连接；不使用旧测试配置代替。 |
| `database_schema_mismatch` | 停写、成套备份并确认迁移；不关闭门禁或 stamp head。 |
| `database_owner_invalid` | 核对归属和恢复方案；不自动认领。 |
| `unfinished_ingestion` | 审查任务状态及付费授权；检查命令不会修改或重试它。 |
| `model_record` / `rag_record` | 准备对应受控配置；文件检查不验证供应商可用性。 |
| `api-startup` | 查当前启动窗口的安全错误；业务恢复及可选引擎检查仍可能失败。 |

需要进一步定位时，在 `backend/` 中运行 `python -m app.doctor --profile text --database --backend-only`（须已受控注入连接）；结果不会打印连接内容或凭证。诊断退出码 `0` 是本地检查完成、`1` 是缺项阻止、`2` 是尚未检查业务库，均不是模型质量或业务验收结论。
