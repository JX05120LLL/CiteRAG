# GitHub CI

2026-09-30 会话切片：公开合成回归加入 `test_conversation_modes.py`，覆盖普通聊天绕过知识库路由、同库摘要作用域／失效、语音／图片入口、失败重试、保留期及 `0010` 迁移／安全回滚。工作流白名单、Ruff 和 pytest 均同步更新；合并前在本机隔离 PostgreSQL 验证通过，合并后的远端 CI 须以提交 SHA 核对。不使用真实模型、私人资料或实际业务库。详见[会话改造记录](CONVERSATION-MODES-AND-MEMORY.md)。

2026-09-29 UI 细化：既有 CI 白名单已覆盖 `test_postgres_local.py` 中的 `0008` 到 `0009` 旧聊天保留用例，以及 `test_m13_answer_api.py` 中的归档/恢复接口用例；`alembic upgrade head` 冒烟会核对新的 `EXPECTED_SCHEMA_REVISION`。本机临时 PostgreSQL 两个文件 25 项通过；推送后的远端结果须按新提交 SHA 核对。详见 [UI 细化记录](UI-POLISH-2026-09-29.md)。

2026-09-28 M3 增量：工作流公开白名单增加合成图片存储及隔离 PostgreSQL 图片 API 测试；后端 Ruff/pytest 随之执行，`0008_image_attachments` 迁移 SQL 和新库 API 冒烟仍按工作流运行。前端 Vitest 增加图片上传/确认/删除边界。CI 没有模型密钥，不执行 Qwen3.8 Omni Flash、真人图片或实际业务库迁移。本轮尚未提交/推送，**没有对应远端 Actions 结果**。详情见 [M3 记录](M3-MULTIMODAL-VALIDATION.md)。

2026-09-27 提交补记：用户已授权提交并推送本次 M2 代码、公开回归和文档。下方“没有新远端结果”为提交前验证快照；推送后须按新提交 SHA 核对三个作业，不沿用旧615ec4d的成功结果。

M2 2026-09-27：白名单和后端命令新增 `test_m2_voice.py`，十个公开文件本机171项通过；覆盖租约/门禁/维护、最终转写去重与纠错、迟到取消、TTS文字降级、重启房间归属、实际 MP3 解码及显式启用后的受控语音凭证加载。前端221项，补配置/连接状态及下载不离开通话页面回归，lint新增voice控制器及API客户端。CI不连接真实ASR/TTS/模型、LiveKit服务或真人设备。本机另获授权的一次真实供应商验证不属于CI。基线615ec4d的远端CI #36310360483本轮重新核对成功；新代码未提交/推送，没有新远端结果。命令及分层边界见 [M2 交付](M2-VOICE-VALIDATION.md)和 [真实供应商记录](M2-VOICE-REAL-SUPPLIERS.md)。

2026-09-27 提交授权补记：用户确认 B 版 UI 升级完成并授权本地提交，未授权推送。下方“未 commit/push”是开发交付快照；本次提交尚无新远端 Actions 结果，不将本机通过写成远端 CI 通过。

2026-09-27 B 版 React 迁移：锁定 React / Ant Design / Ant Design X 和构建测试依赖；公开白名单增加 preview 与 react 的四个合成测试文件。原有 178 项加只读预览 23 项、React 11 项，共 **212 项 / 12 文件本机通过**。工作流运行 `pnpm lint`（React 产品与只读预览），资源门禁覆盖 `index.html`、`voice.html`、`ui-preview.html`。后端作业保持原隔离数据库和模型关闭规则，不使用私人资料、模型密钥或付费调用。正式 B 入口与官方语音布局已迁移，本轮 A/C 清理；未 commit/push，**新远端 CI 未运行**。浏览器矩阵和交互是本机证据，不属于当前远端 CI。见 [迁移与验证](REACT-UI-MIGRATION.md)。下方各日期保留历史范围。

