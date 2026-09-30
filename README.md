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
| 受控工具首片 | 普通聊天可手动读取本机时间，固定库聊天还可查看本库资料目录；服务端检查范围，结果与引用分开。 |

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

## 🗺️ 会话类型与后续工具边界

当前源码支持创建普通聊天或固定知识库聊天。普通聊天不检索知识库，保留至主动删除；知识库聊天保留 180 天规则，并可手动保存来源明确的同库共享摘要。工具网关首片提供手动只读调用、服务端范围门禁、持久结果和审批状态；模型自动选工具、外部 MCP 与写入操作尚未接入。新 schema `0011` 只在隔离库验证，**实际业务库尚未迁移**。

```mermaid
flowchart TD
    A["文字输入 / 语音最终转写 / 图片观察"] --> B["读取聊天绑定与本聊天上下文"]
    B --> C{"聊天绑定知识库？"}
    C -- "否：普通聊天" --> D["本聊天近期记录与摘要"]
    D --> E["普通回答路径"]
    C -- "是：知识库聊天" --> F["本聊天上下文 + 有效的同库共享摘要"]
    F --> G{"意图路由"}
    G -- "普通问题" --> E
    G -- "需要当前库证据" --> H{"精确检索 / 语义检索"}
    H --> I["当前库检索与原文核验"]
    E --> K["生成并保存回答；资料回答先核验"]
    I --> K
    K --> M["页面展示；语音回答再送 TTS 播放"]
```

无库聊天不会自动读取知识库；共享摘要只用于理解背景，不能代替本轮原文证据。共享内容随来源删除或库修订失效；工具历史结果仅反映调用当时状态，也不能代替知识库原文证据。

## 🙏 致谢与许可

CiteRAG 的自有内容采用 [Apache License 2.0](LICENSE)。该许可证允许商用与修改，但不授予项目名称或标志的商标使用权。

第三方材料保留各自许可：[LightRAG](https://github.com/HKUDS/LightRAG/blob/59af311307c7417b342f44850b097648d47e83bd/LICENSE) 为 MIT；改写的 LiveKit starter 布局及其 MIT 声明见[供应商说明](frontend/vendor/livekit/README.md)；Silero VAD 参考实现及 MIT 声明见[后端供应商说明](backend/vendor/silero/README.md)。云模型和语音服务仍按各供应商条款使用。
