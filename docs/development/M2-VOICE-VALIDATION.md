# M2 语音实现与集中验收

提交补记（2026-09-27）：用户随后授权本次改动提交并推送 GitHub main。本文“工作区未提交/新远端CI未运行”等描述记录开发验收时状态；发布后以对应 Git 历史及 Actions 运行结果为准，不将 CI 当成真人或完整 M2 验收。

2026-09-27；基线 `615ec4df7a2ae09bf0039e65955fe0dcff2c7257`，main。当前收尾保留此前全部未提交 M2 工作，未 commit/push、部署或迁移实际业务库。用户已授权会话公共协议扩展，保持不改 schema；随后另行授权一次公开合成验证：火山 ASR 1 个流、现有模型最多8次请求、MiniMax TTS 1次且最多1500字。

**最新结果：用户配置 ASR/TTS 后，一次真实供应商语音闭环通过，实际 ASR1流 / 模型3次 / TTS1次（18字）。** 走隔离 PostgreSQL 原文字面检索，引用实际解析位置；不包含真实 LightRAG 语义检索或真人设备验收。收尾修复受控凭证加载、错误配置状态、原文下载导致断线；后端171项、前端221项、静态/构建检查通过。完整新截图、计数、失败记录、验证边界和集中步骤见 [最新真实供应商交付](M2-VOICE-REAL-SUPPLIERS.md)。下方早期166/219项、缺Key/0付费及已关闭服务描述保留为上一片历史快照，不代表最新状态。首次 M2 开发开始时工作区为空；本次接续时已含未提交工作。

## 实现链路

显式开始 → API owner/聊天/ready 库绑定 → 单控制端内存租约 → LiveKit RTC worker → 16k mono PCM → Silero ONNX → 火山 `bigmodel_async` 二进制流 → 临时字幕/稳定最终转写 → 原 AnswerService auto 分流、上下文、核验与持久提交 → 正文及真实来源 SSE → MiniMax `speech-02-turbo` MP3 流 → 有界 ID3 处理/解码 → LiveKit AudioSource → 浏览器音轨。

worker 在唯一 API owner 内运行；不是独立 Agents 服务，不创建 LightRAG 或聊天表。重启撤销内存会话并回收同安装标记的遗留房间，已有消息由原持久恢复逻辑处理。未知房间不删除。

租约 40 秒、前端每 10 秒续租，单会话最长 45 分钟；与 90 秒媒体 Token 分开。关页/断线/到期/维护/blocked/revision/active_workspace 改变会停止相关轮次。服务端文字发送、SSE 发送、重试均有 `409 voice_active` 门禁及事务内二次检查。固定绑定还在回答提交前复核。

插话/停止/挂断撤销 generation，实际 `clear_queue`、取消旧任务并取消发布旧音轨；浏览器暂停、置空 srcObject、移除音频元素。取消不等待旧模型成功；不能退出的任务保持门禁，明确清理失败并允许重试。假打断不续播。已保存回答才进入 TTS；TTS 失败不把文字改成未保存。failed/interrupted/partial 仍由文字工作台现有重试接口处理。重连读取历史，不重播历史音频。

中间字幕不进消息表。最终 session/utterance/revision 的 UUID5 只提交一次；过期、乱序与重复拒绝或忽略。纠错新建输入，原记录保留。语音页最近三条展开，早前已读取记录折叠；完整聊天仍在文字工作台。

## 控件对应

| 控件/事件 | 前端方法 | 后端或 RTC 方法 |
|---|---|---|
| 工作台语音入口 / 独立 voice.html | 同一 Voice 视图、VoiceController | 打开时只读 status、聊天和库；不申请麦克风或签发 Token |
| 刷新条件 | voiceStatus / conversation / knowledgeBases | GET /api/voice/status、原聊天/库接口 |
| 开始 | connect(chat, true) / voiceStart | POST /api/conversations/{id}/voice/sessions；Room.connect、setMicrophoneEnabled |
| 续租 / 媒体重连 | voiceRenew | POST /api/voice/sessions/{id}/renew；重连取消旧 generation |
| 字幕/回答/来源 | voiceEvents、receive | GET /api/voice/sessions/{id}/events，SSE；正文已提交 |
| 静音 | toggleMicrophone | setMicrophoneEnabled；仅麦克风输入 |
| 开/关声音 | toggleOutput | Room.startAudio、实际 audio.muted；不取消生成 |
| 停止回答 | stopAnswer、discardOutput | POST .../stop；任务取消、AudioSource.clear_queue、unpublish_track |
| 纠正转写 | correctTranscript | POST .../correction；原记录保留、新稳定输入 ID |
| 来源、下载 | displayCitations、originalUrl | 原 GET /api/documents/{id}/original，原归属/维护门禁 |
| 挂断、返回、切页、卸载 | hangup、disconnect | POST .../end；撤销房间；轨道/监听/分析器/元素释放 |
| 文字降级/重试 | 返回聊天、原 retryChat | 原 messages、messages/stream、retry；通话中禁止 |
| 添加图片 | 明确禁用 | 图片链路未实现，本轮不计图片验收 |

