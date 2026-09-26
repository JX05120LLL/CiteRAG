# 原生 LiveKit 语音页交付 · 2026-09-26

用户选择独立 HTML + 原生 TypeScript 方案。语音页移植 [LiveKit Agent Starter](https://github.com/livekit-examples/agent-starter-react) 的白底布局、五条音频柱、蓝色开始按钮和底部通话控件，保留 CiteRAG 品牌、中文与知识库上下文。没有引入 React/Next.js 或另一套聊天后端；官方来源版本、移植范围与 MIT 许可见 [vendor 记录](../../frontend/vendor/livekit/README.md)。

## 入口与截图

Vite 同时构建 `index.html` 与 `voice.html`。工作台点击“语音通话”展示同一原生组件；独立入口 `http://127.0.0.1:5173/voice.html?conversation=<当前聊天 ID>` 会读取本人聊天和固定知识库。同一标签页可沿用工作台选择直接打开 `voice.html`。缺少可用聊天时禁用连接，不自动创建或连接。

截图来自真实 Chromium、真实本地 LiveKit 和 WebRTC；输入为合成音频，聊天/知识库/归属依赖为合成测试响应。截图中的资料、聊天和已连接状态是隔离测试结果，不代表实际业务或语音助手验收。

| 页面 | 截图 |
|---|---|
| 桌面欢迎页 | [1440 × 1000](exports/voice-native-welcome-1440.png) |
| 桌面真实媒体连接 | [1440 × 1000](exports/voice-native-media-1440.png) |
| 手机真实媒体连接 | [390 × 844](exports/voice-native-media-390.png) |
| 手机断开原因 | [390 × 844](exports/voice-native-failure-390.png) |
| 手机独立 HTML 入口 | [390 × 844](exports/voice-native-standalone-390.png) |

以上五张均已目视复核。另检查 320px 布局无横向溢出；这属于浏览器视口验证，没有使用真实手机。

## 控件与实际方法

| 控件/状态 | 方法或 API | 当前效果 |
|---|---|---|
| 配置与刷新 | `GET /api/voice/status` | 显示关闭/未配置/可测试；配置可用不等于媒体已连通 |
| 独立页上下文 | `GET /api/conversations/{id}`、`GET /api/knowledge-bases` | 本人聊天、固定库与 ready 核对，连接接口再次校验 |
| 测试本地音频连接 | `POST /api/conversations/{id}/voice/token` → SDK `Room.connect` → `setMicrophoneEnabled(true)` | 点击后才请求凭证与麦克风；SDK 延迟加载 |
| 五条音频柱 | SDK `createAudioAnalyser` / Web Audio | 实际麦克风频段采样；静音或挂断清零，无装饰循环冒充输入 |
| 静音/取消静音 | SDK `setMicrophoneEnabled` | 操作真实本地音轨，保留键盘焦点 |
| 关闭/开启声音 | `<audio>.muted`、SDK `startAudio` | 操作真实远端播放；此片没有 TTS |
| 挂断 | 清理采样/音轨/监听/音频元素、SDK `Room.disconnect(true)` | 等待清理完成，实际参与者退出 |
| 返回聊天/查看系统状态 | 先挂断；独立页导航 `index.html` / `index.html?view=status` | 恢复已验证的聊天选择，系统页调用现有状态 API |
| 字幕 | 禁用，有原因提示 | ASR 尚未接入，没有伪造转写 |
| 助手状态 | 明确“语音助手尚未接入” | 不显示模拟 listening/thinking/speaking 或知识来源 |

页面更新复用 DOM 控件，修复静音后键盘焦点丢失的问题。离开独立页会停止媒体；页面恢复时重新核对聊天与库，修复缓存恢复后控件失效的问题。音频分析器不可用时显示原因，保持媒体连接；减少动态效果偏好下以透明度响应音量。错误使用安全说明，不显示 Token、底层私有连接信息或原始异常。

## 本轮验证

- 前端最终检查：**8 文件、178 项通过**；`pnpm typecheck`、`pnpm build` 通过。数量包含当前共享工作区的其他文字回归，不全部归于语音改动。独立页新增 8 项，语音控制器现有 10 项；工作台回归核对返回聊天、焦点与状态入口。
- 两个构建入口的模块、样式和图标引用均检查存在。SDK 使用独立动态块；原始块 562.10 kB、gzip 148.01 kB，仍有超过 500 kB 的构建提示。没有提高阈值隐藏提示。
- [浏览器证据 JSON](exports/voice-native-checks.json)：13 项媒体/布局检查、4 项真实断开恢复检查、10 项独立页检查，共 **27 项通过**。媒体与独立页检查没有 pageerror；供应商请求为 0。独立页恢复检查由人工派发 `pagehide/pageshow` 事件驱动真实媒体重连，不等于各浏览器真实缓存恢复验收。
- 实际点通：双端音频音轨/RTP 接收、静音传递、输出开关、取消静音后柱高随输入变化、挂断并退出房间、删除本次测试房间后的断开说明、手动重连、返回文字、独立页系统状态导航。
- 无点击不发 Token、不采集音频、不加载 SDK；独立页缺少聊天或无权访问时禁用连接。合成门禁测试不等于真实业务归属验收。
- 双端测试曾将通话页留在后台，浏览器暂停 `requestAnimationFrame`，造成音量柱静止。检查改为将被检页面切回前台，再核对实际柱高并重拍截图；没有使用假音量补图。
- 本轮原生 UI 未修改后端实现。此前媒体首片的 14 项后端凭证回归及 56 passed/74 skipped 记录见 [M2-1](../../docs/development/M2-LIVEKIT-TRANSPORT.md)，不是本次重新运行的后端结果。

## 集中验收与未验证项

按 [M2 启动步骤](../../docs/development/M2-LIVEKIT-TRANSPORT.md#启动与集中验收) 准备本地 LiveKit 和已加载语音配置的 API。仓库默认关闭媒体测试；本轮未重启已有 8000 API，也未修改私人启动器、业务库或模型配置。测试服务使用独立端口，完成后关闭。

1. 工作台打开 ready 库的聊天，点击语音通话；再用独立 `voice.html` 检查相同聊天/库上下文。未选择聊天的新标签页应禁用连接。
2. 点击“测试本地音频连接”，允许真人麦克风，确认浏览器采集指示与输入音量。静音时音量清零，取消静音恢复；用 Tab 操作控件检查焦点。
3. 挂断、返回聊天和查看系统状态后，确认采集停止。真实设备拒绝权限应显示可操作说明；本轮拒绝权限仅自动化替身验证。
4. 使用本地测试房间/服务检查断线与重连。没有其他参与者时“0 路远端音频”正常；当前没有语音助手，不能据此验收回答与播报。
5. 在真实手机检查布局、音频权限和扬声器听感；浏览器 390/320px 证据不覆盖这些设备行为。

仍未验证：真人麦克风/扬声器、真实权限拒绝、实际业务数据与归属、ASR → AnswerService → TTS、正式通话租约、Cloud 和性能。原始音频不保存；未调用模型、迁移 schema、部署、commit 或 push。完整 M0/M1/M2 均保持未验收。