2026-09-26 集中提交前检查：本次包含文字问答分流、上下文追问、基于原文的总结及事实支持检查，以及本地语音媒体与独立页面。只含暂存文件的独立副本按锁文件安装依赖；公开后端九文件 **145 passed、无跳过**，前端八文件 **178 passed**，类型检查、构建、Ruff、编译、仓库边界、两个 HTML 入口资源检查及原工作流迁移/API 冒烟均通过。数据库为独立临时 PostgreSQL，模型调用为 0，未迁移实际业务库。保留既有 LiveKit 包大小、TestClient 弃用和合成开发密钥长度警告。远端 CI 结果以本次提交对应的 Actions 运行为准；下方“未提交/未推送”描述为各片实施时快照，不代表完整 M0/M1/M2 验收。

2026-09-26 语音增量：公开测试白名单增加 `backend/tests/test_voice_transport.py`、`frontend/src/features/voice/controller.test.ts` 和 `frontend/src/features/voice/page.test.ts`，后端安装锁定的 `rag+voice` 依赖并执行九文件回归。新增测试使用合成配置检查 Token 权限、回环地址、资源门禁、迟到音轨、重复挂断及独立页上下文/清理/安全错误；前端同时构建 `index.html` 和 `voice.html`。CI 不启动 LiveKit 服务、不采集音频、不调用 ASR/TTS 或模型。本片未推送，远程结果尚未验证，详见 [M2-1](M2-LIVEKIT-TRANSPORT.md)。

2026-09-25 本次更新：在原有构建与临时 PostgreSQL/API 冒烟之外，公开选定的合成数据回归测试；前端执行 `pnpm test`，后端执行八个公开测试文件（名单见工作流）。后端按锁文件安装 `rag` 可选依赖，用于与 LightRAG 原文规范化规则核对；测试用 CI 专属临时库随机 schema，显式关闭入库和问答，不连接真实模型。其他维护者本机专用测试仍不入库。下方旧日期段落是当时的 CI 快照，以本节及实际 Actions 运行为当前范围。

2026-09-25 提交前验证记录：业务迁移增加 `0007_partial_answers`，公开 PostgreSQL 冒烟现在检查回答状态约束包含 `partial`；本机隔离 PostgreSQL 与静态检查已核对。提交前远程成功 CI 只对应已推送的 `bd4c75b`，不覆盖本轮改动；后续以 GitHub Actions 实况为准。CI 不运行专用本机测试、真实浏览器或真实模型验收。

