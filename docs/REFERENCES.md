# 上游组件与来源

本项目使用 [LightRAG](https://github.com/HKUDS/LightRAG) SDK 承担知识库索引与检索；会话、资料和引用由 CiteRAG 管理。版本以项目锁文件和代码为准；固定提交不表示上游当前最新版。

| 上游 | 本仓库用途 | 来源与许可 |
| --- | --- | --- |
| LightRAG SDK | `backend` 的 `rag` 可选依赖，负责知识库索引和检索；CiteRAG 另行管理资料、会话和引用核验。 | [固定提交 `59af311307c7417b342f44850b097648d47e83bd`](https://github.com/HKUDS/LightRAG/tree/59af311307c7417b342f44850b097648d47e83bd) · [该提交的 MIT 许可](https://github.com/HKUDS/LightRAG/blob/59af311307c7417b342f44850b097648d47e83bd/LICENSE) |
| LiveKit 官方 starter 布局 | 语音欢迎和通话界面的布局参考与改写；媒体由本项目控制器和 SDK 管理。 | [固定来源、改写范围及 MIT 许可](../frontend/vendor/livekit/README.md) |

这些上游的许可不自动授予 CiteRAG 自身的开源许可。仓库暂未设置项目 `LICENSE`；正式开放使用与贡献前需确定本项目许可，并保留应有的上游声明。历史阶段文档保留当时的选型与验证记录，不应作为当前运行依赖清单。
