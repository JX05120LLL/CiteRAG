# M0 验证记录

## 最新 LightRAG 真实联调（2026-09-24）

用户明确授权本次最多 40 次百炼真实请求后，运行受控 `rag verify --real`，实际退出码 **0**。本次仅发送两份固定的合成 A/B 质保资料及 LightRAG 生成的受限提示词；实际预留 **21 次**模型请求，报告中的 21 个请求追踪标识互异。报告显示真实使用 `qwen-plus`、`text-embedding-v4`、`qwen3-rerank`，Embedding 维度 1024，独立引擎库 PostgreSQL 17 / pgvector 0.8.1，两套 workspace 的五类关键存储行共 **10 项**；来源隔离、删除、重开、清理四项验收标志均为 true。当前配置匹配的本机 `rag.json` 结果为 `available`，只读 API 实测 `database=available`、`models=available`、`rag=available`。核对仅在内存中对请求 ID 计数和去重，终端、文档和聊天只输出模型名、数量、布尔结果与版本，未输出任何 ID、请求/响应正文或凭证明文。两库数据已由验证器按生成的 workspace 清理；报告保存在 Git 忽略的本机目录。本次未重跑五模型连通或 Embedding 边界请求。

LightRAG 的固定 SDK、真实百炼适配与独立 PostgreSQL 已完成 M0 双库验收；**暂缓的 Embedding 单条超长输入边界仍未通过，因此 M0 整体未完成，M1 未开始**。此次模型费用以百炼账单为准，报告中的请求数不是人民币金额。

提交前审查发现 `ProviderError` 的异常字符串此前包含请求 ID，上游 SDK 记录异常时可能将其写入日志。已从异常字符串移除该值，仍保留对象字段供内存中的验证逻辑使用；合成 401/403/429/400/502 响应的离线用例先红后绿，确认异常字符串与 `repr` 不含合成请求 ID。此修复不改变上面的真实请求结果，也没有重新调用供应商。

## Embedding 边界复核（此前快照，2026-09-24）

