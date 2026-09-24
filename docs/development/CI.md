# GitHub CI

工作流位于 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)，在推送、Pull Request 和手动触发时运行。打开仓库的 [Actions 页面](https://github.com/JX05120LLL/CiteRAG/actions) 查看各次运行；已核验的首次结果记录如下。

已核验的首次运行：提交 [`ed26168`](https://github.com/JX05120LLL/CiteRAG/commit/ed261687485a7de832fbd3383831fbe98ff6dc2c) 的 [CI #35708529819](https://github.com/JX05120LLL/CiteRAG/actions/runs/35708529819) **成功**，Repository boundaries、Frontend build and smoke、Backend and PostgreSQL smoke 三个作业均通过。此结论只对应该提交，后续提交以各自运行结果为准。

## 检查范围

| 部分 | 自动检查 |
|---|---|
| 提交文件范围 | 拒绝已跟踪的本地辅助脚本、专用测试、凭证配置及运行产物路径；仅检查路径，不等于完整密钥扫描。 |
| 前端 | 按锁文件安装依赖、TypeScript 类型检查、Vite 构建及构建产物冒烟检查。 |
| 后端静态检查 | 按锁文件安装依赖、Ruff、Python 编译及 Alembic 迁移 SQL 生成。 |
| 后端集成冒烟 | 独立临时 PostgreSQL 17.9 上执行新库迁移与重复迁移，核对本地归属不变；通过 TestClient 启停真实应用生命周期并发起进程内 ASGI 请求，检查存活、数据库状态、空知识库/聊天列表、来源拒绝、不存在知识库及缺失配置的响应。 |

CI 仅具有 `contents: read` 权限，不部署、不连接真实模型，也不读取工作站数据或凭证。PostgreSQL 服务仅供该 CI 作业使用，使用临时免密实例，无需为本工作流添加真实 GitHub Secrets；此方式不适用于持久业务数据库。

## 测试文件与验收边界

按当前仓库约定，`backend/tests/` 与前端 `*.test.ts`、`*.spec.ts` 等专用测试文件保留在维护者本机，不提交。CI 的冒烟断言直接放在工作流 `run` 步骤中，不依赖 `scripts/`，也不运行 `pytest` 或 `pnpm test`。新克隆可运行的检查与本机额外测试分别见[本地开发](LOCAL-DEVELOPMENT.md#运行检查)。

CI 覆盖安装、构建、迁移与基础 HTTP 行为，不能替代本机完整测试集、浏览器视觉核对或真实 LightRAG/模型接入验证。CI 的空配置实例应报告 `not_configured`；维护者本机已完成模型及 LightRAG 双库真实验证，当前配置匹配报告时 API 的 `models`、`rag` 均显示 `available`。不要为使检查通过而伪造 ready 状态。

CI 通过不代表 M0 完成。固定 SDK、pgvector、模型与重排、双知识库隔离和可核查来源已有独立真实验收；Embedding 超长输入边界仍未通过，详见[路线图](ROADMAP.md)与[M0 验证记录](M0-VALIDATION.md)。
