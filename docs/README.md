# CiteRAG 文档导航

当前阶段：**M0 尚未整体验收；用户授权先推进 M1，M1-1 知识库管理已实现并通过本机验证，完整 M1 未完成**。本片只覆盖建库、改名、列表持久化、建库幂等和 5 库上限，空库不可建聊天；上传、任务、问答、语音、图片留后续切片。五模型、短文本 Embedding 1024 维及真实百炼＋LightRAG＋独立 PostgreSQL 双库验收沿用 M0 证据；超长输入边界暂缓且未通过。既有 8000 服务保持旧基线，只读 `database/models/rag=available`；本轮只在隔离测试库验证新迁移，不升级实际业务库。首版仍为本地单用户，无管理员、注册或登录，保留 CiteRAG、A 版桌面 UI 和「回响」无字 Logo。

| 文档 | 用途 |
|---|---|
| [当前进度与下次交接](development/HANDOFF.md) | 本轮完成项、模型准备、M0 剩余条件与新会话提示词。 |
| [本地单用户首版 PRD](多模态知识助手_PRD_v0.2_单企业首版.md) | 已修订产品范围、三模态闭环、数据边界和验收标准；保留旧文件名以兼容链接。 |
| [技术选型与架构设计](多模态知识助手_技术选型与架构设计_v0.1.md) | 原生 TypeScript、FastAPI、LightRAG、PostgreSQL、LiveKit 的职责与接入约束。 |
| [开发路线与 M0 清单](development/ROADMAP.md) | 实施顺序、尚未完成的接入验证及分阶段所需资料。 |
| [M1 分步实施计划](development/M1-IMPLEMENTATION.md) | 四步推进文字闭环；第一片知识库管理的范围、迁移与验收。 |
| [M1 验证记录](development/M1-VALIDATION.md) | 本片实际检查、模拟与真实隔离 PostgreSQL/浏览器证据及未验证项。 |
| [M0 实施计划](development/M0-IMPLEMENTATION.md) | 本批范围、影响与验收重点。 |
| [本地单用户调整计划](development/M0-LOCAL-SINGLE-USER.md) | 最新范围决定、旧数据保留方式和迁移验收。 |
| [本地开发](development/LOCAL-DEVELOPMENT.md) | 安装、启动、受控配置与测试命令。 |
| [GitHub CI](development/CI.md) | 自动构建、静态检查与临时 PostgreSQL/API 冒烟验证；与本机专用测试、M0 验收的区别。 |
| [M0 验证记录](development/M0-VALIDATION.md) | 真实验证与模拟验证的证据、缺项。 |
| [选定 UI 设计](../design/ui/README.md) | A 版桌面界面及设计说明。 |
| [品牌资产](../assets/brand/README.md) | 已选 B「回响」Logo 及使用文件。 |

PRD 与技术架构共同定义当前实施基线；设计中的问题、资料与状态为演示内容，旧导出图中的管理员文字仅保留为历史快照。历史单企业角色方案及多企业方案不作为首版实施要求。文档中的质量、容量与性能目标仍待真实模型、资料及环境验证。
