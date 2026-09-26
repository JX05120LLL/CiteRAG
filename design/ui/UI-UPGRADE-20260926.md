# 三页 UI 升级交付 · 2026-09-26

## 设计与实现

用户确认的三张原图已保存：[工作台](concepts/2026-09-26/workbench.png) · [知识库](concepts/2026-09-26/knowledge.png) · [语音通话](concepts/2026-09-26/voice.png)。这些是概念稿，里面的资料、来源与通话状态均不是业务验收结果。

本轮继续使用原生 TypeScript、HTML/CSS 和 Vite。采用同一品牌与侧栏、居中的新聊天输入区、紧凑资料行、按需打开的详情栏和手机导航抽屉。`frontend/src/ui-upgrade.css` 覆盖布局，原有 `styles.css` 和 API 客户端保持本轮开始时的内容。

- 工作台：新聊天首页、实际最近聊天、输入框内知识库与能力入口；回答旁保留简短来源卡，点击后展开原文片段与定位。维护或隐藏的回答不能打开来源。固定知识库、自动路由、SSE、partial 重试与恢复键保留。
- 知识库：资料、持久任务和已删除记录有分区入口。上传、属性确认、高级维护折叠；当前资料和历史继续调用原有每页 10 条的 API。失败详情显示安全错误代码、原因和关联任务，任务详情独立读取服务；进行中任务显示当前阶段，不推算百分比或剩余时间。
- 通话：独立页面、返回聊天、固定知识库上下文、连接/静音/停止播放/字幕/挂断布局。当前后端没有通话接口，所有通话操作禁用并说明原因；没有访问麦克风或显示模拟字幕、波形、计时及已接通状态。
- 手机：侧栏改抽屉，选择聊天后自动收起；来源和任务详情改为可关闭的底部面板。Escape 可关闭导航或详情。

与概念稿的差异：没有提供全库搜索/状态筛选接口，因此不摆放无效的搜索控件；处理任务入口打开当前查看的库，不冒充全局任务汇总；没有后端累计进度信息，不展示耗时预测。图片入口明确禁用。管理页的黑色主按钮是“返回工作台”，不会暗中创建聊天。

## 控件与 API 对应

| 控件 | 前端方法 / 后端接口 | 约束 |
|---|---|---|
| 侧栏知识库、管理列表、刷新 | `knowledgeBases` → `GET /api/knowledge-bases` | 状态点来自返回值 |
| 创建 / 改名知识库 | `createKnowledgeBase` / `renameKnowledgeBase` → `POST /api/knowledge-bases`、`PATCH /api/knowledge-bases/{id}` | 保留容量、恢复键与保存中门禁 |
| 最近聊天、历史聊天、更多 | `conversations` / `conversationMessages` → `GET /api/conversations`、`GET /api/conversations/{id}/messages` | 保留分页及固定知识库 |
| 新建 / 改名 / 删除聊天 | `createConversation` / `renameConversation` / `deleteConversation` → `POST /api/conversations`、`PATCH/DELETE /api/conversations/{id}` | 删除仍需原有确认 |
| 发送文字问题 | `askMessageStream` → `POST /api/conversations/{id}/messages/stream` | 单输入框，`mode=auto`，沿用 SSE 状态 |
| 重试失败或 partial 回答 | `retryAnswer` → `POST /api/conversations/{id}/messages/{messageId}/retry` | 保留 `failed`、`interrupted`、`partial` 守卫 |
| 来源卡 / 上一条 / 下一条 / 其他来源 | `selectCitation`、`renderSources` | 展示已保存回答中的 API 引用，不新增模型调用 |
| 下载原文核对 / 下载资料 | `originalUrl` → `GET /api/documents/{id}/original` | 维护期间暂停；实际文件下载未在本轮验收通过 |
| 处理任务入口、资料分页与历史 | `documentPage` / `jobPage` → `GET /api/knowledge-bases/{id}/documents`、`GET /api/knowledge-bases/{id}/jobs` | `scope`、`limit=10`、`offset`；沿用已删除/已结束折叠 |
| 查看失败原因 | 资料列表中的 `error_code`，关联任务来自已读取任务列表 | 仅展示安全映射和符合格式的错误代码，不回显原始异常 |
| 关联任务 / 任务详情 / 刷新详情 | `job` → `GET /api/jobs/{id}` | 不把列表快照当成新的单任务读取 |
| 查看解析位置 | `blocks` → `GET /api/documents/{id}/blocks` | 资料或库状态不允许时禁用并说明原因 |
| 添加资料 / 上传 | `uploadDocuments` → `POST /api/knowledge-bases/{id}/documents` | 受理不等于入库；保留原有格式、大小、并发及恢复门禁 |
| 删除失败资料 / 删除就绪资料 / 替换 | `deleteDocument` / `replaceDocument` → `POST /api/documents/{id}/delete`、`POST /api/documents/{id}/replacement` | 保留维护限制与原有确认；历史标记继续保留 |
| 编号与版本确认 | `updateDocumentAttributes` → `PATCH /api/documents/{id}/attributes` | 仅就绪资料和就绪库可改 |
| 重试任务 / 清理 / 从原文重建 | `retryJob` / `cleanupJob` / `rebuild` → `POST /api/jobs/{id}/retry`、`POST /api/jobs/{id}/cleanup`、`POST /api/knowledge-bases/{id}/rebuild` | 高级维护默认折叠，保留确认与核验要求 |
| 语音页 / 返回聊天 / 手机导航 / 详情关闭 | `navigate` 或本地面板状态 | 本地界面切换，不生成虚假服务状态 |
| 连接 / 静音 / 停止播放 / 字幕 / 挂断 / 添加图片 | 禁用 | 无对应通话、ASR/TTS 或图片问答接口 |
| 系统状态 / 刷新 | `health` → `GET /api/status` | 只读，不测试模型在线状态 |

