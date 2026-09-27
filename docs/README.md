# CiteRAG 文档导航

当前 M2 工程实现：[语音链路与集中验收](development/M2-VOICE-VALIDATION.md)、[实施设计](development/M2-VOICE-IMPLEMENTATION.md)、[一次真实供应商验证与新截图](development/M2-VOICE-REAL-SUPPLIERS.md)。会话/ASR/VAD/AnswerService/TTS/RTC 已接线；一次公开合成语音的真实 ASR/模型/TTS 与原文字面检索通过，后端171项、前端221项本机通过。真人设备、真实 LightRAG 语义路径及完整 M2 尚未验收。后文未接入描述属于此前快照。

2026-09-27 用户选择 B「对话优先知识助手」，正式 React/Ant Design 工作台与独立语音入口已接通既有方法；语音融合 LiveKit 官方 starter 布局。本轮 A/C 候选已清理。该授权替代旧的原生 UI 技术限制，不改变后端、资料语义或验收边界；见[访问地址与截图](../design/ui/react-candidates/README.md)、[B 版 UI 迁移与验证](development/REACT-UI-MIGRATION.md)和[设计说明](../design/ui/react-candidates/DESIGN.md)。下方原生 UI 和媒体记录为历史快照。

当前阶段：**M0 未完成；M1-1 至 M1-4 本机主链路已实现，完整 M1 未验收**。最新文字改动支持普通交流、通用解释与知识库查询分流、多轮指代和资料总结；总结经原文摘录及独立事实支持检查、提交后才通过 SSE 展示正文与引用，保留旧 `partial` 记录和重试。此前已接通断线恢复、180 天保留、停写门禁和可选每日备份/空环境恢复。入库、问答和备份默认关闭，真实模型端到端仍须另获授权；详见分层验证。

2026-09-26 历史记录：当时首版保持本地单用户、免登录、原生 TypeScript 与「回响」Logo，按三张概念稿升级工作台、知识库与独立语音页，加入来源/任务详情栏和手机导航。随后接入本地 LiveKit 媒体测试、原生官方语音布局与 `voice.html`。这些历史截图见 [UI 升级交付](../design/ui/UI-UPGRADE-20260926.md)和[原生语音页交付](../design/ui/VOICE-NATIVE-20260926.md)；当前 React B 以本文开头为准。图片、ASR/知识库语音回答/TTS 仍未接入。

M0 五模型及真实双库证据沿用，超长 Embedding 输入仍未通过。隔离测试、真实模型验收和历史服务快照分别记录于 [M1 验证](development/M1-VALIDATION.md)；文档中的端口与数据库快照不代表当前在线状态。UI 自动验证不能替代真实资料和模型验收。

| 文档 | 用途 |
|---|---|
| [当前进度与下次交接](development/HANDOFF.md) | 本轮完成项、模型准备、M0 剩余条件与新会话提示词。 |
| [本地单用户首版 PRD](多模态知识助手_PRD_v0.2_单企业首版.md) | 已修订产品范围、三模态闭环、数据边界和验收标准；保留旧文件名以兼容链接。 |
| [技术选型与架构设计](多模态知识助手_技术选型与架构设计_v0.1.md) | React B 正式入口、FastAPI、LightRAG、PostgreSQL、LiveKit 的职责与接入约束。 |
| [开发路线与 M0 清单](development/ROADMAP.md) | 实施顺序、尚未完成的接入验证及分阶段所需资料。 |
| [M1 分步实施计划](development/M1-IMPLEMENTATION.md) | 四步推进文字闭环；当前 M1-3 切片、M1-2/M1-1 历史范围、迁移及验收。 |
| [M1 验证记录](development/M1-VALIDATION.md) | 本片实际检查、模拟与真实隔离 PostgreSQL/浏览器证据及未验证项。 |
| [交流分流与有依据的总结](development/INTENT-AND-GROUNDED-ANSWERS.md) | 普通交流／通用解释／资料问答、多轮意图、引用与事实核验、控件对应和集中验收。 |
| [入库 Unicode 诊断与修复](development/INGESTION-UNICODE-FIX.md) | 切分原文匹配、索引核验、失败状态与恢复边界。 |
| [M2-1 本地 LiveKit 媒体接入](development/M2-LIVEKIT-TRANSPORT.md) | SDK/页面选型、启动配置、真实本地媒体与合成音频证据；ASR/TTS 尚未接入。 |
| [原生官方语音 UI 移植](../design/ui/VOICE-NATIVE-20260926.md) | 独立 HTML 入口、官方来源、桌面/手机截图、控件对应与验证边界。 |
| [M0 实施计划](development/M0-IMPLEMENTATION.md) | 本批范围、影响与验收重点。 |
| [本地单用户调整计划](development/M0-LOCAL-SINGLE-USER.md) | 最新范围决定、旧数据保留方式和迁移验收。 |
| [本地开发](development/LOCAL-DEVELOPMENT.md) | 安装、启动、受控配置与测试命令。 |
| [GitHub CI](development/CI.md) | 自动构建、静态检查、公开合成回归测试与临时 PostgreSQL/API 冒烟验证；与本机专用测试、M0 验收的区别。 |
| [M0 验证记录](development/M0-VALIDATION.md) | 真实验证与模拟验证的证据、缺项。 |
| [UI 升级交付](../design/ui/UI-UPGRADE-20260926.md) | 最新桌面／手机截图、控件与 API、自动验证结果和集中验收步骤。 |
| [UI 设计资产](../design/ui/README.md) | 已确认的三张概念稿、运行界面历史记录与 A 版资产。 |
| [品牌资产](../assets/brand/README.md) | 已选 B「回响」Logo 及使用文件。 |

PRD 与技术架构共同定义当前实施基线；设计中的问题、资料与状态为演示内容，旧导出图中的管理员文字仅保留为历史快照。历史单企业角色方案及多企业方案不作为首版实施要求。文档中的质量、容量与性能目标仍待真实模型、资料及环境验证。
