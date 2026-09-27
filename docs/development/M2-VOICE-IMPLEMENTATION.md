# M2 语音闭环设计与实施记录

提交补记（2026-09-27）：用户随后授权将本次改动提交并推送 GitHub main；下方“不提交/推送”为开发阶段原始边界。CI 结果以新提交的对应运行记录为准，完整 M2 尚待真人等验收。

日期：2026-09-27；基线 `615ec4d`。本轮用户授权语音会话公共协议扩展，保持不改 schema、不迁移实际业务库、不提交或推送。随后另行授权一次公开合成供应商验证；用户配置 ASR/TTS 后，该次真实路径通过，实际 ASR1流/模型3次/TTS1次，详见 [实测及收尾修复](M2-VOICE-REAL-SUPPLIERS.md)。

## 设计

唯一 API owner 管理内存通话租约及嵌入式 LiveKit RTC worker。会话绑定本地 owner、conversation、kb、revision、active_workspace、随机房间及控制端。失去租约、维护、断线或重启立即取消；不依赖 Token 到期。持久文字及引用仍使用现有 AnswerService/消息/attempt 表。worker 不建立 LightRAG 或第二套历史。

浏览器显式开始后创建会话，内存控制凭证经请求头发送，定时续租，订阅 SSE。音频经 LiveKit → 16k 单声道 PCM → 本地 Silero VAD → 火山双向流式 ASR。中间字幕临时展示；最终文本以 session/utterance 修订生成稳定请求 ID，去重提交。纠错产生新输入，保留原记录。

AnswerService 使用 auto 分流，核验并提交后才发布正文、真实引用并启动 MiniMax speech-02-turbo。TTS 的 MP3 流在后端解码成 PCM，通过 LiveKit AudioSource 发布。插话/停止撤销 generation、取消任务、clear_queue、取消发布旧音轨；浏览器同步切断旧音轨，旧回调不得恢复。假打断不自动续播。TTS 错误保留已保存文字；重连不重播。

配置默认关闭；新增受控供应商 SecretStr 环境字段。原音频只在有界内存处理，不保存或记录正文。新依赖仅 voice extra，精确版本锁定；Silero 模型单独准备并校验 SHA256，不运行时下载。

## 按切片实施

- [x] 供应商二进制协议、流式 TTS 及本地 VAD；锁定依赖并完成协议/错误回归。
- [x] 会话租约、单控制端、去重、重启/维护/迟到及服务端文字门禁。
- [x] worker 实际接收和发布音轨，内部 AnswerService、generation 取消及实际队列清理。
- [x] React 唯一 VoiceController 会话/事件/字幕/回答/引用/纠错及媒体清理。
- [x] 隔离自动化、真实 WebRTC＋合成音频、三种宽度浏览器检查、CI/文档；证据见 [交付](M2-VOICE-VALIDATION.md)。
- [x] 一次公开合成真实 ASR/模型/TTS，经原文字面检索及浏览器播放；不包含真实 LightRAG 语义检索。
- [ ] 真人设备、质量及完整 AT-13/M2 验收；不能以一次合成供应商成功替代。

## 验收边界

替身、隔离数据库、真实 WebRTC+合成音频、真实供应商、真人设备分别记录。真实供应商的一次公开合成字面检索路径通过，真人听感、真实语义检索及完整 M2 待验收。M0 与完整 M1 结论保持原状态。
