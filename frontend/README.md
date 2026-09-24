# CiteRAG 前端

原生 TypeScript、HTML/CSS 与 Vite。沿用 [A 版设计](../design/ui/DESIGN-SPEC.md)与[正式回响图形](../assets/brand/README.md)，不使用 React/Vue。

## 本地运行

使用 Node.js 22.12 或更新版本、pnpm **10.33.0**。本轮已在 Node.js 24.14.0 验证。依赖版本固定在 `package.json` 与 `pnpm-lock.yaml`。

Vite 7.3.6、Vitest 4.1.11 已包含已知开发服务器安全修复；额外将 Vite 的 esbuild 固定到 0.28.1，以避开 [Windows 开发服务器路径穿越问题](https://github.com/evanw/esbuild/security/advisories/GHSA-g7r4-m6w7-qqqr)。仅允许 esbuild 执行安装脚本。

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

打开 [本地工作台](http://127.0.0.1:5173)，直接进入本地单用户工作台。Vite 仅监听 `127.0.0.1`，将 `/api` 同源代理至 `http://127.0.0.1:8000`；后端启动与业务数据库初始化见[本地开发指南](../docs/development/LOCAL-DEVELOPMENT.md)。默认端口被占用时会明确退出，不会自动切换端口。

开发环境的后端须允许前端 Origin。代理保留原始 Host，不设置 `changeOrigin`，使后端可以验证本地访问边界。客户端不发送会话 Cookie，不保存模型凭证。首版没有账号和登录；同一安装的不同浏览器使用同一份本地资料，不提供账号隔离。

## 本轮边界

- 页面启动读取 `/api/knowledge-bases` 和 `/api/conversations`，直接进入工作台；「我的知识库」和「系统状态」入口始终可访问。
- 本地知识库、聊天列表和只读系统状态来自 API。数据库未配置时明确提示配置与迁移步骤；列表读取失败不会冒充空库。`/api/status` 不依赖数据库配置即可说明缺项。
- M1-1 在「我的知识库」增加创建和改名，提交到真实 API 后更新列表；创建使用 UUID 请求键，同一请求重试复用键，后端负责去重与 5 库上限。创建失败不添加伪成功项，改名失败不覆盖已保存名称。
- 同标签页 `sessionStorage` 只保存未确认创建的名称和请求键，整页刷新后恢复手动同键重试，不自动提交；关闭标签页或清除存储后不保证恢复。浏览器存储失败有明确提示，不假装已可靠保存请求。
- 新建聊天、文字问答、上传入库、语音与图片入口暂时禁用，并在附近解释原因。不会生成演示答案、引用或保存成功状态。
- 桌面基线保留 72 px 页眉、232 px 侧栏及底部输入区。小屏提供基础自适应，不代表语音或图片页面已经实现。

当前 M0 尚未整体验收，用户授权先推进 M1-1；完整 M1 未完成。新知识库只保存为空库，不代表已入库或可问答。本轮既有 8000 服务保留旧基线，新增创建/改名接口需要配套后端显式迁移到 `0003_knowledge_management` 并重启；只刷新前端不能升级后端。隔离环境中的真实浏览器与模拟测试结果分别见 [M1 验证记录](../docs/development/M1-VALIDATION.md)。

## 文件职责

| 目录或文件 | 职责 |
|---|---|
| `src/api/client.ts` | 类型、响应校验、同源读写请求、建库请求键与错误区分 |
| `src/app.ts`、`src/state.ts` | 异步请求结果与页面切换 |
| `src/knowledge-draft.ts` | 名称校验、同标签页未确认建库请求保存与恢复 |
| `src/pages/` | 本地工作台、我的知识库、只读系统状态页面 |
| `src/shared/` | DOM、线性图标与工作台框架 |
| `src/styles.css` | A 版视觉 tokens、桌面布局与小屏适配 |

服务端文字通过 `textContent` 写入 DOM，不把库名或聊天标题作为 HTML 执行。资源归属使用后端内部本地标识；后端检查回环地址、Host 与 Origin。前端不提供把本地服务开放给其他设备的设置。

## 检查

```powershell
pnpm typecheck
pnpm build
```

GitHub CI 运行类型检查、构建及构建产物冒烟检查，见 [CI 说明](../docs/development/CI.md)。构建输出为 `dist/`，不纳入 Git；`pnpm preview` 仅用于核对构建资产，不代表后端及知识引擎已经可用。

前端专用测试文件仅在维护者本机保留，不随仓库发布。已有这些文件时可额外运行 `pnpm test`；新克隆无需运行此命令。历史 Vitest 验证使用受控 HTTP 响应，覆盖工作台进入、错误重试、状态校验、空态和 HTML 注入边界；这些本地测试不能替代真实 PostgreSQL、浏览器联调或模型接入验收。
