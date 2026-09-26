# CiteRAG 文档导航

当前阶段：**M0 未完成；M1-1 至 M1-4 本机主链路已实现，完整 M1 未验收**。最新本机改动增加回答模型 SSE 增量、逐段原文前缀检查和 `partial` 持久状态；临时片段不算已核验回答，来源只在最终校验并提交后展示。此前已接通断线恢复、180 天保留、停写门禁和可选每日备份/空环境恢复。入库、问答和备份默认关闭，真实模型端到端仍须另获授权；详见分层验证。

首版保持本地单用户、免登录、原生 TypeScript 与「回响」Logo。2026-09-26 已按确认的三张概念稿升级工作台、知识库与独立语音页，加入来源/任务详情栏和手机导航；图片与实际通话尚未接入。最新截图、控件对应接口、合成浏览器证据及人工验收步骤见 [UI 升级交付](../design/ui/UI-UPGRADE-20260926.md)。

M0 五模型及真实双库证据沿用，超长 Embedding 输入仍未通过。隔离测试、真实模型验收和历史服务快照分别记录于 [M1 验证](development/M1-VALIDATION.md)；文档中的端口与数据库快照不代表当前在线状态。UI 自动验证不能替代真实资料和模型验收。

| 文档 | 用途 |
|---|---|
| [当前进度与下次交接](development/HANDOFF.md) | 本轮完成项、模型准备、M0 剩余条件与新会话提示词。 |
| [本地单用户首版 PRD](多模态知识助手_PRD_v0.2_单企业首版.md) | 已修订产品范围、三模态闭环、数据边界和验收标准；保留旧文件名以兼容链接。 |
| [技术选型与架构设计](多模态知识助手_技术选型与架构设计_v0.1.md) | 原生 TypeScript、FastAPI、LightRAG、PostgreSQL、LiveKit 的职责与接入约束。 |
| [开发路线与 M0 清单](development/ROADMAP.md) | 实施顺序、尚未完成的接入验证及分阶段所需资料。 |
| [M1 分步实施计划](development/M1-IMPLEMENTATION.md) | 四步推进文字闭环；当前 M1-3 切片、M1-2/M1-1 历史范围、迁移及验收。 |
| [M1 验证记录](development/M1-VALIDATION.md) | 本片实际检查、模拟与真实隔离 PostgreSQL/浏览器证据及未验证项。 |
| [入库 Unicode 诊断与修复](development/INGESTION-UNICODE-FIX.md) | 切分原文匹配、索引核验、失败状态与恢复边界。 |
| [M0 实施计划](development/M0-IMPLEMENTATION.md) | 本批范围、影响与验收重点。 |
| [本地单用户调整计划](development/M0-LOCAL-SINGLE-USER.md) | 最新范围决定、旧数据保留方式和迁移验收。 |
| [本地开发](development/LOCAL-DEVELOPMENT.md) | 安装、启动、受控配置与测试命令。 |
| [GitHub CI](development/CI.md) | 自动构建、静态检查、公开合成回归测试与临时 PostgreSQL/API 冒烟验证；与本机专用测试、M0 验收的区别。 |
| [M0 验证记录](development/M0-VALIDATION.md) | 真实验证与模拟验证的证据、缺项。 |
| [UI 升级交付](../design/ui/UI-UPGRADE-20260926.md) | 最新桌面／手机截图、控件与 API、自动验证结果和集中验收步骤。 |
| [UI 设计资产](../design/ui/README.md) | 已确认的三张概念稿、运行界面历史记录与 A 版资产。 |
| [品牌资产](../assets/brand/README.md) | 已选 B「回响」Logo 及使用文件。 |

PRD 与技术架构共同定义当前实施基线；设计中的问题、资料与状态为演示内容，旧导出图中的管理员文字仅保留为历史快照。历史单企业角色方案及多企业方案不作为首版实施要求。文档中的质量、容量与性能目标仍待真实模型、资料及环境验证。
