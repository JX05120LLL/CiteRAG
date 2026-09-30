# 参与 CiteRAG

项目仍在开发，当前实现与未完成条件以 [README](README.md)、[后端说明](backend/README.md)和[前端说明](frontend/README.md)为准。选一个范围明确的任务，区分代码实现、合成验证、真实供应商调用与业务验收。维护者的 `docs/` 目录不随公开仓库发布。

## 开始修改

1. 阅读附近说明并检查工作区已有改动。
2. 说明准备改什么、影响哪些行为、如何验证。
3. 按现有基线完成最小改动；产品范围或架构变化另行记录决定。
4. 行为变化补充有意义的验证，运行与改动相关的检查；公开 CI 测试须使用合成数据，其他专用测试仍在本机保留。
5. 自查 diff，在 PR 中写明结果及未验证项。

当前为本地单用户，无管理员、注册或登录；使用者管理自己的知识库，服务只面向回环地址。正式前端使用 React + TypeScript + Vite + Ant Design / Ant Design X；后端使用 FastAPI 与 LightRAG SDK。新能力应沿用现有问答和来源核验链路，不引入未经确认的多用户或公网访问语义。

## 数据与依赖

- 不提交真实模型 Key、登录令牌、个人聊天、用户附件、业务原文或数据库备份。
- 配置样例使用明确占位值；真实值仅注入本地受控后端进程。
- 示例内容明确标为演示，测试数据应拥有合法使用权限。
- 依赖版本与锁文件在对应阶段实测后建立，不把上游锁文件直接当作本项目锁文件。
- CiteRAG 自有内容按 [Apache License 2.0](LICENSE) 授权；提交贡献前确认自己有权按该许可证提供代码，引用第三方材料时保留其原许可与署名。

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

GitHub Actions 在推送、PR 或手动触发时执行自动检查，包含独立临时 PostgreSQL 的迁移与真实 API 冒烟验证，详见 [工作流](.github/workflows/ci.yml)。CI 不调用模型，也不部署。

选定的前端 Vitest 与后端 PostgreSQL/路由测试已随仓库提交，由 GitHub CI 自动运行；其余专用测试继续留在维护者本机。新克隆可在 `frontend/` 运行 `pnpm test`，后端公开 pytest 命令见 [工作流](.github/workflows/ci.yml)和[后端说明](backend/README.md)。

文档与界面改动还需核对相对链接、SVG 与浏览器预览；提交前检查文件清单，排除凭证、本地资料和运行数据。当前未完成项见[README](README.md)。本机辅助脚本不随仓库发布；CI 通过或静态预览通过均不代表完整业务验收。
