# LiveKit 官方语音 UI 移植来源

来源：[livekit-examples/agent-starter-react](https://github.com/livekit-examples/agent-starter-react)，固定提交 `c5d78a6c381a0ac80b081cf6aeb8ac454d00ca78`，MIT，许可全文见 [LICENSE](LICENSE)。

用户选择保持原生 TypeScript，只移植语音页 UI。本项目不运行官方 React/Next.js 模板，不引入 React、Tailwind、官方云端示例 agent 或独立聊天数据。

原生实现：[voice.ts](../../src/pages/voice.ts)、[voice.css](../../src/features/voice/voice.css)。移植范围：

- `components/app/welcome-view.tsx`：欢迎页中心布局、五条几何音频柱（22/54/38/22/30）、256px 圆角开始按钮。
- `components/agents-ui/blocks/agent-session-view-01/components/agent-session-block.tsx`、`tile-view.tsx`：语音主区与 672px 底部控制区。
- `components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer.tsx`：五条 64px 音频柱、16px 间距；手机调整为 44px/12px。
- `styles/globals.css`：白底、黑灰文字、`#002cf2` 开始按钮及 `0.625rem` 控件容器圆角。

修改：CiteRAG 品牌与中文，系统字体替代模板字体；麦克风音量使用 SDK/Web Audio 实际采样，控制操作沿用本项目生命周期与凭证。助手未接入时不展示 listening/thinking/speaking、字幕或来源；未连接时的静态五条柱为装饰，采集前不创建音频分析器。没有复制视频、屏幕共享、Cloud Token endpoint 或 Agents 聊天功能。
