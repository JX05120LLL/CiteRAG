# 参与 CiteRAG

当前处于 M0 基础开发阶段。先阅读 [PRD](docs/多模态知识助手_PRD_v0.2_单企业首版.md)、[技术架构](docs/多模态知识助手_技术选型与架构设计_v0.1.md) 和 [开发路线](docs/development/ROADMAP.md)，再处理一个范围明确的任务。

## 开始修改

1. 阅读附近说明并检查工作区已有改动。
2. 说明准备改什么、影响哪些行为、如何验证。
3. 按现有基线完成最小改动；产品范围或架构变化另行记录决定。
4. 行为变化补充有意义的验证，运行与改动相关的检查；专用测试文件按当前约定仅在本机保留。
5. 自查 diff，在 PR 中写明结果及未验证项。

首版本地单用户，无管理员、注册或登录；使用者管理自己的知识库，服务只面向回环地址。前端使用原生 TypeScript + HTML/CSS；后端按技术文档划分职责。不要在接入验证前先建复杂平台、引入多用户/多租户或复制参考仓库的完整业务实现。

## 数据与依赖

- 不提交真实模型 Key、登录令牌、个人聊天、用户附件、业务原文或数据库备份。
- 配置样例使用明确占位值；真实值仅注入本地受控后端进程。
- 示例内容明确标为演示，测试数据应拥有合法使用权限。
- 依赖版本与锁文件在对应阶段实测后建立，不把上游锁文件直接当作本项目锁文件。
- 当前尚未选择项目许可证；在接收外部代码贡献或正式发布前先落实许可。

## 当前可用检查

在已安装依赖的仓库根目录执行：

```powershell
Set-Location backend
uv run --no-env-file ruff check .
uv run --no-env-file python -m compileall -q app migrations
uv run --no-env-file alembic upgrade head --sql
Set-Location ../frontend
pnpm typecheck
pnpm build
Set-Location ..
git diff --check
```

GitHub Actions 在推送、PR 或手动触发时执行自动检查，包含独立临时 PostgreSQL 的迁移与真实 API 冒烟验证，详见 [CI 说明](docs/development/CI.md)。CI 不调用模型，也不部署。

`backend/tests/` 与前端 `*.test.ts`、`*.spec.ts` 等专用测试文件仅在维护者本机保留，不提交到仓库。新克隆不以 `pytest` 或 `pnpm test` 作为默认检查入口；具有本地测试文件时，可按[本地开发](docs/development/LOCAL-DEVELOPMENT.md#运行检查)运行额外测试。

文档与设计改动还需核对相对链接、SVG 与浏览器预览；提交前检查文件清单，排除凭证、本地资料和运行数据。实际验证结果见 [M0 验证记录](docs/development/M0-VALIDATION.md)。本机辅助脚本不随仓库发布；CI 通过或静态预览通过均不代表 M0 业务验收通过。
