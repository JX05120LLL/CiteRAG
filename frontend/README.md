# CiteRAG 前端

正式工作台使用 React、TypeScript、Vite、Ant Design 与 Ant Design X。`index.html` 是主入口，`voice.html` 是独立语音入口；`ui-preview.html` 是只读开发预览。界面调用同一套 API 客户端与控制器，语音媒体由唯一 VoiceController 管理。

## 页面与视觉边界

正式主题、字体、间距和断点维护在 [DESIGN.md](DESIGN.md)，界面范围索引见 [PRODUCT.md](PRODUCT.md)；公开产品范围仍以根目录 README 与 AGENTS 为准。

工作台、资料、持久任务、系统状态和两个语音入口共用 `src/react/theme.ts` 的组件主题与 `theme.css` 的导航、阅读尺度和页面边距；正式入口不依赖只读预览的历史样式。手机导航和聊天记录通过抽屉展开，原文、共享摘要和工具抽屉均限制在视口宽度内。资料与任务仍使用服务端分页，历史记录保留折叠入口，失败原因和修复操作来自现有服务状态。

页面保留原有布局与交互，统一采用墨绿侧栏、暖白页面、白色阅读面和浅柠绿选中项。暗侧栏及手机导航、聊天记录抽屉使用近白文字与浅灰绿说明；语音阶段区采用同一墨绿底与浅柠绿主操作。色板由 `theme.ts` 的 `palette` 维护，`ui-preview.html` 也复用共同主题，其样例不计为业务验收。

语音页使用按需加载的 Three.js / React Three Fiber 视觉组件，版本与许可见[第三方说明](vendor/three/README.md)。图形只读取已有 VoiceController 状态与本地麦克风音量；不申请权限、不创建房间、不生成字幕、不分析第二条音轨。播报状态表示服务端发送音频，不表示用户实际听见。关闭播放不会关闭麦克风输入，静音后输入视觉归零。

减少动态效果偏好、页面不可见、WebGL 不支持或渲染失败时使用静态矢量降级，文字状态与实际通话操作仍可用。切页或卸载释放图形资源；图形错误不挂断现有通话。语音、图片、引用、工具审批、归属和修订规则沿用已有 API 与控制器。打开页面不会自动开始通话。

## 启动

需要 Node.js 22.12+ 和 pnpm 10.33.0。在本目录运行：

```powershell
pnpm install --frozen-lockfile
pnpm dev
```

默认打开 http://127.0.0.1:5173/。Vite 只监听回环地址，端口被占用时退出；`/api` 同源代理至 http://127.0.0.1:8000/。后端无数据库时页面会显示实际缺项，不创建演示知识库或伪回答。后端启动、显式迁移和配置见[后端说明](../backend/README.md)。

## 代码边界

- `src/react/`：正式工作台、管理页、语音页、主题和视图控制。
- `src/api/`：与 FastAPI 的同源请求及 SSE 协议。
- `src/features/voice/`：LiveKit 控制、媒体生命周期和语音状态。
- `src/preview/`：只读预览，不参与业务数据验收。
- `../assets/brand/`：正式 Logo 与 favicon。

聊天类型、库绑定、维护、修订、图片归属、引用和重试由服务端最终校验；页面控件不能代替这些门禁。打开工作台或语音页不会自动申请麦克风或连接房间。所有真实凭证只能由后端加载。

进入系统状态页会自动检测已配置的模型、ASR 和 TTS，独立于问答和语音助手开关：模型至多一次 DashScope 请求，TTS 至多一次 MiniMax 请求，ASR 至多一次 MiniMax 合成及一次火山引擎识别请求。检测发送固定短提示或合成语音，可能产生供应商费用；按配置指纹缓存一小时，重复进入复用有效结果，“刷新系统状态”使用 `force=true` 重新检测，在途同项检测复用原任务。模型检测使用固定 `qwen-flash` 文字请求，不逐一验证图片模型、Embedding 或重排能力。知识检索目前保持 `not_checked`，不执行 LightRAG 检索。单项检测成功不代表真人通话或实际业务库验收通过。

普通聊天保留至手动删除；固定知识库聊天按最后一次用户输入（空聊天按创建时间）计算 180 天保留期。内置三项只读天气查询免逐次审批，调用仍会外发地点参数并可能计费；具体范围见[天气工具说明](../backend/QWEATHER.md)。

后端启用 Agent 后，发送/失败重试进入持久任务；任务卡片显示补充输入、精确参数审批、拒绝与取消。最近 20 条任务记录折叠，界面按 generation/事件序号拒绝迟到快照；刷新读取持久状态，不自动批准或重播旧音频。通话任务只在当前 VoiceController 控制端恢复，文字页面不能代为审批。关闭 Agent 时原文字 SSE 保留。协议、初始化及验证边界见 [Agent 说明](../backend/AGENT.md)。

## 检查

```powershell
pnpm test
pnpm typecheck
pnpm lint
pnpm build
node scripts/verify-entries.mjs
```

测试与构建通过不等于真实模型、真人设备或实际业务库验收。公开 CI 的检查范围见[工作流](../.github/workflows/ci.yml)；后端共用测试清单及隔离库要求见[后端说明](../backend/README.md#检查)。
