# LiveKit 官方语音 UI 移植来源

M2 2026-09-27：该MIT许可覆盖UI布局；新增Python RTC/管理SDK依据各自Apache-2.0许可安装，不能混称MIT。React Voice共用原控制器与同进程后端worker，未安装官方React组件/Agents SDK。链路、精确版本和语音验证边界见 [M2交付](../../../docs/development/M2-VOICE-VALIDATION.md)。

来源：[livekit-examples/agent-starter-react](https://github.com/livekit-examples/agent-starter-react)，固定提交 `c5d78a6c381a0ac80b081cf6aeb8ac454d00ca78`，MIT，许可全文见 [LICENSE](LICENSE)。

2026-09-27 用户选定 B 并要求官方语音界面融合。当前 [React Voice](../../src/react/Voice.tsx) 采用下述欢迎/会话布局，以 Ant Design 与 B tokens 重写，工作台和独立入口共用；媒体沿用现有 VoiceController / livekit-client 单生命周期。未安装 LiveKit React 组件包，不运行官方 Next.js/Cloud 或独立 Agents 后端，也不引入 Tailwind。许可全文继续保留。下方原生实现与蓝色 token 是 2026-09-26 的历史范围，当前主题为 B 的绿色。

原生实现：[voice.ts](../../src/pages/voice.ts)、[voice.css](../../src/features/voice/voice.css)。移植范围：

- `components/app/welcome-view.tsx`：欢迎页中心布局、五条几何音频柱（22/54/38/22/30）、256px 圆角开始按钮。
- `components/agents-ui/blocks/agent-session-view-01/components/agent-session-block.tsx`、`tile-view.tsx`：语音主区与 672px 底部控制区。
- `components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer.tsx`：五条 64px 音频柱、16px 间距；手机调整为 44px/12px。
- `styles/globals.css`：白底、黑灰文字、`#002cf2` 开始按钮及 `0.625rem` 控件容器圆角。

修改：CiteRAG 品牌与中文，系统字体替代模板字体；麦克风音量使用 SDK/Web Audio 实际采样，控制操作沿用本项目生命周期与凭证。助手未接入时不展示 listening/thinking/speaking、字幕或来源；未连接时的静态五条柱为装饰，采集前不创建音频分析器。没有复制视频、屏幕共享、Cloud Token endpoint 或 Agents 聊天功能。
