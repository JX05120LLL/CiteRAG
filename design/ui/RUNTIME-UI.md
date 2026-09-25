# CiteRAG 可运行 UI：知识库、文字工作台与系统状态

2026-09-25。当前运行界面以 `frontend/src/` 为准。旧 A 版 SVG/PNG 是历史设计资产，不再约束页面排布。保留 CiteRAG 名称与 `assets/brand/` 的无字「回响」Logo，继续使用原生 TypeScript、HTML/CSS 和 Vite。

参考 [LiveRAG 当前 `master` 的知识库截图](https://github.com/YS-BW/LiveRAG/blob/208eede49e76cbb00ed85ee644576bd2520ce8f6/README.assets/%E7%9F%A5%E8%AF%86%E5%BA%93.png)及同提交的 `frontend/components/app/live-rag-knowledge-panel.tsx`：借鉴窄侧栏、右侧任务区、紧凑状态与资料清单。未复制其源码、图片、品牌或语音入口。

## 页面结构

- 左侧：品牌、当前聊天或知识库列表、文字工作台／知识库／系统状态导航。375px 时列表与导航横向滚动。
- 右侧：顶部固定上下文、当前页面标题与操作，内容区显示回答或受管资料。工作台的消息区独立滚动，底部保留输入区。
- 工作台：当前聊天固定知识库；换库需展开“切换知识库并新建聊天”。回答附近区分临时片段、已保存核验、`partial`、拒答与引用原文位置。刷新时选中的旧聊天即使不在首屏 20 条内，也从单聊接口恢复。
- 输入区（2026-09-25 最新调整）：只保留一个问题输入框和发送按钮，不展示查询模式或精确条件。前端以 `mode=auto` 调用原消息流接口；后端识别意图后受控转发到普通检索、已确认资料属性等值定位或当前知识库原文块的编号/短语字面定位。订单号只是知识库查询示例。
- 资料页：分别显示已受理、已解析和当前可作问答候选的资料数量；`blocked`／维护期间候选数为零。任务列表显示阶段和失败原因；单任务详情另从服务读取。`blocked` 与维护期直接提示暂停问答及修复入口。
- 状态页：只读展示 `/api/status` 的能力状态、本次读取时间和安全的验证元信息；这里不发起模型请求。

## 控件与 API

| 控件 | `frontend/src/api/client.ts` / 后端路径 | 状态约束 |
|---|---|---|
| 知识库列表、刷新、创建、改名 | `knowledgeBases` `GET/POST /api/knowledge-bases`；`renameKnowledgeBase` `PATCH /api/knowledge-bases/{id}` | 创建使用恢复键；上限与处理中禁用 |
| 资料与任务列表、刷新 | `documents` `GET /api/knowledge-bases/{id}/documents`；`jobs` `GET /api/knowledge-bases/{id}/jobs` | 分别显示受理、解析、问答候选；上传受理不等于核验通过 |
| 上传、原文、解析位置、属性确认 | `uploadDocuments` `POST /api/knowledge-bases/{id}/documents`；`originalUrl` `GET /api/documents/{id}/original`；`blocks` `GET /api/documents/{id}/blocks`；`updateDocumentAttributes` `PATCH /api/documents/{id}/attributes` | 维护和 blocked 禁用新上传；原文与解析入口按库及资料状态控制 |
| 删除、替换、重试、旧空间清理、重建 | `deleteDocument` `POST /api/documents/{id}/delete`；`replaceDocument` `POST /api/documents/{id}/replacement`；`retryJob` `POST /api/jobs/{id}/retry`；`cleanupJob` `POST /api/jobs/{id}/cleanup`；`rebuild` `POST /api/knowledge-bases/{id}/rebuild` | 受管任务完成前不显示成功；重建需确认影响 |
| 任务详情、刷新 | `job` `GET /api/jobs/{id}` | 每次打开或刷新单独读取；显示阶段、影响资料、失败原因和可重试状态，不展示原始异常文本 |
| 聊天列表、更多、选中与续读 | `conversations` `GET /api/conversations?limit=&offset=`；旧聊天 `conversation` `GET /api/conversations/{id}`；`conversationMessages` `GET /api/conversations/{id}/messages` | 当前聊天固定知识库；首屏外旧聊天直接读取单聊，分页偏移仍按列表实际加载数计算 |
| 新建、改名、删除聊天 | `createConversation` `POST /api/conversations`；`renameConversation` `PATCH /api/conversations/{id}`；`deleteConversation` `DELETE /api/conversations/{id}` | 删除需确认；换库须新建聊天 |
| 单框文字提问、失败／部分回答重试、来源原文 | `askMessageStream` `POST /api/conversations/{id}/messages/stream`，发送 `mode=auto`；`retryAnswer` `POST /api/conversations/{id}/messages/{messageId}/retry`；来源链接 `originalUrl` | 服务端校验并保存路由，响应只暴露安全的 `route` 类型；编号/短语从问题复制并在已入库原文查找；SSE 临时片段无引用，`partial` 同消息重试成功后才展示已核验来源 |
| 系统状态、刷新 | `health` `GET /api/status` | 显示 API 返回的数据库、引擎、模型、备份等状态及本次读取时间；这里不做模型在线测试 |

知识库整体删除尚无后端接口，因此页面不提供该按钮。状态页没有独立模型测试接口；真实模型请求尚未获本轮授权，页面只提供状态刷新。语音和图片为后续阶段，因此本版没有可点击入口。

## 浏览器截图

最新知识库编号查询：[桌面 1440×960](exports/runtime-kb-literal-1440.png) · [手机 375×812](exports/runtime-kb-literal-375.png)。独立 5174 Vite、8001 新代码 API 和 55435 临时 PostgreSQL 只使用合成 TXT 与本地确定性路由/回答替身。浏览器点通新建聊天、单框发送 `mode=auto`、“查订单 ORD-001 的金额”返回合成原文第 1 行；另一份资料中的 `ORD-0010` 未混入。DOM 确认只有一个问题 textarea、一个发送按钮，手动模式/精确条件控件为零；375px 与 320px 整页 `scrollWidth` 分别等于视口宽度。未调用真实模型，旧 8000 服务未停止或重启。先前 [X100 属性路由桌面](exports/runtime-auto-workbench-1440.png)、[手机](exports/runtime-auto-workbench-375.png)记录了用户澄清前的样例和文案，不再作为当前订单意图的截图；更早的手动模式及下表页面截图也仅保留历史证据。

### 本轮隔离服务截图（2026-09-25）

以下截图由 1440×960 和 375×812 的真实浏览器生成。8000 为本仓库 `.local/manual-m1/manual_api.py` 的隔离手工服务，资料、检索和生成使用合法合成数据及本地替身；5173 为本仓库 Vite。截图时浏览器仅把知识库列表过滤为名称含“合成资料”的测试库，避免带入其他本地资料；资料、任务、聊天和 `/api/status` 仍直连隔离服务。手机资料与状态采用整页截图。另在 320px 宽度核对工作台、知识库、资料、任务详情、状态页均无整页横向溢出。截图证明页面排布和这套隔离服务的响应，不证明真实供应商、实际业务库或完整 M1。

| 页面／状态 | 桌面 | 手机 |
|---|---|---|
| 知识库管理 | [1440px](exports/runtime-isolated-knowledge-1440.png) | [375px](exports/runtime-isolated-knowledge-375.png) |
| 文字工作台，合成回答与原文行号 | [1440px](exports/runtime-isolated-workbench-1440.png) | [375px](exports/runtime-isolated-workbench-375.png) |
| 受管资料和持久任务 | [1440px](exports/runtime-isolated-documents-1440.png) | [375px](exports/runtime-isolated-documents-375.png) |
| 单任务详情，直接读取任务 API | [1440px](exports/runtime-isolated-task-detail-1440.png) | [375px](exports/runtime-isolated-task-detail-375.png) |
| 系统状态，直接读取 `/api/status` | [1440px](exports/runtime-isolated-status-1440.png) | [375px](exports/runtime-isolated-status-375.png) |

本轮浏览器实际点通：知识库列表与资料页读取、解析位置读取、单任务详情读取、状态刷新；在隔离服务的合成库中创建聊天、普通问答、单次模拟生成中断产生无引用 `partial`、同消息重试完成、确认资料编号后精确问答，再清空编号恢复原值。测试聊天留在隔离手工库中，未执行不可逆删除。知识库新建／改名、资料上传／删除／替换／重建等其他写控件的行为由前端测试和已有隔离后端测试覆盖，本轮未逐项在浏览器执行，不能列作本轮真实点通。

### 早期合成路由截图

以下截图是 1440×960 与 375×812 真浏览器渲染。知识库、资料和聊天使用隔离的合成浏览器路由，名称均标明“合成”；系统状态截图中的 `/api/status` 则直连本机隔离手工服务，左侧库列表仍为合成响应。它们证明布局和控件呈现，不代表真实模型、生产数据或完整 M1 验收。

| 状态 | 桌面 | 手机 |
|---|---|---|
| 文字工作台，已保存回答与来源 | [1440px](exports/runtime-synthetic-workbench-1440.png) | [375px](exports/runtime-synthetic-workbench-375.png) |
| `partial` 可见且可重试 | [1440px](exports/runtime-synthetic-partial-1440.png) | — |
| 受管资料、任务与核验阶段 | [1440px](exports/runtime-synthetic-documents-1440.png) | [375px](exports/runtime-synthetic-documents-375.png) |
| `blocked` 修复 | — | [375px](exports/runtime-synthetic-blocked-375.png) |
| 系统状态，真实 `/api/status` 只读响应 | — | [375px](exports/runtime-status-api-375.png) |

早期阶段还检查了布局、焦点、文字溢出、移动滚动、空库、blocked、来源、`partial` 和证据不足。该阶段的合成浏览器路由及本轮隔离手工服务是不同证据，均没有操作真实模型与实际业务库。

## 集中验收步骤与未验证边界

1. 按 [本地开发](../../docs/development/LOCAL-DEVELOPMENT.md)核对即将使用的 API、Vite 进程与端口归属，先在隔离环境打开工作台。对照上方桌面和手机截图，检查知识库列表、资料与任务、工作台及状态页；在 320px 手机宽度检查单框输入与页面无横向溢出。
2. 用合法合成资料在隔离库创建知识库并上传 TXT、Markdown、文字 PDF、普通 DOCX；分别观察受理、解析、入库核验和维护/blocked 状态。核对任务详情来自单任务接口，失败时刷新、重试或重建后只有核验通过才恢复问答。此步四类文件和写操作在本轮没有逐项重新浏览器点通。
3. 在已就绪的隔离库创建聊天，普通提问核对回答和可下载原文的真实行号；确认文档编号、型号或版本后，在同一个输入框中自然提问，核对等值来源。另上传合成 `ORD-001` 与 `ORD-0010` 两份资料，以“查订单 ORD-001 的金额”核对原文字面路由、完整编号边界和来源；同编号出现在两份资料时应澄清且无引用。模拟中断时，`partial` 只有临时正文且无来源；同消息重试成功后才显示已保存回答。刷新并打开首屏外旧聊天，核对续读和“更多”分页。
4. 切到系统状态并刷新，核对读取时间更新、数据库/引擎/模型/备份与 API 相符；`not_configured` 不能解释为真实模型可用。本轮状态刷新已点通，时间由 15:55:17 更新至 15:55:30（2026-09-25 本机时间）。
5. 如要做实际业务验收，先另行决定真实模型请求的内容类别、次数、费用与数据影响；实际业务库迁移/恢复前须停写并成套备份业务库、引擎库、私有原文及配置。上述两类验证本轮均未授权和执行。M0 超长 Embedding 边界与完整 M1 保持未通过。
