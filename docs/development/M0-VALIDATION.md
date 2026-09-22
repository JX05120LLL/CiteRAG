# M0 验证记录

日期：2026-09-22。**M0 进行中，未通过整体验收。** 首版范围已由用户修正为本地单用户、免登录、自管知识库，见[本轮范围与计划](M0-LOCAL-SINGLE-USER.md)。旧账号测试与截图仅是历史证据，不能用于证明当前单用户实现通过。本文件区分实际检查、模拟验证与未完成的真实接入。

## 初始仓库同步范围（2026-09-22）

用户已授权筛选现有代码后提交并同步 GitHub。发布前复验：前端 26 项测试通过，类型检查与构建通过；后端在新建的独立 PostgreSQL 实例中 68 项通过、1 项跳过（可选 SDK 来源检查），ruff 通过。临时数据库验证后已停止，不影响当前工作台数据库。

仓库保留前后端源码、必要迁移、依赖锁文件、产品/架构/开发文档以及已确定的 UI 与 Logo 资产。用户随后要求集成 CI 且不提交测试文件；`backend/tests/`、前端专用测试文件、整个 `scripts/`、设计生成工具、`.local/`、参考仓库、运行数据、截图证据、日志、依赖安装目录和凭证文件保留在本机，不纳入公开提交历史。公开开发步骤已改为仓库内可执行的标准工具命令；本文后续提及的本机测试集、辅助脚本及输出仅作为历史验证证据，克隆仓库不包含这些文件。

模型选型与凭证录入工具的存在不代表真实模型接入；此次仓库同步不改变 M0 尚未完成的验收状态。下文中“尚未提交/仅本地暂存”描述的是此前开发时点。

## GitHub CI 配置验证（2026-09-22）