## 浏览器截图

截图为真实 Chromium 中的页面渲染，使用标明“合成UI验证”的隔离响应。合成响应只位于本机浏览器验证脚本，未加入运行时产品。它们证明布局与客户端接线，不证明真实 LightRAG、实际业务库或模型回答质量。

| 页面 | 桌面 1440×1000 | 手机 390×844 |
|---|---|---|
| 工作台首页 | [桌面](exports/ui-upgrade-home-1440.png) | [手机](exports/ui-upgrade-home-390.png) |
| 回答与来源核查 | [桌面](exports/ui-upgrade-sources-1440.png) | [手机](exports/ui-upgrade-sources-390.png) |
| 独立语音页，未接入状态 | [桌面](exports/ui-upgrade-voice-1440.png) | [手机](exports/ui-upgrade-voice-390.png) |
| 资料列表 | [桌面](exports/ui-upgrade-documents-1440.png) | [手机](exports/ui-upgrade-documents-390.png) |
| 失败处理详情 | [桌面](exports/ui-upgrade-knowledge-1440.png) | [手机](exports/ui-upgrade-knowledge-390.png) |
| 持久任务详情 | [桌面](exports/ui-upgrade-task-1440.png) | [手机](exports/ui-upgrade-task-390.png) |

## 验证结果

- `pnpm test`：6 文件、146 项通过。包含来源门禁、未接入语音、手机导航收起、当前库任务入口、失败详情与 Escape 关闭、安全错误信息；保留原有 SSE、partial、分页、上传和维护恢复用例。
- `pnpm typecheck`、`pnpm build`：通过。
- [浏览器检查记录](exports/ui-upgrade-checks.json)：23 项布局/行为记录，最后一轮 33 个隔离 API 响应、无页面异常、无供应商调用。1440px/390px 检查首页、来源、语音、资料和详情；资料另检查 320px、820px、1240px，状态页检查 1240px 和 390px。所检容器无横向溢出，桌面详情未遮挡列表操作。
- 本轮浏览器实际点通（合成响应）：选库、打开聊天、打开/关闭来源、partial 重试、单输入框发送 `mode=auto` SSE、语音页与返回聊天、资料下一页/上一页、已删除与已结束历史展开、失败详情、任务详情、手机导航遮罩关闭与选聊天收起、系统状态读取。
- 原文下载尝试在 CLI 中返回 `canceled`，没有获得可核对的文件；只确认 URL 接线，未标为下载通过。其他资料写操作由前端测试覆盖，本轮未在真实业务库执行。
- 本轮基线对比：没有删除前端文件，`retryChat` 函数内容、API 客户端和原样式文件均保持本轮开始时的内容。后端改动和已有开发文档属于开始时的未提交工作，本轮没有修改它们。

已修复并复查：详情侧栏遮挡操作、手机导航不收起、选中聊天文字对比度、输入区按钮间距、窄屏资料行，以及不同知识库之间的任务入口和两个详情面板叠放问题。

## 集中验收与未验证项

1. 在已有 Vite 开发页刷新（本轮核对的地址为 `http://127.0.0.1:5173`），浏览器若保留当前聊天，可直接对照聊天来源；新聊天首页需创建或打开聊天后才可发送问题。观察当前真实 API 返回的库状态，不以截图中的合成状态为准。
2. 检查知识库列表和“资料与任务”；展开添加资料、编号与版本、高级维护，确认已有功能可找到。对已有超过 10 条资料的库翻页，展开已删除记录与已结束任务。历史记录不会因这次布局升级而物理删除。
3. 点击真实失败资料的“查看失败原因”，核对错误代码、说明及关联任务；打开单任务详情后刷新。任务有进行中记录时，维护操作应受限，不能出现假完成百分比。
4. 在就绪知识库打开已有已核验回答，点击来源卡，核对片段与页/行/段/表定位，再实际下载原文核对。文件下载是本轮未通过的人工验证项。
5. 进入语音页：应明确“语音服务尚未接入”，连接、静音、播报、字幕与挂断均不可用。图片入口也不可用。此页只是完成布局，真实通话和图片问答尚未实现。
6. 将窗口调到手机宽度，检查导航、输入区、资料行和详情底部面板；点击关闭或按 Escape。返回聊天应保留原聊天与固定知识库。

本轮未验证：真实模型问答、LightRAG 入库/核验、真实文件下载、真实业务资料上传/删除/替换/重建、真实麦克风与多模态服务。仅 UI 升级获本轮授权，未迁移数据库、改 schema、部署、commit 或 push；旧服务未停止或重启。完整 M0/M1 的验收结论不因这次 UI 结果改变。

## 后续提交前检查 · 2026-09-26

用户随后授权更新 README 并提交仓库。提交范围包含上述 UI、公开概念图与截图，以及工作区已有的相关后端分页、Unicode 切分、失败状态和删除恢复修复；不包含本机配置、运行数据或私人资料。

- 前端重新运行：146 项测试、类型检查、生产构建通过。
- 后端公开八文件回归：27 项通过、73 项跳过；跳过项均需要显式配置独立 PostgreSQL 测试库。本次清除测试子进程的数据库/供应商环境变量，没有连接业务库或模型。
- 后端公开范围 Ruff 和 `app`、`migrations` 字节码编译通过。
- 这些结果是提交前工程检查；不新增真实资料入库或完整 M1 的验收结论。GitHub CI 尚未由本次本地提交触发。
