# CiteRAG 前端

正式工作台使用 React、TypeScript、Vite、Ant Design 与 Ant Design X。`index.html` 是主入口，`voice.html` 是独立语音入口；`ui-preview.html` 是只读开发预览。界面调用同一套 API 客户端与控制器，语音媒体由唯一 VoiceController 管理。

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