- 新增 [CI 工作流](../../.github/workflows/ci.yml)，推送、PR 与手动触发均运行。前后端与提交文件范围分别检查；无需真实凭证，不部署或调用模型。检查与测试文件边界见 [CI 说明](CI.md)。
- 本机已通过 `actionlint 1.7.12` 工作流语法检查、锁定依赖安装、前端类型检查与构建、后端 Ruff/编译/离线迁移 SQL 生成。直接提取工作流中的前端产物检查和后端 ASGI 冒烟代码执行通过，不使用另一份测试替代。
- 后端冒烟使用本机新建的独立 PostgreSQL 17.9 集群：新库与重复迁移、本地归属 ID 不变、两次应用启停、真实数据库列表与状态、来源边界、参数拒绝及未配置响应通过。临时实例验证后停止，未触及当前业务库。GitHub 的 Linux 容器环境须以 [Actions 实际运行结果](https://github.com/JX05120LLL/CiteRAG/actions/workflows/ci.yml) 为准，本机执行不替代远程 CI 证据。
- 专用测试源码不发布，CI 仅运行工作流内的冒烟断言，不宣称运行前端 26 项或后端 68 项本机测试集，也不作为 M0 完成依据。

## 本机持久业务库配置（2026-09-22）

用户要求配置实际验收环境。本次没有更改应用代码或依赖，在 `.local/runtime/` 新建持久 PostgreSQL 17.9 集群，仅监听 `127.0.0.1:55432`，未改动已有 5432 服务及其数据。应用使用独立非超级用户角色，随机凭证经 Windows DPAPI 当前用户保护，目录 ACL 仅限当前用户与 SYSTEM；初始化临时明文密码文件已删除，数据、凭证与日志均被 Git 忽略。

- 真实业务库迁移达到 `0002_local_single_user (head)`；`local_profiles` 恰好一条，知识库与聊天均为空。应用数据库角色没有超级用户、建库或建角色权限。
- API 8000 与前端 5174 代理的 `/api/health`、`/api/status`、`/api/knowledge-bases`、`/api/conversations` 均返回 200；业务库 `available`，两个列表 `items: []`，引擎和模型如实保持 `not_configured`。
- 允许的 5174 Origin 返回 200；未知 Origin、无 Origin 写入返回 403；向不存在的知识库创建聊天返回 404，业务库未写入测试资料。
- 本机启动脚本初次重复运行发现 Windows venv Python 启动器 PID 与监听子进程不同；已按已记录启动器和其 Python 子进程核验归属，重复运行成功。随后停止本轮创建的 API 和专用数据库，再由脚本恢复，内部本地归属 ID 保持不变。原有前端继续运行，未实际重启 Windows。
- Chrome / Playwright 访问真实前端/API/持久数据库，工作台显示“还没有知识库”“暂无聊天”，知识库页为空列表，系统状态显示数据库可用，无数据库红色报错。截图：`output/playwright/m0-persistent-workbench.png`、`output/playwright/m0-persistent-status.png`。
- 本机启动方式见 [本地开发指南](LOCAL-DEVELOPMENT.md)。本轮为环境配置与真实服务冒烟检查，未重跑前一节记录的全量自动测试，未调用模型、未添加演示知识库、未把该结果当作真实 RAG 接入验证。M0 仍待 pgvector/引擎四存储、双 workspace 检索隔离及真实模型和可核查来源验证。

## 本地单用户修订验证

2026-09-22 模型凭证录入入口：新增 `scripts/configure_models.ps1`，Windows PowerShell 5.1 隐藏输入，按平台使用 DPAPI 加密保存至忽略目录，支持仅检查存在状态。新增 8 项虚构凭证测试通过，覆盖加密往返、目录/文件 ACL、默认拒绝覆盖、空输入、显式替换、脚本隐藏输入与取消、junction 拒绝、被占用文件保存失败及语音元数据；脚本测试全集 **12 passed / 1 skipped**（旧 PostgreSQL runner 的符号链接权限测试跳过）。未使用真实 Key、未调用模型，保存入口不代表模型加载/适配已完成。初始化测试先因缺少脚本失败；过程中修复 Windows PowerShell 重复设置 ACL 的权限问题，最终使用只持久化修改段的 .NET ACL API 验证通过。

本轮范围修订已实现并验证：直接打开工作台，无需管理员初始化，保留本地请求边界，旧数据库升级保留数据且不自动认领。**这些基础通过，不表示 M0 的真实引擎/模型接入通过。**

| 检查 | 本轮结果 | 证据边界 |
|---|---|---|
| `python scripts/test_postgres.py -- -q` | **68 passed / 1 skipped** | 使用新建的隔离 PostgreSQL 17 集群；含真实迁移、API、归属、连接失败及 owner 检查，也含非 PG 单元测试。跳过可选 SDK 安装来源检查；本轮未重新安装可选 SDK。 |
| `uv run --no-env-file ruff check .` | 通过 | 后端代码检查。 |
| `pnpm test` | **26 passed** | 受控 HTTP 响应下的真实 DOM/客户端测试，覆盖免登录、错误重试、两种列表分别失败、状态与 HTML 注入边界；不作为真实数据库证明。 |
| `pnpm typecheck` / `pnpm build` | 通过 | 原生 TS 类型与 Vite 构建。 |
| `python scripts/check_repository.py` / `git diff --check` | 通过 | 99 个公开文件的链接、SVG、设计 PNG 尺寸和有限凭证特征检查；不等同完整安全审计。 |
| Chrome + Playwright：真实 Vite/API/隔离 PG | 通过 | 空库从 `0001` 升级到 `0002_local_single_user` 后，无账号进入工作台、我的知识库和状态页；不是模拟数据库响应。 |
| Chrome + Playwright：真实未配置 API | 通过 | 无登录表单，明确显示业务库未配置，管理/状态导航仍可达；列表 503 属于预期。 |
| 实际 HTTP 边界探测 | 通过 | 非允许 Host、Origin、代理来源头返回 403；旧 `/api/me`、`/api/admin/users` 返回 404；无登录 Cookie。 |

- 测试先红再绿：旧账号实现不满足本地入口与请求边界；追加迁移不存在时集成测试失败。真实业务连接拒绝曾返回 500，已修正为脱敏 503。独立审查发现聊天失败会遮住成功读取的知识库，已拆分错误状态并补双向混合失败与重试测试，复核通过。
- `0001_m0_accounts` 原样保留；`0002_local_single_user` 建立单例 `local_profiles`。旧账号/会话/资料保留，未归属记录不对当前工作区可见；有本地资料时拒绝回退迁移。
- 本轮没有新依赖。移除已无使用需求的 `argon2-cffi` 密码依赖并更新 `uv.lock`，固定 LightRAG 提交保持不变。
- 1440×960 工作台实测顶栏 72 px、侧栏 232 px；管理页保留 A 稿灰底与圆角白面板，正式 Logo 未改。390×844 状态页无横向溢出；表单及密码输入均为 0。
- 本轮截图均为真实 API：`output/playwright/m0-local-workbench-real-1440.png`、`m0-local-knowledge-real-1440.png`、`m0-local-status-real-390.png`、`m0-local-unconfigured-real-1440.png`。前 3 张使用真实隔离业务库，最后一张未配置数据库；均未调用模型、未写入演示知识库。
- 验证临时使用 Vite 5175 → API 8001；隔离 PostgreSQL 随机回环端口。临时实例验证后停止，没有操作用户已有的 8000/5174 进程或 5432 PostgreSQL 服务，没有读取真实凭证。
- PRD、架构、ROADMAP、实施/开发说明与设计说明已按本地单用户修订。当前改动留在工作区，原 41 项暂存记录未改动；未提交、推送或部署。

以下“基线”到“固定版本与交付状态”记录的是此前单企业账号方案，保留便于追溯。当前有效范围与验收以本节及 ROADMAP 为准。

## 基线

- 工作目录 `D:\code\CiteRAG`。开始时 41 个文件暂存、0 次提交；本轮保留原暂存区，不提交、推送、部署。
- 已依次阅读 AGENTS、README、PRODUCT、文档导航、PRD、架构、ROADMAP、DESIGN-SPEC，并查看预览器和四页 PNG。
- 历史参考只读：LiveRAG `208eede49e76cbb00ed85ee644576bd2520ce8f6`；LightRAG `59af311307c7417b342f44850b097648d47e83bd`（1.5.8 / API 0347）。没有复制整仓、私人资料或凭证。
- 此前按单企业账号方案实施；2026-09-22 用户纠正为本地单用户，登录与管理员部分已不属于首版。本轮同步修订 PRD、架构与 ROADMAP。

## 检查分类

| 检查 | 类型 | 当前证据 / 边界 |
|---|---|---|
| 文档、链接、SVG、四页 PNG、凭证特征检查 | 实际静态检查 | 初始 41 文件通过；新增工程后 `python scripts/check_repository.py` 及 `git diff --check` 复检通过；不等于完整安全审计 |
| 每库目录、重复初始化、失败清理、失去 owner 门禁 | 应用契约，SDK 测试替身 | 测试通过；不是实际 PG 引擎初始化或检索隔离 |
| 临时 PostgreSQL 启动、建库、停止 | 真实本机 PostgreSQL 17.9 | `python scripts/test_postgres.py --check-only` 已通过；现有 Windows 数据库服务未改动 |
| 账号、迁移、Cookie/CSRF、所有者及单 API 锁 | 真实 PostgreSQL + 应用 API | 最终 `python scripts/test_postgres.py -- -q`：后端全套 **40 passed / 1 skipped**；跳过项仅为基础环境未装可选 SDK 的来源检查，已在单独安装环境补验。包含真实 PG 测试与非 PG 单元测试，不是 40 项全部调用数据库 |
| 固定 SDK 与 PG 四存储类导入 | 真实安装 / 导入，未连接服务 | `uv run --project backend --isolated --locked --extra rag --no-env-file ...` 校验 direct_url 提交及四种类导入通过；同环境 `pytest backend/tests/test_rag_sdk.py -q` **5 passed**。工厂参数与配置门禁测试中使用替身，导入成功不等于存储可用 |
| 页面状态、API 错误、前端类型与构建 | 单元 / 构建 | `pnpm test` **25 passed**；`pnpm typecheck`、`pnpm build` 通过；官方 registry `pnpm audit` 未报告已知漏洞 |
| 后端代码与迁移 | 实际静态 / 编译 | `ruff check .`、`compileall`、`alembic upgrade head --sql` 通过；真实 PG 集成测试也执行了迁移 |
| 测试集群脚本自身 | 实际子进程 / 路径测试 | `python -m unittest discover -s scripts/tests -v`：**4 passed / 1 skipped**；Windows 账号未获创建符号链接权限，该特定路径反例未实跑 |
| 浏览器与 A 版对照 | Chrome / Playwright | 1440 × 960 桌面及 390 × 844 窄屏已核对；角色响应模拟与真实 API 未配置状态分开，下文列证据 |

## 实际行为与浏览器证据

- PostgreSQL **17.9** 在项目忽略目录中新建隔离测试集群，真实建表、账号/令牌持久化、登录退出、临时改密、重置/停用会话撤销、管理员越权拒绝和私人聊天 owner 检查通过。未用 SQLite/内存数据库替代。
- 真实 PG 并发测试复现并修复双管理员交叉停用的锁顺序冲突；非 ASCII CSRF 请求头原先触发异常，补校验后返回 403。
- 单 owner 争锁、关闭释放、连接终止回调通过；独立 Uvicorn 进程在其 owner 数据库连接被终止后实际退出。测试不把回调事件等同于进程退出。
- 已配置但缺迁移表、空迁移版本或旧版本的库启动被拒绝，未自动升级，失败释放 owner；未配置数据库仍可启动受限页面/API。
- Chrome 访问本机 Vite 与真实 FastAPI，确认无数据库时显示“服务尚未就绪”，没有伪造身份、资料、历史或模型输出。该场景 `/api/me` 的 503 是预期失败状态。
- 管理员、普通用户空态及系统状态的浏览器检查使用明确的 HTTP 测试响应，**不是浏览器到真实 PostgreSQL 的登录闭环验收**。角色显示与后台授权真实 PG 测试分别验证。
- 工作台使用 72 px 顶栏、232 px 侧栏及原定底部输入区。管理页面按 A 稿复核后改为灰色外背景、坐标约 (48, 98) 的圆角白色面板、248 px 团队知识库侧栏；空态不复制演示资料。正式 Logo 未更改。
- 390 px 页面没有横向溢出；普通用户没有管理导航；发送、语音、图片、上传等未开放入口保持禁用并说明原因。语音和图片页面仍以既有静态设计为准，未实现后续阶段功能。
- 截图：`output/playwright/m0-unconfigured-1440.png`（真实未配置 API）、`m0-admin-workbench-mock-1440.png`、`m0-admin-knowledge-mock-1440.png`、`m0-admin-status-mock-390.png`、`m0-user-mock-390.png`（明确模拟响应）。截图为本机检查产物，未加入 Git。
- 原 5173 端口已有其他进程，本轮没有停止它；浏览器临时使用 5174 并显式允许对应本机 Origin。仓库默认开发端口仍为 5173。

## 固定版本与交付状态

本轮使用 Python 3.12.13、uv 0.11.3、Node 24.14.0、pnpm 10.33.0。前端直接依赖锁定 TypeScript 5.9.3、Vite 7.3.6、Vitest 4.1.11；esbuild 0.28.1 补丁覆盖的原因见前端说明。后端及 SDK 完整解析结果保存在 `backend/uv.lock`，前端为 `frontend/pnpm-lock.yaml`。

已有 41 项暂存记录保持原样；新增工程和本轮文档修订保留在工作区。0 次提交，未推送、未部署。配置和验证示例不包含真实凭证。独立代码审查指出的两处认证边界已修复并回归通过。

## 尚需完成的 M0 条件

1. **引擎数据库**：业务与引擎应分为 `assistant_app` / `assistant_rag` 或等价独立库、独立账号。本机 PostgreSQL 程序存在，但未安装服务器端 pgvector 0.8；不修改已有 PostgreSQL 安装或服务。本轮临时测试库只验证业务持久化与锁。
2. **PG 四类存储**：安装指定服务器扩展后，真实验证 PGKVStorage、PGVectorStorage、PGTableGraphStorage、PGDocStatusStorage 的初始化、写入、检索、删除和关闭；单纯导入 SDK 不满足这一项。
3. **模型配置**：2026-09-22 用户确认采用 LiveRAG 默认模型选型并准备申请 Key：百炼 qwen-flash/qwen-plus/qwen-max、text-embedding-v4，语音阶段使用火山 bigmodel、MiniMax speech-02-turbo、本地 Silero VAD。此次仅同步文档，未加载凭证或发起真实模型调用；账号区域、模型权限、输入限额与测试预算待提供。重排及 VLM 在参考项目中未接入，CiteRAG 对应模型仍待确认，相关验收要求保留。不要把 Key 发到聊天或写入文档；以后仅通过受控后端配置加载，见 [申请清单](LOCAL-DEVELOPMENT.md#模型服务准备2026-09-22-确认)。
4. **模型适配及实测**：后续接入明确配置的供应商，实测 Embedding 维度（候选 1024）及输入上限、重排 index/relevance_score 协议、超时/限流/失败。候选参数不是已验证配置。
5. **真实双库隔离及来源**：两库写入同名/相似合法样例，核对独立 workspace 和工作目录，实际检索、修订、来源映射及失败恢复。不能用 UUID 字符串不同证明引擎检索不串库。
6. **运行环境**：Docker CLI 存在但 daemon 未启动；未固定或验收容器镜像组合。本轮不部署；语音 LiveKit 版本组合和真实媒体测试仍按 M2 执行。

M0 未通过前，不开放问答、文件入库、图片识别或语音能力。页面的禁用操作及空态不会生成演示答案、聊天历史或来源。
