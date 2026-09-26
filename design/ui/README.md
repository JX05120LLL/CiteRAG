# CiteRAG · UI 设计资产与可运行界面

当前 UI 已按 2026-09-26 用户确认的[三张概念图](concepts/2026-09-26/README.md)升级。实现、控件—API 对应、桌面／手机截图与验收步骤见 [本轮交付](UI-UPGRADE-20260926.md)。历史实现记录见 [2026-09-25 可运行 UI 说明](RUNTIME-UI.md)。下方 A 版四张 SVG/PNG 保留为历史设计资产；当前实现以 `frontend/src/` 为准。运行截图均注明隔离响应与业务验收边界。

已选 **A · 对话优先**，搭配正式无字「回响」Logo。四张桌面稿均为 1440 × 960，采用白底、黑灰层级、适度信息密度和靠近回答的来源展示。

2026-09-22 产品范围已改为本地单用户：直接进入工作台，使用者管理自己的知识库，没有管理员或登录。四张 PNG/SVG 继续作为布局参考；其中管理员、团队共享等旧文案是历史快照，已失效，不能作为当前业务要求。实际文案以 [设计规范](DESIGN-SPEC.md) 和最新 PRD 为准，本次未重新导出图片。

## 查看与下载

直接用浏览器打开 [index.html](index.html)，无需依赖或联网。支持四页切换、左右方向键与 Home / End、适应窗口与 100% 缩放、PNG / SVG 下载。兼容 `#a/03-image` 等地址片段。

| 页面 | PNG | 可编辑 SVG |
| --- | --- | --- |
| 问答工作台 | [PNG](exports/a-01-workbench.png) | [SVG](exports/a-01-workbench.svg) |
| 实时语音 | [PNG](exports/a-02-voice.png) | [SVG](exports/a-02-voice.svg) |
| 图片提问 | [PNG](exports/a-03-image.png) | [SVG](exports/a-03-image.svg) |
| 知识库管理 | [PNG](exports/a-04-knowledge.png) | [SVG](exports/a-04-knowledge.svg) |

[四页总览](exports/a-overview.png) · [视觉与状态规范](DESIGN-SPEC.md) · [正式 Logo](../brand/index.html)

效果图内部均为静态演示；只实现审核器的切换、缩放和下载，不连接知识库、模型或麦克风。

## 资产与编辑

仓库发布四页已选 SVG、PNG、预览页及保存视觉参数的 `a-design.json`。直接打开预览页查看和下载资产，或使用矢量编辑器调整 SVG；生成工具仅在维护者本机保留，不随仓库发布。

PNG 是浏览器导出快照，修改 SVG 后需另行更新对应 PNG；本次快照已包含正式 Logo。中文字体使用 Microsoft YaHei UI / Microsoft YaHei / PingFang SC / sans-serif，不内嵌商业字体。跨系统编辑后应复查字宽及排版。

SVG 保留文字和矢量，不等同于原生 Figma 组件；未验证 Figma 导入与自动布局。历史方案在本地保留，不随公开仓库发布。
