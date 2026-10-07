<p align="center"><img src="assets/brand/app-icon.svg" width="88" height="88" alt="CiteRAG 标志"></p>

<h1 align="center">CiteRAG</h1>

<p align="center">
  让本地资料变成有原文可查的回答<br>
  文字、语音与图片提问，共用同一条问答链路
</p>

<p align="center">
  <a href="backend/pyproject.toml"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white"></a>
  <a href="backend/pyproject.toml"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-API-009688?style=flat-square&amp;logo=fastapi&amp;logoColor=white"></a>
  <a href="frontend/package.json"><img alt="React 19" src="https://img.shields.io/badge/React-19-149ECA?style=flat-square&amp;logo=react&amp;logoColor=white"></a>
  <a href="frontend/package.json"><img alt="TypeScript 5.9" src="https://img.shields.io/badge/TypeScript-5.9-3178C6?style=flat-square&amp;logo=typescript&amp;logoColor=white"></a>
  <a href="backend/pyproject.toml"><img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-Storage-4169E1?style=flat-square&amp;logo=postgresql&amp;logoColor=white"></a>
</p>
<p align="center">
  <a href="https://github.com/HKUDS/LightRAG"><img alt="LightRAG 知识引擎" src="https://img.shields.io/badge/LightRAG-Knowledge_Engine-355C7D?style=flat-square"></a>
  <a href="frontend/vendor/livekit/README.md"><img alt="LiveKit 实时音频" src="https://img.shields.io/badge/LiveKit-Real--time_Audio-6C3EF4?style=flat-square&amp;logo=livekit&amp;logoColor=white"></a>
  <a href="LICENSE"><img alt="Apache-2.0 许可证" src="https://img.shields.io/badge/License-Apache--2.0-D22128?style=flat-square&amp;logo=apache&amp;logoColor=white"></a>
  <a href="https://github.com/JX05120LLL/CiteRAG/actions/workflows/ci.yml"><img alt="GitHub CI 状态" src="https://img.shields.io/github/actions/workflow/status/JX05120LLL/CiteRAG/ci.yml?branch=main&amp;style=flat-square&amp;logo=githubactions&amp;label=CI"></a>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-界面预览">界面预览</a> ·
  <a href="#-数据保存在哪里">数据边界</a> ·
  <a href="CONTRIBUTING.md">参与项目</a>
</p>

## 🎨 CiteRAG 是什么

