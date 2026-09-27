# B 版 React / Ant Design UI 交付与验证

日期：2026-09-27。用户选定 **B · 对话优先知识助手**。正式 `index.html`、独立 `voice.html` 已切换 React；本轮 A/C 候选代码、截图与链接已移除。历史设计资产与业务逻辑保留。UI 完成不表示 M0、完整 M1 或 M2 验收通过。

后续确认：用户已确认本次 UI 升级交付完成并授权本地提交，未授权推送。下方“未 stage/commit/push”与 HEAD、端口是开发交付时的快照；本次确认不新增实际业务、模型或媒体测试通过结论。提交前重新执行前端测试、类型检查、lint、构建、入口/文档资源与暂存文件审核，结果以该次实际输出为准。

提交前复核（2026-09-27）：212 项 / 12 文件测试通过（22.39s），typecheck、lint、build、三个入口资源、165 个文档目标与 65 个截图引用检查通过；保留构建大块警告。提交范围为 B 前端、锁定依赖、公开合成回归、CI、文档与合成截图；路径边界与常见凭证特征检查通过，不等同完整密钥扫描。没有新业务数据操作、模型请求、媒体连接或远端 CI 结果。

## 查看与边界

- [启动与查看](../../design/ui/react-candidates/README.md)、[设计说明](../../design/ui/react-candidates/DESIGN.md)。
- [桌面 / 手机 / 320px 截图和前后对照](../../design/ui/react-candidates/index.html)。
- 接手：`main` / `fbfc8d32738f60127502e0586da29d4be749ea60`，暂存与未提交改动为空。未 stage、commit、push 或部署。
- 使用确认空闲的 Vite 5193；未停止其他服务。API 代理仍指向 8000。本轮 API 未运行，未改变配置、业务库或后端进程。
- 没有 schema 变更、业务库迁移/清空、真实原文读取或上传/删除/重建、模型/ASR/TTS 付费调用、凭证读取。
- 正式界面写操作接现有方法；只读预览禁用写操作与媒体连接。合成设计和请求替身验证均不计作真实业务验收。

## 实现结构

`src/react/App.tsx` 负责 ConfigProvider / XProvider、B 布局、中文、导航、弹窗与挂载；Workbench、Management 按页面延迟加载。主题集中于 `theme.ts` 与少量 CSS。82px 桌面导航、居中聊天、按需历史/来源抽屉；手机转为导航抽屉和单列资料卡。

`app.ts` 与 `DocumentsPanel` 暴露快照、业务方法和释放入口，React 消费同一业务状态，复用 API/SSE、固定库、本地归属、revision/active_workspace、幂等和维护门禁。原生渲染适配器保留供既有回归和兼容调用；正式入口不挂载旧视图，不建立第二套业务或媒体状态。

StrictMode effect 在发读取前登记清理，卸载中止请求并释放控制器；旧代际结果不进入新视图。资料操作弹窗关闭后再次检查身份/忙碌/门禁。来源用已保存、已核验、未遮蔽/失效的实际引用，通用与普通交流无引用；运行中和未提交正文不提前发布。failed/interrupted/partial 均沿用同消息重试。

## 锁定依赖与兼容性

| 用途 | 精确版本 |
| --- | --- |
| React / ReactDOM / 对应类型 | 19.3.0 |
| Ant Design / Ant Design X / Icons | 6.6.5 / 2.9.0 / 6.3.4 |
| Vite / React 插件 | 7.3.6 / 5.2.0 |
| TypeScript / pnpm | 5.9.3 / 10.33.0 |
| LiveKit 客户端（沿用） | 2.22.3 |
| Testing Library React / DOM | 16.3.3 / 10.4.2 |
| ESLint / @eslint/js / typescript-eslint | 10.11.0 / 10.0.1 / 8.70.1 |
| Vitest / jsdom（沿用） | 4.1.11 / 27.4.0 |

安装时核对稳定版本与 peerDependencies，package.json 精确固定并锁入 pnpm-lock.yaml。Node >=22.12.0，CI Node 24.14.0。未安装 LiveKit React 组件包，不引入 Next.js、Tailwind 或独立 Agents 后端。

