# CiteRAG 文档导航

从仓库 [README](../README.md) 了解项目、快速启动与当前边界。本目录把**产品目标、当前实现、验证记录和历史切片**分开；旧文档中的时间、端口和“尚未接入”只代表当时快照，不覆盖较新的实现与实际验收结论。

## 产品与架构

| 文档 | 用途 |
| --- | --- |
| [产品需求（PRD）](多模态知识助手_PRD_v0.2_单企业首版.md) | 本地单用户范围、资料和回答规则；0.3 节记录可选知识库、同库共享摘要与普通聊天保留目标。文件名为历史兼容保留。 |
| [技术架构](多模态知识助手_技术选型与架构设计_v0.1.md) | React、FastAPI、LightRAG、双 PostgreSQL、语音与图片的职责和边界；7.2.1 节记录会话改造。 |
| [开发路线](development/ROADMAP.md) | 阶段顺序、未完成条件和下一阶段切片。 |
| [上游参考与许可](REFERENCES.md) | LightRAG 固定来源与 LiveKit 界面许可。 |

## 开发与实现

| 文档 | 用途 |
| --- | --- |
| [本地开发](development/LOCAL-DEVELOPMENT.md) | 依赖、无数据库启动、受控配置、迁移与检查；历史工作站记录不代表当前服务在线。 |
| [GitHub CI](development/CI.md) | 公开合成测试、静态检查和临时 PostgreSQL 冒烟的实际覆盖边界。 |
| [意图路由与有据回答](development/INTENT-AND-GROUNDED-ANSWERS.md) | 普通回答、精确/语义检索、来源与事实核验。 |
| [固定聊天类型与共享摘要](development/CONVERSATION-MODES-AND-MEMORY.md) | 普通聊天、同库共享摘要、隔离迁移、保留期与未来工具授权边界。 |
| [语音链路](development/M2-VOICE-VALIDATION.md) | LiveKit、ASR/VAD、问答、TTS、浏览器播放及集中验收步骤。 |
| [React 界面](development/REACT-UI-MIGRATION.md) | 正式工作台与独立语音入口的实现、控件及验证。 |

## 验证与交接

| 文档 | 用途 |
| --- | --- |
| [M0 验证](development/M0-VALIDATION.md) | 模型与 LightRAG 双库证据；Embedding 超长输入边界仍未通过。 |
| [M1 验证](development/M1-VALIDATION.md) | 资料、文字问答、来源、隔离数据库与业务验收缺项。 |
| [M2 真实供应商记录](development/M2-VOICE-REAL-SUPPLIERS.md) | 一次公开合成语音的真实供应商结果；不代表真人设备验收。 |
| [M3 图片记录](development/M3-MULTIMODAL-VALIDATION.md) | 图片观察、隔离测试与实际业务库迁移边界。 |
| [UI 细化记录](development/UI-POLISH-2026-09-29.md) | 当前桌面方向、对话归档接口及迁移边界。 |
| [工作交接](development/HANDOFF.md) | 分阶段的历史交接信息；使用前核对 Git、服务和数据库现状。 |

`development/` 中的 M0/M1 分步计划、早期语音媒体与原生 UI 记录保留为**历史实施证据**。设计原稿和旧截图位于 [`design/ui/`](../design/ui/README.md)，正式品牌资产位于 [`assets/brand/`](../assets/brand/README.md)。截图、合成测试、真实供应商和实际业务验收必须分别看待。