控制凭证只在标签页内存，经 `X-CiteRAG-Voice-Control` 请求头发送，不进 URL、storage、日志。旧 media token 接口仍兼容，不能替代正式助手会话。

## 自动化与实际浏览器证据

| 类别 | 结果与边界 |
|---|---|
| 公开后端全量 | 十文件 166 passed / 0 skipped，5条现有依赖弃用/合成短HMAC Key警告；真实隔离 PostgreSQL，供应商与检索替身。包括worker修订中止竞态修复；最后关闭墓碑解除worker/原音频队列引用后，两语音文件35项再次通过。 |
| 前端全量 | 12 文件 219 passed；长问题折叠及纠错竞态修复后再次全量测试，typecheck、扩展到 voice/API 的 lint、构建通过。Vite仍有大于500kB chunk提示，不作为错误或性能验收。 |
| 静态 | Ruff、compileall、锁文件及 diff 检查；最终结果以末次交付审核为准。 |
| 真实 RTC + 合成音频 | Chrome → LiveKit 1.13.7 → Python RTC/Silero → 本地 ASR 协议替身 → 真 AnswerService/业务持久表/原文定位 → TTS 协议替身 MP3 → 真 av 解码/RTC → Chrome 音频元素。没有真实供应商识别或真人听感。 |
| 真实供应商 | 最新已执行一次公开合成真实 ASR/模型/TTS 字面检索路径，实耗1/3/1，详细边界见上方最新记录；上一片因缺Key而0请求的结论是历史状态。 |
| 真人设备 | 未运行；使用 Chrome fake media + Windows 离线合成 WAV，输出为合成音调。 |

RTC 实测 ASR 替身收到首段 110592 bytes PCM；首个解码错误由带 ID3 的 MP3 复现，补 split-header 回归并修复后重测。后续 RTC 入站样本记录到 44348 bytes / 190 audio RTP packets，audio `paused=false` / active=true。点击停止在同一次浏览器操作内切断活动元素，5 秒后未恢复。另一次合成语音插话经真实 VAD，433ms 后无活动音频，约3914ms（新最终转写后）出现新播报。这是本机采样，不能作为真人插话延迟保证。

实际点通：主入口到语音、独立页开始、最终字幕、实际解析行号来源、原文下载、纠错新输入、TTS 返回错误后文字/来源保留、停止、麦克风静音/取消静音、声音关闭/开启、挂断、返回文字读取同一消息、来源抽屉 Escape 返回触发按钮。挂断后 Audio DOM=0、PeerConnection=closed。1440、390、320 均无横向溢出。433字纠错文本经真实纠错API显示，320px字幕高度179px；长问题标题可展开全文，避免无限撑高卡片。这证明布局与纠错接线，不是长语音识别准确率。

通话中停止本轮已核对归属的合成API，再启动：浏览器旧音频清空、旧PeerConnection关闭，持久文字仍可读取；新API的ASR/TTS计数均为0，显式新开始前不重播。短暂CDP离线仅验证浏览器受控故障场景，不等同真实网络ICE重连或真人设备验收。

一次全量复测误用了正在运行合成 API 的同一测试数据库，owner 门禁拒绝并产生失败；随后使用另一个全新回归库完整重跑，最终166项通过。不计第一次失败为通过。最后新增worker竞态测试首次缺本地ServiceError导入；修正后重跑完整166项通过。开发 HMR 期间观察到两条 React 更新深度警告；干净重载、来源/纠错与实时采集复测未复现。受控短离线还产生预期的资源网络错误。保留观察记录，不声称已定位该 HMR 问题。

最终审核修复两处并发边界：worker先于维护监控发现binding失效时，结束事件也必须屏蔽旧回答；纠错HTTP请求未完成时拒绝旧generation事件，同时接收已到达的新轮次，HTTP确认不得再次切断新音频。新回归覆盖这些行为，原文字partial重试守卫保留。

