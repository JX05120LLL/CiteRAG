# LiveKit 界面参考与许可

语音欢迎与通话布局参考并改写自 [livekit-examples/agent-starter-react](https://github.com/livekit-examples/agent-starter-react)，固定来源提交 `c5d78a6c381a0ac80b081cf6aeb8ac454d00ca78`。上游布局代码为 MIT 许可，全文见本目录的 [LICENSE](LICENSE)。

CiteRAG 的正式语音页位于 [Voice.tsx](../../src/react/Voice.tsx)，使用本项目的 React/Ant Design 主题。媒体连接由现有 VoiceController 和 livekit-client 管理；后端使用独立锁定的 LiveKit Python SDK。没有复制上游的 Next.js 服务、Cloud Token endpoint、视频共享或另一套 Agents 问答服务。当前语音工作流仍由 CiteRAG 的 AnswerService 处理。

MIT 声明仅覆盖相应上游布局参考；CiteRAG 自有内容采用 [Apache License 2.0](../../../LICENSE)，其他依赖仍按各自许可证使用。首版运行边界见[项目 README](../../../README.md)。