用户自行运行 `models boundary --real`，只发出一次 `text-embedding-v4` 边界请求。终端的**脱敏分类**为 `unexpected_error`、供应商类别 `request`、HTTP 400、请求 ID 存在、`top_level`、`invalid_parameter`、`input_length_generic`、`reserved_requests=1/1`。`reserved_requests` 是请求预算计数，不是 Token 计数；这里没有保存原始响应正文或供应商 Token 数。[百炼官方接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/)规定单条上限为 8192 Token，[错误码说明](https://help.aliyun.com/zh/model-studio/error-code)描述了更具体的长度范围错误；本次只有泛化分类。可确认供应商以输入长度相关的参数错误拒绝了该请求，**不能确认拒绝的是单条超过 8192 Token**，也不能把普通 400 当作边界通过。用户已查看百炼审计页，未发现更详细的错误原因；该边界复核时助手未发真实模型请求。

用户现决定**暂时搁置**这一边界验收，先继续 LightRAG 验证。该项仍为未通过，五模型成功证据仍有效；搁置不等于从 M0 验收条件中删除，也不触发新的边界请求。

短文本 Embedding 的真实调用和实测 **1024 维向量**是已有成功证据，与本次超长输入边界失败是两项不同结论。独立边界命令能运行到 `reserved_requests=1/1`，说明它当时已通过当前五模型报告的前置检查；该失败分支不写入边界通过，也不覆盖五模型成功报告。边界复核当时只读 API 为 `database=available`、`models=available`、`rag=unverified`，尚无 RAG 通过报告；最新状态见本文件顶部。`models=available` 表示五模型连通证据可用，**不表示 Embedding 输入上限或整个 M0 已验收**。

现有适配器只在 Embedding 的 HTTP 400 明确返回输入范围 `[1, 8192]` 且请求 ID 有效时归类 `input_limit`；本次泛化提示正确保持 `request` 与边界未通过。补充了一项完全按上述**脱敏字段组合构造的模拟 HTTP 响应**，覆盖一次请求、非零退出、五模型报告保留、无边界结果和终端脱敏；测试样本不是真实供应商响应的重放。无需为了这次诊断再次调用五个已验证模型。边界失败的实际请求 ID 只在当次进程内使用，CLI 未输出或写入模型报告，不能从本地报告反查；[百炼审计日志](https://help.aliyun.com/zh/model-studio/model-telemetry)可能显示请求 ID 和错误信息（如有）。若控制台能按该次调用的时间、模型或其显示的请求追踪信息定位，用户可在百炼私有工单中询问究竟触发单条 Token 上限、批次上限还是其他长度校验。**不要把请求 ID 或响应正文发送到本项目聊天或文档**。若仍得不到具体阈值证据，边界继续保持未通过；任何新请求都要另行授权。

边界复核当时的后端离线全套为 **229 passed / 20 skipped**，其中新增一项与用户脱敏分类一致的端到端回归；Ruff、公开文件与链接检查（100 files）、`git diff --check` 通过。这是随后 LightRAG 集成测试之前的快照；最新测试数和分层证据见下节。跳过项和模拟响应不替代真实供应商联调。**M0 未完成，M1 未开始。**

## LightRAG 付费前接线验证（2026-09-24）

在独立的 `127.0.0.1:55433` 引擎 PostgreSQL 上，显式 opt-in 测试使用固定提交的 LightRAG SDK 和**本地确定性替身** LLM、Embedding、重排：两套随机 workspace 实际入库，查询返回各自来源和资料标记，跨库查询未返回外库来源；直接核对 KV、向量、图节点、文档状态的非零行；重排回调至少两次；删除 A 后关闭重开，B 仍可检索而 A 不可见；最终只清理该次生成的两套 workspace 和目录。新双库测试也获取真实 `RagVerificationOwner` 锁，避免与付费验证并行写引擎库。另一个真实 PG 测试重验了两套空存储初始化/清理。两项 opt-in PG 测试 **2 passed**；测试自身未连接百炼、未写 `rag.json`，当时 API 的 `rag` 保持 `unverified`。这些结果验证真实存储和应用接线，**不能替代随后单独执行的真实百炼验收**。

首次替身模型测试先复现了实际接线缺陷：每库只有一个候选 chunk，LightRAG 请求 `top_n=3`，而供应商适配器要求 `top_n` 不超过候选数；上游回退导致重排未触发，验证器报 `retrieval/rerank_missing`。已在 `backend/app/rag/runtime.py` 的桥接层把请求值限制为实际候选数，供应商适配器原有参数校验不变。新增离线回归先红后绿，随后相同的真实 PG＋替身模型用例通过；失败测试留下的两套临时 workspace 已按各自随机 UUID 定向清理并确认无残留。另补纯离线 CLI 成功路径，使用合成预算、请求追踪和用量验证 `rag` 报告写入/读取及终端脱敏，不接触真实服务。完整离线后端 **231 passed / 21 skipped**，Ruff 与公开仓库检查（100 files）通过。21 项跳过包含需要显式隔离数据库环境的用例，不可与上述 2 项 opt-in PG 结果相加为全套一次运行。

上述付费前检查发现并修复重排候选数量问题；用户随后授权最多 40 次真实请求，真实执行结果及当前 API 状态见本文件顶部。该授权已用于本次验证，不视为以后请求的持续授权。**Embedding 边界未通过，M0 未完成，M1 未开始。**

## 前次 Embedding 边界修复与本机状态（历史快照，2026-09-23）

本节保留前次处理时的原始记录；其中“当前”“现为”和测试数量仅指该历史时点，最新状态以本文件顶部 2026-09-24 复核为准。

用户随后运行 `models verify --real --max-requests 6`，终端仅显示 `步骤=embedding_boundary`、`reserved_requests=6/6` 和旧报告失效。`reserved_requests` 是预留请求数；旧实现把第六次被接受、非预期供应商错误、缺少请求 ID 都折叠为相同的 `ValueError`。**没有保存足以区分这些情况的响应证据，不能追认第六次的实际结果或声明超限通过。**本轮没有发送新的真实模型请求，也没有运行真实双库验证。

[百炼同步 Embedding API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/)规定 `text-embedding-v4` 单条输入上限为 **8192 Token**，模型 Tokenizer 的计数不能用字符数替代；[模型规格](https://help.aliyun.com/zh/model-studio/embedding)列出的北京单批次 33000 Token 上限也不是单条上限。旧探针的 8193 个汉字不能证明超限。修复后使用固定、非私人、不同的数字单元构造候选探测输入；由于官方未提供该模型的精确本地 Tokenizer，**仍只以供应商明确返回 `[1, 8192]` 输入长度范围、HTTP 400 和唯一请求 ID 同时成立作为通过证据**。接受输入、不同上限、其他错误、缺少 ID 都分别输出不含请求/响应正文或 ID 的诊断并保持边界未通过；普通 400 不算超限。后续真实调用必须另获授权，命令见[本地开发](LOCAL-DEVELOPMENT.md#模型服务准备2026-09-22-确认)。

旧五模型成功报告的严格 schema、当时的配置指纹、五个角色、五组数字用量和实测 1024 维均匹配；五个 ID 互异，形状也与请求 ID 相符。然而旧适配器可能把聊天成功响应的对象 `id` 或错误正文 `id` 当作请求 ID，报告没有保存 ID 来源，无法从归档严格追认五个请求追踪 ID。曾尝试仅在本机离线恢复，独立复核后已撤回当前报告并归档，保留历史证据；因此本机有两份旧格式归档（原件及临时恢复副本），**不代表两次独立验证**，没有当前 `models.json`。模型验证指纹的证据版本已升为 v2，使旧报告即使被误放回也不能匹配新版状态 API。不补造来源或边界结果。只读 API 现为 `database=available`、`models=unverified`、`rag=unverified`。本轮没有重跑浏览器或前端测试，也没有发出供应商请求；本轮检查结果见下文最新验证段落。

适配器现按端点解释 ID：聊天成功正文 `id` 是 completion 对象 ID，不算请求 ID；Embedding/重排成功正文 `id` 可按官方接口语义使用；错误响应只认响应头或明确的 `request_id`。旧报告缺来源元数据，不能用 UUID 形状代替原始证据。新的五模型验收必须由修复后的适配器重新产生当前报告，之后才能执行单请求边界命令；目前其离线预检应拒绝缺少当前报告。**M0 仍未通过，M1 未开始。**

本轮最新离线检查：后端全套 `pytest -q` **221 passed / 20 skipped**，边界、供应商适配和报告专项 **92 passed**；Ruff、Python 编译、公开文件静态检查 **100 files** 与 `git diff --check` 均通过。20 个跳过项仍需独立数据库等显式条件；以上模拟响应不等于新的供应商验证。只读 `127.0.0.1:8000/api/status` 实测为 `database=available`、`models=unverified`、`rag=unverified`。本轮没有真实模型请求、真实双库验证、前端浏览器重验、提交、推送或部署。

## 前轮执行快照（截至 2026-09-22，保留历史证据）

**M0 未通过，M1 未开始。** 当时本机九项 M0 计划的第 1–6 项已有实现与分层证据。第 7 项双库验证器已完成离线测试，且在独立引擎 PostgreSQL 17.8/pgvector 0.8.1 上实际初始化并清理两套 LightRAG PG 存储；**尚未执行会产生模型费用的双库入库/检索**。第 8 项四态状态 API 和页面已在真实浏览器核对。第 9 项审计继续进行；Embedding 超长输入边界也未真实核验。

- 后端 `pytest -q` **205 passed / 20 skipped**，Ruff、编译和离线 Alembic SQL 生成通过；前端 **36 passed**、typecheck/build 通过；本机辅助脚本 **25 passed / 1 skipped**；公开文件静态检查 **100 files** 通过。跳过项主要是需显式提供隔离 PostgreSQL 连接的测试；专用测试与脚本保留本机，不参与公开 CI。模拟供应商响应只证明适配器行为，真实模型能力由下一条独立报告证明。
- 用户在本机完成百炼 v3 元数据升级后，实机重跑 `uv run --no-env-file python -m app.cli models verify`：退出码 0，显示北京地域、五个固定模型、Embedding 目标 1024 维和“未发送供应商请求”。CLI 专项测试 **17 passed**。该结果只证明配置可加载，不证明模型权限或真实调用成功。
- 第一次获批最多 6 次的百炼真实验证返回非零，旧诊断未保存失败步骤或预留请求数，没有模型可用报告，不能推断哪项成功。补充脱敏步骤/计数诊断后，用户另行授权最多 5 次请求；第二次 `models verify --real --max-requests 5 --confirm RUN` 退出码 **0**，当时产生五项模型响应、五组用量和 Embedding 实测 1024 维。后续第六次边界探测失败并归档该报告；本轮复核发现其中 ID 来源不可追认，因此当前模型状态保持 `unverified`。两次旧授权均已用完，超长输入边界仍无真实结果。
- 第 7 项：固定 A/B 质保文本、不同 UUID/workspace/source key、来源一致性、跨库否定、删除、关闭重启、按生成 workspace 清理及失败分类已有 **19 项离线测试**；另在 **55433 独立真实库**运行 `pytest -q tests/test_rag_real.py -m postgres`，**1 passed / 19 deselected**，实际初始化 PGKV、PGVector、PGTableGraph、PGDocStatus 两套空间，清理后无供应商调用。该项只证明空存储初始化/清理，不证明入库、来源或检索隔离。真实 `rag verify --real` 尚未获费用授权，`rag.json` 未生成。
- 第 8 项：前轮重启项目 API 后，`127.0.0.1:5174/api/status` 当时返回业务库 `available`、模型 `available`、RAG `unverified`，并列出 PostgreSQL 17 / pgvector 0.8.1 与模型验证时间；本轮当前状态已更新为模型 `unverified`，见上文。状态查询的引擎连通性复核是只读的；脱敏检查未发现 Key、Workspace ID、连接串、正文或工作目录字段。Playwright 在 1440×960 与 390×844 核对真实页面，无登录/管理员、无红色数据库错误，未开放的输入与操作保持禁用；390 宽无横向溢出。截图只在忽略目录 `output/playwright/m0-capabilities-status-1440.png`、`m0-capabilities-status-390.png`。
- 本轮没有提交、推送或部署。以下 2026-09-22 条目为历史检查，不覆盖本快照的阶段判断。

以下是当时 ROADMAP 五条未勾选项的历史核对；最新勾选状态以 [ROADMAP](ROADMAP.md) 为准：

| M0 项目 | 已有证据 | 尚缺证据 |
|---|---|---|
| 固定 LightRAG 与 PG 四存储 | 锁定提交、四类实际导入；55433 中两套空存储初始化/清理通过 | 实际入库后验证 KV、向量、图和状态数据的协同结果 |
| 两个 workspace 与目录隔离 | UUID 命名、路径和全局覆盖拒绝有离线测试；真实库两套空 workspace 可分别初始化 | A/B 实际资料互不检索、来源不交叉及清理后的负例 |
| 引擎生命周期与单 API owner | 真实业务库 owner 争锁/失锁历史检查；真实引擎空存储初始化、关闭与清理 | 带资料的引擎重开、删除恢复及 API owner 与引擎的同链路验证 |
| Embedding 与重排 | 历史调用实测 Embedding 1024 维、`qwen3-rerank` 单独成功；报告已保留归档 | 修复后五模型请求追踪证据及 Embedding 单条 8192 Token 上限的真实超限拒绝；真实 LightRAG 检索中重排调用与排序证据 |
| 模型、来源与失败路径 | 历史五项模型成功响应的配置指纹与用量匹配；适配错误和泄露防护有离线测试 | 新的完整五模型请求追踪报告；双库真实来源映射、删除/重启/失败恢复及脱敏报告 |

日期：2026-09-22。**M0 进行中，未通过整体验收。** 首版范围已由用户修正为本地单用户、免登录、自管知识库，见[本轮范围与计划](M0-LOCAL-SINGLE-USER.md)。旧账号测试与截图仅是历史证据，不能用于证明当前单用户实现通过。本文件区分实际检查、模拟验证与未完成的真实接入。

## 当前交接状态（2026-09-22）

- 初始提交 [`ed26168`](https://github.com/JX05120LLL/CiteRAG/commit/ed261687485a7de832fbd3383831fbe98ff6dc2c) 已推送至 `origin/main`。87 个远程文件的 blob 与本地提交逐一相符；初始公开历史未包含专用测试、辅助脚本、密钥或本机运行目录。测试源码仍保留在维护者本机。
- [GitHub CI #35708529819](https://github.com/JX05120LLL/CiteRAG/actions/runs/35708529819) 已实际成功，三个作业及其检查步骤均通过；不是仅在本机运行工作流命令。结果对应上述提交，不代替后续变更的检查。
- 本轮交接再次只读探测 API `8000` 与前端 `5174` 的 `/api/health`、`/api/status`、`/api/knowledge-bases`、`/api/conversations`，共 8 个请求均为 200。业务库 `available`，两个列表为空，引擎与模型仍为 `not_configured`。用户已确认工作台、知识库页和数据库可用状态；本轮没有重跑浏览器截图或全套测试。
- 当前 **M0 未通过、M1 未开始**。用户准备模型服务后，下一会话先实现受控配置加载/模型适配并完成真实引擎接入验证，再按 ROADMAP 判断能否进入 M1。准备内容、具体缺项与提示词见 [HANDOFF](HANDOFF.md)。

## 新版火山凭证录入兼容（2026-09-22）

- 用户确认已申请百炼华北2（北京）与火山新版 API Key，真实测试预算留待后续确认。本机仅检查到百炼加密文件存在，没有读取内容，不能认定为本次新 Key；火山尚待用户录入。
- 本机 `scripts/configure_models.ps1` 新增 `-AuthMode api-key`，火山新版仅隐藏输入 API Key；旧版 `app-token` 与省略参数的旧命令保持兼容。新保存记录为 v2，明确记录并规范化 `AuthMode`；旧 v1 文件不会因查看状态或运行脚本而自动迁移，替换仍要求用户明确输入 `REPLACE`。未改数据库、应用接口或依赖。
- `python -m unittest discover -s scripts/tests -p test_model_config.py -v`：**12 项通过**，全部使用临时目录与虚构凭证。覆盖新版隐藏输入、加密往返与 ACL、鉴权模式校验及大小写规范化、旧记录保护和显式替换，并回归原有失败/取消行为。新增功能和大小写反例均先复现失败，再修复通过。
- 文档同步后 `python scripts/check_repository.py` 检查 88 个公开文件通过，`git diff --check` 通过；`git check-ignore` 确认录入工具、测试及两平台凭证路径均被忽略。静态检查不等于完整安全审计或真实接入验证。
- 这些结果只证明本机录入工具行为。未读取真实凭证、未连接模型、未验证供应商权限或计费；模型加载器、供应商适配和真实接入仍待实现。工具及专用测试继续被 Git 忽略，当前修改未提交或推送。

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
3. **模型配置**：2026-09-22 用户确认采用 LiveRAG 默认模型选型：百炼 qwen-flash/qwen-plus/qwen-max、text-embedding-v4，语音阶段使用火山 bigmodel、MiniMax speech-02-turbo、本地 Silero VAD。百炼北京与火山新版 API Key 已申请，录入工具支持已补齐，但没有加载真实凭证或发起模型调用；模型权限、输入限额与测试预算仍待确认。重排及 VLM 在参考项目中未接入，CiteRAG 对应模型仍待确认，相关验收要求保留。不要把 Key 发到聊天或写入文档；以后仅通过受控后端配置加载，见 [申请清单](LOCAL-DEVELOPMENT.md#模型服务准备2026-09-22-确认)。
4. **模型适配及实测**：后续接入明确配置的供应商，实测 Embedding 维度（候选 1024）及输入上限、重排 index/relevance_score 协议、超时/限流/失败。候选参数不是已验证配置。
5. **真实双库隔离及来源**：两库写入同名/相似合法样例，核对独立 workspace 和工作目录，实际检索、修订、来源映射及失败恢复。不能用 UUID 字符串不同证明引擎检索不串库。
6. **运行环境**：Docker CLI 存在但 daemon 未启动；未固定或验收容器镜像组合。本轮不部署；语音 LiveKit 版本组合和真实媒体测试仍按 M2 执行。

M0 未通过前，不开放问答、文件入库、图片识别或语音能力。页面的禁用操作及空态不会生成演示答案、聊天历史或来源。