一次并行复测中，原StrictMode来源测试的默认1秒DOM等待超时；单独复跑通过。将该异步恢复断言改为有界5秒等待，原来源、焦点与禁止写请求断言保留。停止临时浏览器和媒体服务后，最终完整前端219项、typecheck、lint与构建重新通过；未把那次失败算作通过。

最后媒体对象引用释放复测前，专用PostgreSQL重启命令遗漏55447参数，默认端口绑定失败，6项数据库用例因此setup失败；未停止或修改系统5432服务。补回 `-o '-h 127.0.0.1 -p 55447'` 后，同组35项重跑通过，Ruff/compileall通过。关闭后的幂等墓碑不再持有worker、原始音频队列或转写映射，持久历史仍由原消息表保存。

### 可复制的自动验证命令

先创建独立、可丢弃的PostgreSQL测试库并显式设置 `CITERAG_TEST_DATABASE_URL`；不要使用业务API正在使用的数据库。本轮使用127.0.0.1:55447的新集群与独立 `citerag_m2_regression`。缺测试URL会skip，不能算全部通过。

```powershell
Set-Location D:\code\CiteRAG\backend
# 仅适用于本轮专用回归集群；其他环境替换为确认隔离的测试库
$env:CITERAG_TEST_DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:55447/citerag_m2_regression'
uv run --locked --extra rag --extra voice --no-env-file pytest -q tests/test_postgres_local.py tests/test_ingestion_jobs.py tests/test_ingestion_lifecycle.py tests/test_query_routing.py tests/test_m13_exact_api.py tests/test_m13_answer_api.py tests/test_m1_stream.py tests/test_m14_lifecycle.py tests/test_voice_transport.py tests/test_m2_voice.py
uv run --locked --extra rag --extra voice --no-env-file ruff check app migrations tests/test_postgres_local.py tests/test_ingestion_jobs.py tests/test_ingestion_lifecycle.py tests/test_query_routing.py tests/test_m13_exact_api.py tests/test_m13_answer_api.py tests/test_m1_stream.py tests/test_m14_lifecycle.py tests/test_voice_transport.py tests/test_m2_voice.py
uv run --locked --extra rag --extra voice --no-env-file python -m compileall -q app migrations
Set-Location D:\code\CiteRAG\frontend
pnpm test
pnpm typecheck
pnpm lint
pnpm build
Set-Location D:\code\CiteRAG
git diff --check
```

CI新增相同公开合成行为回归；不会安装或启动真人设备，也不会调用付费供应商。主入口、voice.html、ui-preview.html及其构建资源均实查存在；新文档本地链接、CI YAML三作业、UTF-8与公开文件边界检查通过。边界/Key模式检查不是穷尽密钥扫描。

隔离环境仅使用本轮新建目录和端口：PostgreSQL 55447，浏览器库 `citerag_m2_synthetic`，回归库 `citerag_m2_regression`；API 18027、ASR 替身18028；Vite5197；LiveKit17890/17891/17892。原5193、系统5432和其他未知进程未停止。LiveKit 信令为127.0.0.1，RTC TCP 实测监听 `::`、候选地址127.0.0.1；不宣称所有 RTC socket 只绑定回环。未改防火墙或部署。

## 截图（全部合成验证）

| 页面 | 桌面 | 手机 | 窄手机 |
|---|---|---|---|
| 正式工作台语音 | [1440](../../design/ui/exports/m2-workbench-voice-synthetic-1440.png) | [390](../../design/ui/exports/m2-workbench-voice-synthetic-390.png) | [320](../../design/ui/exports/m2-workbench-voice-synthetic-320.png) |
| 独立 voice.html | [1440](../../design/ui/exports/m2-voice-synthetic-1440.png) | [390](../../design/ui/exports/m2-voice-synthetic-390.png) | [320](../../design/ui/exports/m2-voice-synthetic-320.png) |

[实际原文来源抽屉390](../../design/ui/exports/m2-source-synthetic-390.png)。这些截图均来自公开合成资料与供应商替身，不代表真实业务验收。

## 依赖与官方依据

voice extra 精确版本：livekit-api1.2.1、livekit1.1.20（Apache-2.0），websockets16.0（BSD-3-Clause，17.x 与现有 google-genai 的 <17 约束冲突），onnxruntime1.30.0（MIT）、numpy2.5.3（BSD 等组合许可）、av18.1.0（BSD-3-Clause）。Silero v6.2.1 固定 ONNX/MIT，归属见 [许可](../../backend/vendor/silero/README.md)。不引入 torch、Agents SDK、官方 React 组件包或第二套 RAG。