CiteRAG 使用 [LightRAG](https://github.com/HKUDS/LightRAG) 为本地资料建立知识索引。你可以上传文档，在固定知识库中用文字或语音提问，也可以附图片让模型先观察，再依据当前问题决定是否需要检索资料。

知识库回答会回查受管原文、核验事实和引用后保存；问候及通用问题走普通回答，并明确标明没有检索知识库。创建聊天时可选“普通聊天”或固定一个知识库；普通聊天不会访问未选择的知识库。

## ✨ 为什么做 CiteRAG

- **答案可追溯**：来源指向本轮可核对的原文位置；证据不足时明确说明，不把常识包装成资料结论。
- **三种输入共用问答服务**：文字、语音最终转写和图片观察进入同一套意图路由、检索、核验与聊天记录。图片观察本身不是知识库引用。
- **资料和任务有真实状态**：上传受理、解析、索引、核验及失败分别展示；处理失败可查看原因，资料维护会暂停相关问答。
- **本地数据边界清楚**：原始文件留在本机私有目录；业务记录与 LightRAG 索引分别存于 PostgreSQL 业务库和引擎库。

## 🧭 从上传到回答

1. **上传资料**：接收 TXT、Markdown、文字 PDF 或普通 DOCX，保存原文件与资料任务。
2. **解析入库**：检查文档结构和内容，写入受管原文；LightRAG 建立索引后还要核验资料状态，上传成功并不等于可问答。
3. **提出问题**：文字直接进入工作台；语音仅在 ASR 最终转写后提交；图片先生成有不确定性标记的文字观察。
4. **选择路径**：普通问题由模型结合本聊天上下文回答；需要当前库事实的问题选择精确定位或语义检索。
5. **核验并保存**：资料回答核对证据、事实和引用，保存后才交给 TTS 播放；普通语音回答可分段提前播报，最终状态仍写入聊天。

## 🧰 已接入的能力

| 场景 | 当前代码能力 |
| --- | --- |
| 知识库与资料 | 创建和维护知识库，批量上传、查看任务、失败原因、删除、替换和受管重建。 |
| 文字问答 | 自动区分普通回答与知识库回答；知识库回答选择精确或语义检索，保留来源、历史和重试。 |
| 图片提问 | PNG/JPEG 私有附件先经视觉模型观察；不确定编号可要求确认，图片不会自动入库。 |
| 实时语音 | LiveKit 音轨、火山 ASR、本地 Silero VAD、共用问答、MiniMax TTS 和浏览器播放；提供字幕、插话取消与文字降级。 |
| 状态与记录 | 原问题、回答尝试、摘要和持久任务保存在业务库；工作台显示来源、错误与系统状态。 |
| 工具与 Agent（可选） | LangGraph 有界自动工具循环、补参/审批暂停、持久恢复和取消；自写工具与已审查 MCP 共用网关，结果与资料引用分开。 |

上表表示**代码已接线**，不是完整 M0—M3 验收结论。

## 🖼️ 界面预览

![CiteRAG 对话工作台、知识库与资料、语音通话和系统状态四合一界面预览](assets/preview/citerag-ui-overview.png)

四张图来自当前正式 React 工作台的浏览器截图，使用公开合成资料和只读 API 响应。语音页停在连接前；截图不代表真实模型、媒体连接或业务库验收通过。

## 🗂️ 数据保存在哪里

原始 DOCX、PDF、TXT 和 Markdown 保存在本机私有目录；业务 PostgreSQL 保存资料、解析块、任务、原始聊天、回答尝试与摘要；独立的 PostgreSQL/pgvector 引擎库保存 LightRAG 索引。聊天摘要不会替换原始消息，索引也不能单独恢复原文件或聊天。

## 🚀 快速开始

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 22.12+ 和 pnpm 10.33.0。先确认本机 `8000` 与 `5173` 端口空闲，不要停止来源不明的服务。

```powershell
git clone https://github.com/JX05120LLL/CiteRAG.git
cd CiteRAG
```

在仓库根目录打开两个终端。

**终端一：后端**

```powershell
cd backend
uv sync --locked
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

**终端二：前端**

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

打开 [工作台](http://127.0.0.1:5173/) 或[独立语音页](http://127.0.0.1:5173/voice.html)。这些命令可启动**无数据库、无模型配置**的界面和 API；系统状态会如实显示缺失的服务，不会生成演示资料或自动请求供应商。

打开系统状态页时，已配置的模型与语音能力会自动进行一次有界实际调用：模型至多一次 DashScope 请求，TTS 至多一次 MiniMax 请求，ASR 至多一次 MiniMax 合成及一次火山引擎识别请求。结果按配置指纹保留一小时；重复打开页面复用有效结果，点击“刷新系统状态”会重新检测。调用可能产生供应商费用。未配置的能力显示不可用；知识检索目前缺少可证明只读的完整验收路径，因此保持“未检测”，不会伪报可用。本地诊断仍可在状态页单独运行。

### ⚙️ 启用完整功能

| 需要准备 | 作用 |
| --- | --- |
| PostgreSQL 业务库及显式迁移 | 保存知识库、受管原文、任务与聊天。 |
| PostgreSQL/pgvector 引擎库 | 存放 LightRAG 索引；与业务库隔离。 |
| 受控后端模型配置 | 入库、Embedding、重排、文字回答和图片观察；凭证不写入前端或 Git。 |
| 本地自托管 LiveKit 与语音服务 | 开启实时通话、火山 ASR、MiniMax TTS 和本地 VAD。 |

完整接入还需在 `backend/` 安装锁定的可选依赖（`uv sync --locked --extra rag --extra voice`），准备独立业务库与引擎库，将数据库和模型配置注入受控后端环境，并对**新建空业务库**显式运行 `uv run --no-env-file alembic upgrade head`。已有数据升级前须停写并成套备份业务库、引擎库和私有文件。默认 `CITERAG_INGESTION_ENABLED=false`、`CITERAG_ANSWER_ENABLED=false`；配置凭证或打开页面都不会自动授权付费调用。不要把实际业务库、私人资料或密钥用于公开测试。配置字段见 [backend/app/config.py](backend/app/config.py)。

## 🏗️ 技术与项目结构

- **界面**：React、TypeScript、Vite、Ant Design / Ant Design X。
- **应用**：FastAPI、SQLAlchemy、Alembic，负责资料、会话、任务、来源和状态。
- **知识引擎**：固定版本 LightRAG SDK 与 PostgreSQL/pgvector。
- **媒体**：本地 LiveKit；语音最终转写交给现有 AnswerService。

```text
CiteRAG/
├── backend/       API、模型适配、知识引擎接入与迁移
├── frontend/      正式工作台与独立语音入口
└── assets/brand/  正式标志
```

## 🧪 当前验证边界

- GitHub CI 使用公开合成资料与临时 PostgreSQL，执行静态检查、测试和构建；它不调用真实模型，也不验收私人业务数据。
- 已有局部真实供应商与合成语音验证记录，不能替代真人设备、真实资料及完整语音质量验收。
- M0 的 Embedding 超长输入边界、完整 M1 验收、完整 M2 真人语音验收和实际业务库图片闭环仍有未完成项。

## 📑 项目说明

- [后端](backend/README.md) · [前端](frontend/README.md) · [贡献指南](CONTRIBUTING.md)

## 🗺️ 系统流程与工具网关

内置本机时间、本地十进制计算器及仅限固定库聊天的资料目录工具。另提供可显式启动的只读 MCP 计算参考服务，启动、审查登记与失败处理见[工具与 MCP](backend/TOOLS.md)。可选[和风天气工具](backend/QWEATHER.md)提供城市搜索、实时天气与短期预报，两类聊天共用同一网关；默认关闭，执行前显示地点参数并要求审批。天气结果保留单位、查询时间和供应商归属，独立于知识库引用。目前只完成隔离合成验证，真实账户和模型规划仍需验收。

当前源码支持普通聊天与固定知识库聊天；同库共享摘要保留来源与失效规则。可选 LangGraph 编排已接入模型工具决策、补参/审批暂停、预算和持久恢复，MCP 提供受控 Streamable HTTP 适配器。Agent/MCP 默认关闭，内置工具仍为本机只读工具，没有注册真实外部写入。新 schema `0012` 只在隔离库验证，**实际业务库尚未迁移**。

下图展示启用 Agent 后的调用链。LangGraph 编排下一步，工具网关执行权限和幂等检查，AnswerService 继续负责唯一正式聊天历史、知识库路由与提交核验。每任务最多 6 次决策生成、4 次工具尝试、60 秒活动预算；资料路由/观察/核验可能另有模型请求。关闭 Agent 时保留原问答链路。安装、协议、恢复与 MCP 审查见 [Agent 说明](backend/AGENT.md)。本轮验证使用隔离数据库、合成模型和本地合成 MCP，尚未验收真实模型、用户 MCP 服务或真人语音。

```mermaid
flowchart TD
    A["文字 / 图片观察 / 语音最终转写"] --> B["AnswerService<br/>检查聊天归属与固定类型"]
    B --> C["准备本聊天上下文<br/>知识库问题先检索并准备证据"]
    C --> D["LangGraph：模型决定下一步"]
    D -->|直接回答| E["AnswerService 核验并保存"]
    D -->|需要补参数| F["暂停，等待用户补充"]
    F --> D
    D -->|请求工具| G["ToolGateway<br/>范围、参数、版本、幂等检查"]
    G -->|需要审批| H["展示目标、参数、影响"]
    H -->|批准并重新校验| I["执行工具"]
    G -->|无需审批| I
    I --> J["保存实际成功 / 失败结果"]
    J --> D
    E --> K["显示正文、引用与工具记录<br/>语音回答保存后交给 TTS"]
```

无库聊天不会自动读取知识库；共享摘要只用于理解背景，不能代替本轮原文证据。共享内容随来源删除或库修订失效；工具历史结果仅反映调用当时状态，也不能代替知识库原文证据。

## 🙏 致谢与许可

CiteRAG 的自有内容采用 [Apache License 2.0](LICENSE)。该许可证允许商用与修改，但不授予项目名称或标志的商标使用权。

第三方材料保留各自许可：[LightRAG](https://github.com/HKUDS/LightRAG/blob/59af311307c7417b342f44850b097648d47e83bd/LICENSE) 为 MIT；改写的 LiveKit starter 布局及其 MIT 声明见[供应商说明](frontend/vendor/livekit/README.md)；Silero VAD 参考实现及 MIT 声明见[后端供应商说明](backend/vendor/silero/README.md)。Agent 可选依赖包含 MIT 与 LGPL-3.0-only，见[依赖许可](backend/vendor/agent/README.md)。云模型和语音服务仍按各供应商条款使用。