三个构建入口资源检查通过。按页面拆分后 Workbench 约 5.74 KB、Management 26.97 KB；共享组件块 862.15 KB（gzip 280.31 KB），LiveKit SDK 562.10 KB（gzip 148.01 KB）。Vite 大块警告仍在，不代表已完成加载性能验收。

## 控件—前端方法—API / LiveKit 对应

表中为正式入口接线；只读预览仅允许 GET。接线和合成替身通过不等于真实 API 成功。

| 控件 | 前端方法 / 控制器 | 现有接口 / SDK |
| --- | --- | --- |
| 知识库查看、选择 | selectKb、knowledgeBases | GET /api/knowledge-bases |
| 新建、改名知识库 | actions.createKnowledgeBase / renameKnowledgeBase → 同名 API 方法 | POST /api/knowledge-bases；PATCH /api/knowledge-bases/{id} |
| 创建、选择、加载更多聊天 | createChat、selectChat、loadMoreChats | POST /api/conversations；GET /api/conversations?limit=20&offset={n}；GET /api/conversations/{id}/messages |
| 改名 / 删除聊天 | renameChat / deleteChat | PATCH / DELETE /api/conversations/{id} |
| 单输入发送 / 自动路由 | sendChat → askMessageStream | POST /api/conversations/{id}/messages/stream（mode=auto） |
| failed / interrupted / partial 重试 | retryChat → retryAnswer | POST /api/conversations/{id}/messages/{messageId}/retry |
| 来源展开 / 关闭 | displayCitations、SourceDrawer | 实际消息 citations；不新增或伪造来源 |
| 合格来源 / 就绪资料下载 | originalUrl | GET /api/documents/{id}/original |
| 当前 / 删除记录真分页 | DocumentsPanel.movePage / refresh → documentPage | GET /api/knowledge-bases/{id}/documents?scope=current或deleted&limit=10&offset={n} |
| 上传受理 / 同键恢复 | DocumentsPanel.submit → uploadDocuments | POST /api/knowledge-bases/{id}/documents |
| 失败资料删除 / 替换 | DocumentsPanel.maintain → deleteDocument / replaceDocument | POST /api/documents/{id}/delete；POST /api/documents/{id}/replacement |
| 属性确认 | DocumentsPanel.saveAttributes → updateDocumentAttributes | PATCH /api/documents/{id}/attributes |
| 查看解析位置 | DocumentsPanel.inspect → blocks | GET /api/documents/{id}/blocks |
| 影响确认 / 重建 | DocumentsPanel.submit → rebuild | POST /api/knowledge-bases/{id}/rebuild |
| 活动 / 历史任务分页 | DocumentsPanel.refresh / movePage → jobPage | GET /api/knowledge-bases/{id}/jobs?scope=history&limit=10&offset={n} |
| 详情 / 刷新 | DocumentsPanel.inspectTask → job | GET /api/jobs/{id} |
| 任务重试 / 清理 | DocumentsPanel.retry / cleanup → retryJob / cleanupJob | POST /api/jobs/{id}/retry；POST /api/jobs/{id}/cleanup |
| 系统状态刷新 | loadHealth → health | GET /api/status |
| 媒体条件刷新 | loadVoiceCapabilities / VoicePage.refresh → voiceStatus | GET /api/voice/status |
| 显式开始连接 | VoiceController.connect → voiceToken / createMediaRoom | POST /api/conversations/{id}/voice/token；Room.connect |
| 麦克风 / 静音 | VoiceController.toggleMicrophone | localParticipant.setMicrophoneEnabled |
| 播放开关 | VoiceController.toggleOutput | Room.startAudio；实际远端 audio.muted |
| 实际音量 | createMediaRoom.onLevels | 实际音轨 / Web Audio 分析器采样 |
| 挂断 / 切页 / 卸载 / 异常 | VoiceController.hangup / dispose；MediaRoom.disconnect | stop 音轨、detach 元素、释放分析器和监听、Room.disconnect |
| 返回文字 / 系统状态 | navigate；独立页 back / status | 本地页状态或既有独立入口 |
| 图片 / ASR / TTS / 字幕 / 语音回答 | 无可用业务方法 | 明确禁用，当前未接入 |