PyAV 使用所安装 wheel 的 FFmpeg；若后续分发二进制，应保留 wheel 及其 FFmpeg/编解码器许可并核对构建选项，不能把 PyAV BSD 外推到所有打包库。本轮只通过锁文件安装，不发布二进制包。

协议依据：[火山官方 ASR](https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-automatic-speech-recognition-websocket?lang=zh)、[异步双工更新](https://www.volcengine.com/docs/6561/162929?lang=en)、[MiniMax HTTP TTS](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)、[LiveKit AudioSource 清理](https://docs.livekit.io/reference/python/livekit/rtc/audio_source.html)。页面布局许可继续见 [LiveKit UI](../../frontend/vendor/livekit/README.md)。

## 启动与关闭

先核对端口、PID及命令归属。不要在业务 API 使用中的库上运行 pytest、迁移或备份恢复。保持一个 API worker，不使用 --reload 测真实通话。

```powershell
Set-Location D:\code\CiteRAG\backend
uv sync --locked --extra rag --extra voice
Set-Location D:\code\CiteRAG\frontend
pnpm install --frozen-lockfile
```

准备固定 VAD（公开模型，无付费请求）：

```powershell
Set-Location D:\code\CiteRAG
New-Item -ItemType Directory -Path .local\voice -Force | Out-Null
Invoke-WebRequest 'https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx' -OutFile .local\voice\silero_vad.onnx
if ((Get-FileHash .local\voice\silero_vad.onnx -Algorithm SHA256).Hash.ToLower() -ne '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3') { throw 'VAD checksum mismatch' }
```

实际语音验收先取得对应数据与付费授权；由受控后端加载原业务/模型/LiveKit配置，并注入下面字段，不在终端打印或提交真实值。新增字段不会迁移旧库。默认 voice_assistant=false，answer/ingestion=false；页面打开不调用供应商。

| 环境配置 | 含义 |
|---|---|
| CITERAG_VOICE_TRANSPORT_ENABLED=true | 本地媒体 SDK |
| CITERAG_VOICE_ASSISTANT_ENABLED=true | 正式助手；默认 false |
| CITERAG_ANSWER_ENABLED=true | 复用现有问答；入库仍按独立授权 |
| CITERAG_VOICE_ASR_KEY | 火山新 X-Api-Key；仅后端 SecretStr。显式启用助手且环境未设置时读取已有 DPAPI 火山记录 |
| CITERAG_VOICE_ASR_APP_KEY | 可选；有值时 ASR_KEY 改为旧 Access-Key，不能混淆两套凭证 |
| CITERAG_VOICE_ASR_RESOURCE | 默认 volc.seedasr.sauc.duration，须核对账户开通的资源 |
| CITERAG_VOICE_TTS_KEY / CITERAG_VOICE_TTS_VOICE | MiniMax；默认 male-qn-qingse，模型固定 speech-02-turbo。显式启用助手且环境 Key 未设置时读取已有 DPAPI MiniMax 记录 |
| CITERAG_VOICE_VAD_MODEL | D:\code\CiteRAG\.local\voice\silero_vad.onnx |
| CITERAG_LIVEKIT_URL / API_KEY / API_SECRET | 本机 ws://127.0.0.1:7880 及受控服务配置 |
| CITERAG_ALLOWED_ORIGINS | 前端实际回环 Origin，端口必须一致 |

已保存记录固定为 `.local/runtime/models/volcengine.credential.xml` 与 `minimax.credential.xml`，通过既有 ACL/DPAPI 加载器受控读取，不能改为明文配置。默认关闭时不读取，环境 Key 优先，缺失或损坏仍未配置；不加载 `.env`。本次两个记录已确认可加载，不打印真实值。

例如在已核对空闲 7880/7881/7882、8000、5173，确认既有业务库 schema 无需迁移，取得本次真人/数据/费用授权并准备模型、LiveKit 受控配置后，三个专用终端分别执行：

```powershell
# 终端1：自托管服务，配置步骤见 M2-LIVEKIT-TRANSPORT.md；不覆盖既有服务配置
& 'D:\code\CiteRAG\.local\voice-transport-20260926\server\bin\livekit-server.exe' --config 'D:\code\CiteRAG\.local\voice\livekit.yaml'
# 终端2：受控环境配置已加载后
Set-Location D:\code\CiteRAG\backend
$env:CITERAG_VOICE_TRANSPORT_ENABLED='true'
$env:CITERAG_VOICE_ASSISTANT_ENABLED='true'
$env:CITERAG_ANSWER_ENABLED='true'
$env:CITERAG_INGESTION_ENABLED='false'
$env:CITERAG_VOICE_VAD_MODEL='D:\code\CiteRAG\.local\voice\silero_vad.onnx'
$env:CITERAG_ALLOWED_ORIGINS='http://127.0.0.1:5173'
uv run --locked --extra rag --extra voice --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
# 终端3
Set-Location D:\code\CiteRAG\frontend
pnpm dev --host 127.0.0.1 --port 5173 --strictPort
```

从文字工作台选择 ready 库和聊天进入语音；或复制当前聊天 UUID 到 `http://127.0.0.1:5173/voice.html?conversation=UUID`。不要使用本轮合成替身服务作为真实语音验收。

关闭：先页面挂断确认结束，再 Ctrl+C 关闭自己启动的 API（清理 worker/房间）、前端和 LiveKit。后台服务只能在确认 PID/命令/专用目录后关闭。数据库按原受控维护步骤关闭，不停未知 PostgreSQL。失败撤销时保持文字门禁，重试挂断或检查 LiveKit 后重启 API。

自托管 LiveKit 不提供应用级 Token 立即吊销保证：删房间可停止当前连接，但尚未到期的签名 Token 可能重新建立一个无助手空房间。应用会话/租约已失效，不允许新 ASR/问答/TTS；回收和空房间超时覆盖遗留房间。不能把 Token 到期写成通话有效期。

## 集中人工验收与待确认

1. 本轮一次付费验证已完成，实耗ASR1流/模型3次/TTS1次，两份凭证已确认可加载。ASR/TTS次数已用完，不删除登记或重复调用。持续真人/私人资料调用需要另行确定数据与费用范围；本次局部成功不代表完整验收。具体结果、启动边界及步骤见 [最新记录](M2-VOICE-REAL-SUPPLIERS.md)。
2. 真人麦克风允许/拒绝/无设备、扬声器、自动播放权限：未验证，缺真人硬件和授权。预期拒绝可执行提示，挂断/切页/卸载停止采集。
3. AT-13：说一段话，中间字幕不入历史、最终一次入历史；资料回答核验后出现来源并播报。播报中插话和停止，旧音频不能回来；假打断不会续播；纠错创建新输入；TTS 失败保留可读文字；断网和 API 重启后恢复持久文字、不重播。当前仅工程与合成媒体证据，不标完整 AT-13 通过。
4. AT-14 的文字门禁：通话中直接请求文字/SSE/重试应409 voice_active，挂断后可文字继续；隔离测试通过。图片仍明确禁用，图片归属与识别不计本轮通过。
5. 用专门授权的真实库验证普通交流、通用知识、资料查询分流、来源正确、纠错和维护后的屏蔽。真实 ASR准确率、回答质量、TTS听感、噪声/回声、真人插话延迟、后台标签页长时间续租均待人工。M0 Embedding 边界和完整 M1 仍未通过。
6. 当前工作区未提交，因此新远端 CI 未运行；仅原615ec4d的 CI #36310360483 本轮重新查到成功，不能证明新代码。没有待确认 schema/迁移；剩余为超出本次范围的真实数据/真人调用、语义检索与质量判断，后续提交/推送另行授权。

## 最终审核与本机状态

已审核后端租约/控制授权、维护与事务提交门禁、真实媒体队列清理、供应商协议边界和错误脱敏；前端纠错/迟到权限/事件 generation、历史恢复和 React 卸载；测试、CI、依赖锁与许可、文档及合成截图。`git diff --check`、新文档链接、构建入口资源及公开路径边界检查通过。未加入 `.local/`、运行数据、凭证或私人资料；这些路径继续忽略。本轮不改数据库schema或迁移文件。

Git仍为main、HEAD `615ec4df7a2ae09bf0039e65955fe0dcff2c7257`，暂存为空；改动留在工作区和新增文件中，未commit/push。隔离浏览器、API/ASR替身18027/18028、Vite5197、LiveKit17890/17891/17892、专用PostgreSQL55447已在核对PID/配置/目录后关闭，专用库无其他客户端时才关闭；本地证据和合成库目录保留。系统5432 PID8716未停止，初始未知5193进程未执行停止操作。下次先核对当前端口再启动，不能沿用历史服务结论。
