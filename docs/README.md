# CiteRAG 文档导航

当前阶段：**M0 未完成；M1-1 至 M1-4 本机主链路已实现，完整 M1 未验收**。最新本机改动增加回答模型 SSE 增量、逐段原文前缀检查和 `partial` 持久状态；临时片段不算已核验回答，来源只在最终校验并提交后展示。此前已接通断线恢复、180 天保留、停写门禁和可选每日备份/空环境恢复。入库、问答和备份默认关闭，真实模型端到端仍须另获授权；详见分层验证。

此前接手时 8000、5174、55432 不可达，未读取实际业务库 schema；更早的 `0002` 为历史核查。`0004`—`0007` 仅在隔离库升级，真实业务未迁移、旧 API 未重启。固定 SDK＋隔离 PostgreSQL 使用本地模型替身，新增真实模型请求为 0；M0 五模型及真实双库证据沿用，超长 Embedding 输入仍暂缓未通过。首版保持本地单用户、免登录、原生 TypeScript、A 版 UI 与「回响」Logo。

最新只读收尾检查中，API、前端、业务库和引擎库均不可达；原因未定位、未尝试启动旧服务。先前引擎库可连接是历史快照，具体检查见 [交接](development/HANDOFF.md)和 [M1 验证](development/M1-VALIDATION.md)。

| 文档 | 用途 |
|---|---|
| [当前进度与下次交接](development/HANDOFF.md) | 本轮完成项、模型准备、M0 剩余条件与新会话提示词。 |
| [本地单用户首版 PRD](多模态知识助手_PRD_v0.2_单企业首版.md) | 已修订产品范围、三模态闭环、数据边界和验收标准；保留旧文件名以兼容链接。 |
| [技术选型与架构设计](多模态知识助手_技术选型与架构设计_v0.1.md) | 原生 TypeScript、FastAPI、LightRAG、PostgreSQL、LiveKit 的职责与接入约束。 |
| [开发路线与 M0 清单](development/ROADMAP.md) | 实施顺序、尚未完成的接入验证及分阶段所需资料。 |
| [M1 分步实施计划](development/M1-IMPLEMENTATION.md) | 四步推进文字闭环；当前 M1-3 切片、M1-2/M1-1 历史范围、迁移及验收。 |
| [M1 验证记录](development/M1-VALIDATION.md) | 本片实际检查、模拟与真实隔离 PostgreSQL/浏览器证据及未验证项。 |
| [M0 实施计划](development/M0-IMPLEMENTATION.md) | 本批范围、影响与验收重点。 |
| [本地单用户调整计划](development/M0-LOCAL-SINGLE-USER.md) | 最新范围决定、旧数据保留方式和迁移验收。 |
| [本地开发](development/LOCAL-DEVELOPMENT.md) | 安装、启动、受控配置与测试命令。 |
| [GitHub CI](development/CI.md) | 自动构建、静态检查与临时 PostgreSQL/API 冒烟验证；与本机专用测试、M0 验收的区别。 |
| [M0 验证记录](development/M0-VALIDATION.md) | 真实验证与模拟验证的证据、缺项。 |
| [选定 UI 设计](../design/ui/README.md) | A 版桌面界面及设计说明。 |
| [品牌资产](../assets/brand/README.md) | 已选 B「回响」Logo 及使用文件。 |

PRD 与技术架构共同定义当前实施基线；设计中的问题、资料与状态为演示内容，旧导出图中的管理员文字仅保留为历史快照。历史单企业角色方案及多企业方案不作为首版实施要求。文档中的质量、容量与性能目标仍待真实模型、资料及环境验证。