提交后更新（2026-09-24）：代码提交 `a149976` 的 [CI #36007107240](https://github.com/JX05120LLL/CiteRAG/actions/runs/36007107240) 三个作业均成功，包括新状态字段的 PostgreSQL/API 冒烟。下方“尚未提交／无新远端结果”是验收时快照；CI 不验证本机专用测试、浏览器、真实模型或实际业务库迁移。本次文档校时提交的结果以其对应运行记录为准。

2026-09-24 本轮补记：公开基础冒烟现校验 `0006` 摘要表，以及无配置时的 `backup=disabled`、`retention=available` 状态；仍不跑专用 SSE、180 天清理或双库恢复测试，也不调用真实模型。本机对当前工作流执行独立临时 PostgreSQL 冒烟已通过。远程最近 CI #35994408491 成功仅覆盖旧 HEAD `d4c951a`，本轮代码未提交/推送，无新远程结果。实际业务库本轮不可达，旧 `0002` 只作历史核查。下方早期表述保留对应切片的当时范围。

M1-3 补记：公开工作流随 `0005_answer_attempts` 校验消息/回答表存在，并检查默认问答关闭返回 `answer_disabled`；仍只用临时 PostgreSQL 和合成资料，不调用真实模型。此前远端成功运行只覆盖 `fbafd86`；新增改动的运行结果应按对应提交核对，本机冒烟结果另见 [M1 验证记录](M1-VALIDATION.md)。

工作流位于 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)，在推送、Pull Request 和手动触发时运行。接手实查 `fbafd86` 对应的 [CI #35957495928](https://github.com/JX05120LLL/CiteRAG/actions/runs/35957495928) 三个作业均成功；该历史结果仅覆盖 M1-1，不覆盖后续 M1-2/M1-3 代码。其他运行见 [Actions](https://github.com/JX05120LLL/CiteRAG/actions)。

已核验的首次运行：提交 [`ed26168`](https://github.com/JX05120LLL/CiteRAG/commit/ed261687485a7de832fbd3383831fbe98ff6dc2c) 的 [CI #35708529819](https://github.com/JX05120LLL/CiteRAG/actions/runs/35708529819) **成功**，Repository boundaries、Frontend build and smoke、Backend and PostgreSQL smoke 三个作业均通过。此结论只对应该提交，后续提交以各自运行结果为准。

## 检查范围

| 部分 | 自动检查 |
|---|---|
| 提交文件范围 | 只允许列入白名单的公开合成测试；拒绝其余本机测试、辅助脚本、凭证配置及运行产物路径。仅检查路径，不等于完整密钥扫描。 |
| 前端 | 按锁文件安装依赖、Vitest 行为测试、TypeScript 类型检查、Vite 构建及构建产物冒烟检查。 |
| 后端静态检查 | 按锁文件安装依赖、公开测试与应用 Ruff、Python 编译及 Alembic 迁移 SQL 生成。 |
| 后端回归测试 | 临时 PostgreSQL 上执行本地归属、受管入库生命周期、文字回答、SSE/部分回答、意图路由与知识库原文字面定位的 pytest；只用合成资料与确定性模型替身。 |
| 后端集成冒烟 | 临时 PostgreSQL 17.9 上执行 `0004` 新库/重复迁移，核对本地归属；TestClient 启停真实应用生命周期，检查建库/改名/幂等、上传受理与同键任务重放、受限 TXT 解析、私有原文、重启后仍为 parsed 等待及本地请求边界。 |

CI 仅具有 `contents: read` 权限，不部署、不连接真实模型，也不读取工作站数据或凭证。PostgreSQL 服务仅供该 CI 作业使用，使用临时免密实例，无需为本工作流添加真实 GitHub Secrets；此方式不适用于持久业务数据库。

## 测试文件与验收边界

选定的前端测试与后端路由/原文定位测试现随仓库提交，GitHub 可在新克隆运行。其他本机专用测试仍被 `.gitignore` 排除；原有 API 冒烟断言仍在工作流中。公开测试清单以 [工作流](../../.github/workflows/ci.yml) 的白名单和测试命令为准；本机完整测试集见[本地开发](LOCAL-DEVELOPMENT.md#运行检查)。

CI 覆盖安装、构建、迁移与基础 HTTP 行为，不能替代本机完整测试集、浏览器视觉核对或真实 LightRAG/模型接入验证。CI 的空配置实例应报告 `not_configured`；维护者本机已完成模型及 LightRAG 双库真实验证，当前配置匹配报告时 API 的 `models`、`rag` 均显示 `available`。不要为使检查通过而伪造 ready 状态。

CI 通过不代表 M0 完成。固定 SDK、pgvector、模型与重排、双知识库隔离和可核查来源已有独立真实验收；Embedding 超长输入边界仍未通过，详见[路线图](ROADMAP.md)与[M0 验证记录](M0-VALIDATION.md)。

## M1-1 历史检查增量

用户允许先推进 M1-1，M0 保持未完成。工作流增加 `0003_knowledge_management` 迁移和知识库创建/重放/改名/重启持久化的临时 PostgreSQL 冒烟；它不运行本机专用测试，不连接真实模型，也不升级维护者实际业务库。并发去重、容量竞争、旧记录保留、失败事务与回退门禁由本机隔离 PostgreSQL 测试补足，实际本轮结果见 [M1 验证记录](M1-VALIDATION.md)。

M1-1 已随后提交为 `fbafd86`，远程结果见本文开头；开发时未 push 的记录是历史快照。

## M1-2 检查增量

公共基础冒烟已追加 `0004_managed_ingestion` 迁移、同一上传键重放、解析子进程、原文访问及应用生命周期重启。`CITERAG_INGESTION_ENABLED` 保持默认 false，断言解析后仍未修改引擎；CI 不安装/运行真实模型链路。专用生命周期、并发故障、真实 SDK＋隔离引擎库和浏览器测试仍仅本机。

M1-2 开发时，工作流修改尚未 commit/push，也没有对应远端运行；当时的本机结果见 [M1 验证记录](M1-VALIDATION.md)，不能把旧提交的绿色作业计为该切片通过。实际业务库迁移须另行核对；CI 通过也不等于 M0 或完整 M1 验收完成。