删除记录和历史保留原事实，分页调用真实接口 offset，非对已加载结果切片。恢复不生成新成功事实；重建/替换等影响与确认、blocked/维护/任务忙碌门禁继续有效。控制器幂等键及丢响应恢复语义保留。

## 官方 LiveKit 融合

采用固定 MIT 上游的欢迎中心布局、静态几何图形和会话控制区组织，以 React / Ant Design 与 B 主题重写视图；来源和许可见 [vendor 记录](../../frontend/vendor/livekit/README.md)。这不是直接运行官方示例或安装官方 React 组件套件。

工作台与独立语音入口共用 React Voice 和现有 VoiceController，唯一媒体生命周期。打开页面仅 GET 条件，不自动申请麦克风、发 Token、连接或调用模型。只有用户按钮触发 connect；挂断、返回、切页、卸载和 pagehide 释放媒体，迟到 Token/权限/音轨沿用既有代际清理。

当前仅媒体测试；ASR、语音知识库回答、TTS、字幕未接入，界面清楚标明。欢迎五柱静态装饰，连接后仅实际采样改变高度，分析不可用时提示。只读设计的连接状态显式标为合成，控件全部禁用。

## 本机验证记录

2026-09-27，在 frontend 执行，浏览器为真实 Chromium；资料/回答/状态均明确合成：

| 命令 / 检查 | 结果与范围 |
| --- | --- |
| pnpm install --frozen-lockfile --offline | 通过，锁文件无漂移 |
| pnpm test | 通过：原有 178 项、只读预览 23 项、React 11 项，合计 212 项 / 12 文件；最终全量 22.65s |
| pnpm typecheck | 通过 |
| pnpm lint | 通过；新 React 产品与只读预览，未声称 lint 所有旧代码 |
| pnpm build | 通过，保留大块警告 |
| node scripts/verify-entries.mjs | index / voice / ui-preview HTML、JS、CSS、Logo 资源通过 |
| migration-browser.js | 正式入口 33 个页面/尺寸截图 + 14 条交互，1440/390/320px；无横向溢出，输入边界对齐，页面异常 0，媒体申请 0 |
| preview-browser.js | B 只读 47 个页面/状态/尺寸，无横向溢出；API 请求、写操作、麦克风申请、页面异常均为 0 |
| preview-interactions.js | 通过 7 组：来源键盘/焦点、无引用交流、partial、门禁、任务分页、刷新、安全 API 失败和正式入口挂载；无写操作或页面异常 |
| node scripts/verify-preview-docs.mjs | 文档、截图与离线目录链接通过 |
| preview-gallery.js | file:// 1440/390/320px 通过，主图加载且无横向溢出 |
| git diff --check（根目录） | 通过 |

[正式入口原始记录](../../design/ui/react-candidates/exports/migration-checks.json)、[只读矩阵记录](../../design/ui/react-candidates/exports/browser-checks.json)、[交互记录](../../design/ui/react-candidates/exports/interaction-checks.json)、[截图目录](../../design/ui/react-candidates/index.html)。

实际点通的 14 条正式界面路径：聊天改名；单输入 auto 路由 + SSE 保存；知识库创建；失败资料删除；勾选确认重建；文件选择与上传受理；资料属性确认；替换影响确认；解析块读取；任务重试；任务后端分页；知识库改名；聊天创建；确认删除聊天。上述请求在浏览器隔离拦截，由合成响应验证前端协议，**没有真实业务写入、模型或实际资料读取**。

行为测试包含 failed/interrupted/partial 的同消息重试、未提交正文/引用隐藏、后端 offset、StrictMode 迟到读取与卸载、独立语音不自动连接及显式 fake-room 连接/清理。原有媒体控制器回归继续覆盖迟到音轨、权限、挂断等边界；fake-room 不是 WebRTC。

本轮修复并复测：手机导航被共享样式隐藏、来源快速关闭焦点丢失、未提交回答误标保存、资料文本挤压、模态确认关闭/重新核对门禁、上传受理与真正完成混淆。最终审核另复现并修复跨聊天知识库切页仍显示旧任务范围、关闭详情取消列表刷新导致加载不结束，两项均先复现失败再补回归通过。手机全页截图等待导航关闭并回到顶部，避免动画影响。新回归曾因语音标签与标题相同产生选择器歧义，改为按标题定位；不删媒体生命周期断言。

