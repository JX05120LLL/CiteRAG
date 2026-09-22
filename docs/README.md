# CiteRAG 文档导航

当前阶段：**M0 基础开发，整体验收未通过**。首版已修正为本地单用户，无管理员、注册或登录；工程、本地归属与页面基础正在调整。问答、语音、图片未开放。项目名称 CiteRAG、A 版桌面 UI、第二款 B「回响」无字 Logo 已确定。

| 文档 | 用途 |
|---|---|
| [本地单用户首版 PRD](多模态知识助手_PRD_v0.2_单企业首版.md) | 已修订产品范围、三模态闭环、数据边界和验收标准；保留旧文件名以兼容链接。 |
| [技术选型与架构设计](多模态知识助手_技术选型与架构设计_v0.1.md) | 原生 TypeScript、FastAPI、LightRAG、PostgreSQL、LiveKit 的职责与接入约束。 |
| [开发路线与 M0 清单](development/ROADMAP.md) | 实施顺序、尚未完成的接入验证及分阶段所需资料。 |
| [M0 实施计划](development/M0-IMPLEMENTATION.md) | 本批范围、影响与验收重点。 |
| [本地单用户调整计划](development/M0-LOCAL-SINGLE-USER.md) | 最新范围决定、旧数据保留方式和迁移验收。 |
| [本地开发](development/LOCAL-DEVELOPMENT.md) | 安装、启动、受控配置与测试命令。 |
| [GitHub CI](development/CI.md) | 自动构建、静态检查与临时 PostgreSQL/API 冒烟验证；与本机专用测试、M0 验收的区别。 |
| [M0 验证记录](development/M0-VALIDATION.md) | 真实验证与模拟验证的证据、缺项。 |
| [选定 UI 设计](../design/ui/README.md) | A 版桌面界面及设计说明。 |
| [品牌资产](../assets/brand/README.md) | 已选 B「回响」Logo 及使用文件。 |

PRD 与技术架构共同定义当前实施基线；设计中的问题、资料与状态为演示内容，旧导出图中的管理员文字仅保留为历史快照。历史单企业角色方案及多企业方案不作为首版实施要求。文档中的质量、容量与性能目标仍待真实模型、资料及环境验证。
