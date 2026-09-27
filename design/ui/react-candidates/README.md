# CiteRAG · B 版交付

2026-09-27 用户选定 **B · 对话优先知识助手**。主入口和独立语音入口已迁移 React + Ant Design / Ant Design X。本轮 A/C 候选代码、截图与链接已删除；旧项目历史资产保留。

## 直接打开

| 入口 | 地址 | 数据与能力 |
| --- | --- | --- |
| 正式工作台 | [打开工作台](http://127.0.0.1:5193/index.html) | 现有 API；真实方法接线。当前 8000 离线，应显示失败，不显示合成成功 |
| B 只读设计 | [打开完整 B 预览](http://127.0.0.1:5193/ui-preview.html?design=b&data=sample) | 显式合成样例；所有写入和媒体禁用，可浏览整套页面 |
| 独立语音 | [打开语音页](http://127.0.0.1:5193/voice.html) | 需已有归属聊天和固定就绪库；无条件时说明原因 |
| 离线截图与前后对照 | [打开目录](index.html) | 1440 / 390 / 320px；合成设计及替身接口截图，不是实际业务验收 |

服务若未运行，先检查端口与拥有者，再从项目根目录运行：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 5193 -ErrorAction SilentlyContinue
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend dev --host 127.0.0.1 --port 5193 --strictPort
```

不要停止未知服务；占用时选择确认空闲端口。Vite 仍代理 127.0.0.1:8000，不自动启动后端、迁移库或启用模型。正式页面不自动写资料或连接语音。

## 查看步骤

1. 只读 B 中依次点击聊天、知识库、任务、系统状态、语音；手机点顶部导航。
2. 展开来源，关闭或按 Escape，焦点返回来源；聊天历史看普通交流、通用回答与 partial。
3. 资料第二页、已删除折叠、失败详情、重建影响；任务历史和第二页、失败详情。
4. 语音切换欢迎、连接中、已连接、重连、断开/挂断、失败六种 **设计状态**，均不能申请媒体。
5. 打开正式入口确认 B 导航和安全错误；现有 API 可用后再做实际操作。
6. 语音沿用 LiveKit 官方 starter 的欢迎/会话控制区，融合 CiteRAG 中文、品牌与主题。当前只有媒体接入，ASR/TTS/字幕/助手未接入。

## 页面与对照

| 页面 | 正式桌面 / 手机 |
| --- | --- |
| 聊天 | [1440](exports/b-final-workbench-1440.png) / [390](exports/b-final-workbench-390.png) |
| 来源 | [1440](exports/b-final-sources-1440.png) / [390](exports/b-final-sources-390.png) |
| 知识库 / 资料 | [1440](exports/b-final-knowledge-bases-1440.png) / [390](exports/b-final-documents-390.png) |
| 任务 / 详情 | [1440](exports/b-final-tasks-1440.png) / [390](exports/b-final-task-detail-390.png) |
| 状态 | [1440](exports/b-final-status-1440.png) / [390](exports/b-final-status-390.png) |
| 官方融合语音 | [1440](exports/b-final-voice-welcome-1440.png) / [390](exports/b-final-voice-standalone-390.png) |
| 连接界面设计 | [1440](exports/b-voice-connected-1440.png) / [390](exports/b-voice-connected-390.png) |

截图均有合成标识。前后对照使用本轮第一阶段 B 候选与正式迁移后画面，不是读取你的历史聊天或私人资料。

## 实现与验证边界

正式 React 复用现有 app / DocumentsPanel / VoiceController 方法和 API，完整保留自动分流、固定库、SSE 保存核验、来源、failed/interrupted/partial 重试、维护门禁和真分页。只读 B 使用同一主题与语音视图，以及受限 GET 适配器；不建立三套逻辑。

正式入口浏览器已检查 33 个页面/尺寸与 14 条实际控件路径（合成接口隔离拦截）；只读 B 检查 47 个页面/状态/尺寸。全部为前端或替身验证，真实业务 API、模型、WebRTC 与设备听感未验收。自动命令及最终结果、控件—方法—接口表、未验证步骤见[交付报告](../../../docs/development/REACT-UI-MIGRATION.md)。没有 commit/push，新远端 CI 未运行。
