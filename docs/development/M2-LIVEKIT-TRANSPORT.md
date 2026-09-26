# M2-1 本地 LiveKit 媒体接入 · 2026-09-26

用户允许在完整 M1 尚未验收时开始语音，并选择本地自托管，Cloud 留后续。此片完成音频传输基础，ASR/TTS、知识库语音对话和正式通话业务约束尚未接入，M0/完整 M1/M2 均不能标为通过。

## 官方能力与选型

| 能力 | 官方提供 | 本片采用 |
|---|---|---|
| 浏览器 SDK | [JavaScript/TypeScript SDK](https://docs.livekit.io/reference/client-sdk-js/) | `livekit-client=2.22.3`，原生 TS、动态加载 |
| 语音页面 | [Agent Starter React](https://github.com/livekit-examples/agent-starter-react)、[React 组件](https://github.com/livekit/components-js) | 参考交互，保留 TS/HTML/CSS，不引入 React/Next.js |
| 服务端凭证 | [AccessToken/VideoGrants](https://docs.livekit.io/reference/python/livekit/api/access_token.html) | 可选 `livekit-api=1.2.1`，后端签发 |
| 本地服务 | [自托管说明](https://docs.livekit.io/transport/self-hosting/local/) | Windows Server `1.13.7`，下载包已与官方 `checksums.txt` 核对 SHA256 |
| 语音代理 | [LiveKit Agents](https://docs.livekit.io/agents/) | 下一片接 ASR、统一 AnswerService、TTS/VAD；本片没有启动模型或 Agents |

Server/客户端负责实时媒体，自身不识别、回答或合成语音。后续沿用项目已选火山 ASR、MiniMax `speech-02-turbo` 和本地 Silero VAD，不从示例复制另一套 LightRAG 或聊天历史。

### 官方通话页面（已选择原生移植）

[当前官方完整页](https://github.com/livekit-examples/agent-starter-react)采用 React/Next.js 和 Agents UI，提供音频可视化、媒体控件与字幕；可以修改品牌与功能。默认会连接官方演示 agent，接入本项目时必须改为本地 LiveKit、本项目凭证接口与本项目 worker，不能沿用演示助手。字幕依赖代理实际发送的转写，界面不会自动提供识别或知识库回答。

[旧 agent-starter-embed](https://github.com/livekit-examples/agent-starter-embed)已明确弃用，本地新接入不采用它，也不启用 Cloud 嵌入。用户确认选择第一种方案：独立 HTML + 原生 TypeScript，使用官方 JS SDK，将官方语音页布局与控件移植到当前 Vite。源版本固定为 `c5d78a6c381a0ac80b081cf6aeb8ac454d00ca78`，来源范围和 MIT 许可见 [移植记录](../../frontend/vendor/livekit/README.md)。这是官方 UI 的原生移植，不是运行原始 React 模板。

独立入口为 `voice.html?conversation=<当前聊天 ID>`，也可读取本标签页工作台的选择；工作台内的语音入口复用相同界面与生命周期。独立页只读核对本人聊天和固定知识库，无聊天、无法读取或库未就绪时禁用连接；凭证接口再次执行服务端门禁。返回聊天或查看系统状态先等待挂断。没有增加 React/Next.js、Cloud 请求或示例聊天后端。最新截图与验证见 [原生语音页交付](../../design/ui/VOICE-NATIVE-20260926.md)。

## 控件与实际接线

| 控件/行为 | 实际调用 | 边界 |
|---|---|---|
| 打开语音页、刷新配置 | `GET /api/voice/status` | 只读；configured 只说明配置/依赖存在，不表示已连通 |
| 独立页读取聊天/知识库 | `GET /api/conversations/{id}`、`GET /api/knowledge-bases` | 只读归属与 ready 核对，不新建聊天、不写业务库 |
| 测试音频连接 | `POST /api/conversations/{id}/voice/token` → `Room.connect` → `setMicrophoneEnabled(true)` | 用户点击后请求权限；要求本人聊天、ready 库、无进行中回答 |
| 五条输入音量柱 | SDK `createAudioAnalyser` / Web Audio | 实际麦克风采样；静音/挂断清零，不模拟助手 speaking |
| 静音 | `setMicrophoneEnabled` | 实际本地麦克风音轨 |
| 关闭/开启声音 | `<audio>.muted`、`Room.startAudio` | 播放开关，不等于取消回答/TTS 生成 |
| 挂断、返回、切页/pagehide | 音轨/监听/音频元素清理、`Room.disconnect(true)` | 拒绝迟到 Token/权限/回调；重复挂断等待同一清理 |
| 字幕、助手对话 | 禁用并解释 | 没有模拟转写、助手在线状态或知识来源 |

默认 `CITERAG_VOICE_TRANSPORT_ENABLED=false`。前后端一致只支持 `localhost`、`127.0.0.1`、`[::1]` 的 WebSocket origin，其他回环别名也拒绝，避免配置已加载但客户端拒绝连接；密钥仅在后端，前端不持久化 Token。房间随机命名，不含业务 ID/文件名；Token 只允许指定房间的 microphone 发布和订阅，禁止 data 发布、摄像头、录屏、自改元数据，不含管理/录制权限。API 沿用本地 Host/Origin、归属门禁。默认不保存音频，不改 schema，不写通话业务记录。

[Token 有效期只影响初次认证](https://docs.livekit.io/frontends/reference/tokens-grants/)：90 秒不是已连接通话的自动挂断期限。本片明确为 `media_test`，每次独立房间；正式通话仍需租约、单控制端、维护中止、重启回收和服务端撤销。

## 实际验证与截图

- 官方 UI 原生移植后的当前结果、独立入口与桌面/手机截图见 [原生语音页交付](../../design/ui/VOICE-NATIVE-20260926.md)。下方保留此前媒体首片的验证快照。
- 媒体首片前端 7 文件 **164 项通过**，类型检查与构建通过；先复现并修复重复挂断提前显示 idle 的竞态。主入口 gzip 约 31.46 kB，SDK 单独动态加载、gzip 约 147.75 kB；构建有 SDK 原始块超过 500 kB 的提示，未隐藏。
- 独立 `rag+voice` 虚拟环境的公开九文件 **56 passed、74 skipped**，跳过项需独立 PostgreSQL/相应条件；新增语音文件 14 项通过，Ruff/编译通过。先复现并修复前后端地址接受范围不一致。该公开测试数量包含同工作区并行进行的文字链路修复，语音贡献以新增文件为准。短开发密钥产生 PyJWT 警告，另有既有 TestClient 弃用警告。
- [浏览器检查 JSON](../../design/ui/exports/voice-local-checks.json)：真实本地 LiveKit、签名 SDK、Chromium 和 WebRTC；合成音频、合成聊天/归属依赖，不使用实际业务 PostgreSQL、真人麦克风、ASR/TTS 或模型。核对另一端实际 RTP 字节/包计数，点通静音传递、输出开关和挂断后的参与者退出。
- 实际删除本会话创建的测试房间，页面显示断开并清理音轨；手动重连清除错误，返回文字页停止媒体。桌面/手机截图已目视核对，手机无横向溢出。
- Windows Server 报 CPU 容量监控不支持，未测容量/性能。验证使用独立 17880/17881/17882 和 18000；既有 8000 API、5173 Vite 未停止或重启。测试结束关闭本片专用浏览器与测试服务。

| 页面 | 截图 |
|---|---|
| 桌面媒体连接 | [1440px](../../design/ui/exports/voice-local-media-1440.png) |
| 手机媒体连接 | [390px](../../design/ui/exports/voice-local-media-390.png) |
| 手机真实断开 | [失败原因](../../design/ui/exports/voice-local-failure-390.png) |

这些是实际媒体协议验证，不是语音助手或真实业务验收。当前 API 若未加载新接口，前端会提示版本/配置不可用，不沿用截图的连接状态。

## 启动与集中验收

1. 按官方说明下载 Windows Server，二进制放私有 `.local/`，先核对端口归属；独立终端执行 `livekit-server --dev --bind 127.0.0.1`。devkey/secret 是官方本机开发值。RTC 媒体监听与信令监听分开，信令回环不证明全部媒体端口限制到回环；本片不修改防火墙。
2. 后端 `uv sync --locked --extra rag --extra voice`；前端 `pnpm install --frozen-lockfile`。本轮使用独立虚拟环境，未改正在运行 API 的虚拟环境。
3. 在现有已授权验收 API 的启动配置中设置下方字段，下次正常重启加载；先结束该服务的进行中工作，不另占用 8000、不迁移库或开启模型。保留原独立验收业务库配置。

```powershell
$env:CITERAG_VOICE_TRANSPORT_ENABLED = 'true'
$env:CITERAG_LIVEKIT_URL = 'ws://127.0.0.1:7880'
$env:CITERAG_LIVEKIT_API_KEY = 'devkey'       # 官方本机开发值
$env:CITERAG_LIVEKIT_API_SECRET = 'secret'   # 官方本机开发值
```

变量由 `Settings.from_env()` 读取；若启动器显式构造 `Settings(...)`，须传入 `voice_transport_enabled`、`livekit_url`、`livekit_api_key` 和 `livekit_api_secret`，环境变量不会覆盖显式对象。本轮未改私人验收启动器。

4. 在工作台选择 ready 库并打开聊天，进入语音页；也可用同一标签页打开 `http://127.0.0.1:5173/voice.html`。直接打开未选择聊天的新标签页会禁用连接，可显式传 `?conversation=<当前聊天 ID>`。仍须提示助手未接入；点击音频测试、允许权限，核对设备指示、实际输入音量、静音、挂断、返回聊天后停止采集。没有另一参与者时 0 路远端音频正常。拒绝麦克风权限须显示原因。
5. 断开本地服务/测试房间，核对重连、断开说明、旧音轨清理与手动重连。手机检查控件和错误说明。

真人设备/扬声器听感、真实权限拒绝、实际业务归属、Cloud、ASR/TTS 与性能仍未验收。音频测试不需要语音模型请求，本侧会话供应商请求为 0。

## 下一片：ASR → AnswerService → TTS

1. 单控制端、服务端租约、room/session/conversation/kb 绑定、维护中止与重启清理；通话中禁止新增文字/图片问题。
2. LiveKit worker 接火山流式 ASR/VAD；中间字幕不创建正式输入，最终转写通过幂等 ID 只提交一次，纠错撤销旧轮次。
3. 内部接口调用现有 AnswerService，沿用 auto 路由、库修订与来源门禁；worker 不创建 LightRAG 或独立聊天历史。
4. MiniMax TTS 播放应用核验正文，屏幕保留引用；插话/停止撤销旧输出，generation 拒绝迟到音频，TTS 失败保留文字。
5. 说明音频/正文发送范围、费用并获得真实语音调用授权，再验收 AT-13/AT-14、设备与性能；Cloud 独立配置与验证。

代码及 CI 本片未提交/推送。CI 增加后端凭证、前端生命周期及独立页三份公开语音回归与 `voice` 可选依赖，不启动 LiveKit、不采集音频或调用模型；旧远程 CI 结果不覆盖这些变更。