CI 增加 React 公开合成测试白名单、产品 lint、三个入口门禁，锁定依赖；后端原临时 PostgreSQL/模型关闭流程保留。**未运行新远端 CI**。本轮后端未改，不重新执行真实数据库或付费链路。

## 集中查看与未验证项

1. 无业务服务也可打开 B 只读设计，依次看聊天/来源、资料/失败、任务/历史、状态、语音六阶段；比较 1440/390/320px。
2. 正式主入口用于既有 API，离线应展示安全失败，不能回退到合成成功。启动后端需另按原项目环境流程；本轮不替你迁移或改配置。
3. 对已有授权的隔离资料，手动依次新建聊天/改名/删除、发送问候/通用问题/资料问题、重试、下载来源与对照定位、上传/替换/删除/重建、分页和任务详情。
4. 语音入口先选择归属聊天和 ready 库，刷新条件；显式连接后检查静音/播放/挂断/切页/独立入口与返回。

| 未验证项 | 步骤与预期 | 原因 |
| --- | --- | --- |
| 实际 API、真实资料及原文 | 启动既有环境，读取真实状态、核对来源与定位；成功只按后端返回 | 本轮 API 离线，禁止读取私人资料或为截图改资料 |
| 真实模型分流/引用/入库 | 另获授权后使用隔离资料测试实际供应商链路 | 本轮禁止新增付费请求，替身不能算真实模型验收 |
| 真 WebRTC / 合成音频 | 独立本地 LiveKit 环境显式连接，检查轨道/挂断/迟到清理 | 本轮未启动媒体服务或请求 Token，未复用旧验证冒充本轮 |
| 真人设备 / 听感 / 真权限拒绝 | 允许/拒绝麦克风，拔插设备，开关播放，听感核对；离开后无采集 | 需要人工设备与权限操作 |
| ASR / TTS / 语音助手 | 应保持未配置/禁用且不生成字幕回答 | 后端尚未实现，不因 UI 扩范围 |
| 加载性能与大块 | 缓存为空、限速网络检查首屏及延迟页面 | 构建警告保留，未做性能基准 |
| 新远端 CI | 将来授权提交/推送后检查相应 commit Actions | 当前禁止 commit/push |

## 最终审核范围

保留原业务/API/SSE、任务和媒体控制器，只增加视图适配、清理入口与异步弹窗接口。原生兼容适配器保留供既有调用/回归，正式 React 页面只使用快照。仅本轮 A/C 生成内容被删除，旧历史 A 设计资产保留。未读取、添加或修改 .local、备份或实际业务资料。

收尾 Git 为 main / fbfc8d32738f60127502e0586da29d4be749ea60，暂存 0，改动均留工作区。tracked 与可见 untracked 文件按 CI 路径边界检查通过；A/C 截图缺席、partial/failed/interrupted 重试守卫保留。路径白名单检查不是完整密钥扫描。没有后端/API 客户端/SSE/媒体 SDK 的改动；未 commit/push，未运行新远端 CI。

收尾端口检查：127.0.0.1:5193 为本轮 Vite（node PID 25108）；8000/5173 未监听。保留前端服务供查看，没有停止未知服务。正式界面此时无法读取实际 API，只读 B 可完整看外观。

## 官方参考

- [Ant Design 主题](https://ant.design/docs/react/customize-theme)、[ConfigProvider](https://ant.design/components/config-provider)：共享 tokens、中文和组件层级。
- [Ant Design X Sender](https://x.ant.design/components/sender/)、[Bubble](https://x.ant.design/components/bubble/)、[Conversations](https://x.ant.design/components/conversations/)：仅 UI，不替换模型请求/SSE。
- [LiveKit starter 固定源](https://github.com/livekit-examples/agent-starter-react/tree/c5d78a6c381a0ac80b081cf6aeb8ac454d00ca78)、[显式 RoomContext 生命周期](https://docs.livekit.io/reference/components/react/concepts/livekit-room-component/)：采用布局交互，媒体沿用项目单控制器。
- [React StrictMode](https://react.dev/reference/react/StrictMode)：开发重复 setup/cleanup 不是自动连接授权。
