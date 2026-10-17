---
name: CiteRAG
description: 墨绿导航、暖白页面与浅柠绿选中的本地工作台，保留原有布局与真实语音状态。
colors:
  primary: "#276650"
  primary-hover: "#1d503e"
  primary-selected: "#194a37"
  soft-green: "#e8efdf"
  ink: "#1c3028"
  muted: "#627167"
  reading-ink: "#1c3028"
  surface: "#ffffff"
  surface-soft: "#f4f3ef"
  surface-tint: "#eef2e9"
  navigation: "#142621"
  navigation-hover: "#243c32"
  navigation-line: "#365046"
  navigation-text: "#f4f3ef"
  navigation-muted: "#b3c3b9"
  mint: "#bcf48c"
  line: "#dedfd8"
  neutral-tag: "#eeefea"
  neutral-tag-ink: "#626e65"
  error: "#b23b36"
  warning: "#94611b"
  echo-quiet: "#99b4a7"
  echo-working: "#73b49a"
  echo-attention: "#e2b573"
typography:
  headline: { fontSize: "clamp(25px, 2.2vw, 32px)", fontWeight: 650, lineHeight: 1.3, letterSpacing: "-0.025em" }
  title: { fontSize: "18px", fontWeight: 650, lineHeight: 1.5 }
  body: { fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif', fontSize: "14px", lineHeight: 1.65 }
  reading: { fontSize: "15px", lineHeight: 1.85 }
  label: { fontSize: "12px", fontWeight: 600, lineHeight: 1.5 }
rounded: { tag: "6px", compact: "8px", control: "10px", inset: "12px", reading: "14px", stage: "16px" }
spacing: { "8": "8px", "12": "12px", "16": "16px", "20": "20px", "24": "24px", "28": "28px", "32": "32px" }
components:
  button-primary: { backgroundColor: "{colors.primary}", textColor: "{colors.surface}", rounded: "{rounded.control}", height: "36px" }
  field: { backgroundColor: "{colors.surface}", textColor: "{colors.ink}", rounded: "{rounded.control}", height: "36px" }
  nav-selected: { backgroundColor: "{colors.mint}", textColor: "{colors.navigation}", rounded: "9px", height: "44px" }
  state-tag: { backgroundColor: "{colors.neutral-tag}", textColor: "{colors.neutral-tag-ink}", rounded: "{rounded.tag}", padding: "3px 8px" }
  answer-card: { backgroundColor: "{colors.surface}", textColor: "{colors.reading-ink}", rounded: "{rounded.reading}", padding: "20px 24px" }
---

# Design System: CiteRAG

## Overview

**Creative North Star: "墨绿导航与暖白阅读的本地工作台"**

保留正式 React 工作台的原有布局与交互，采用已确认的静态设计稿配色：墨绿侧栏、暖白页面、浅柠绿选中项和白色阅读面。暗侧栏使用近白正文与浅灰绿次级文字，浅色页面使用深绿正文。文字工作台、管理页及两个语音入口共用主题；品牌沿用无字「回响」Logo。

**Key Characteristics:**

- 稳定导航、单列阅读和同列输入；管理信息按任务密度排列。
- 状态、失败原因和可用操作来自现有控制器与 API。
- 回响环响应实际输入和处理阶段，通话操作与文字记录保留普通 DOM。

文档位于前端应用边界；[项目 README](../README.md) 与 [AGENTS.md](../AGENTS.md) 定义公开范围。本地根目录 `.impeccable/design.json` 仅补充设计工具无法在 frontmatter 表达的信息，已被 Git 忽略；读取该 sidecar 的工具须同时读取本文件。这里记录实现约定，不声明模型、设备或业务验收通过。

## Colors

Primary 是操作青绿；墨绿导航上的选中项使用浅柠绿底与深色字，问题气泡使用浅绿底与深绿字。暗侧栏及手机导航、聊天记录抽屉使用近白文字，次级说明使用浅灰绿，焦点使用浅柠绿。Neutral 使用深绿正文、灰绿次级文字、白色阅读面、暖白页面底和细边框。Error 与 Warning 保留现有状态语义，不用颜色替代状态文字。基础色板由 [theme.ts](src/react/theme.ts) 的 `palette` 维护。

默认状态标签使用 `#626e65` 前景和 `#eeefea` 背景，正文对比度为 `4.61:1`；描述列表标签使用 Muted。状态标签显式设置语义前景与背景，覆盖 Ant Design 6 的 filled 状态样式；语音舞台上的成功和处理状态改用浅柠绿底与墨绿字。

语音阶段区沿用原有布局，使用墨绿底、近白文字及浅柠绿主操作。语音环的 quiet、working、attention 分别标识待连接/安静、识别或生成、错误或等待处理；listening 与 sending 使用 Mint。精确值由 frontmatter 承载，状态映射由 [语音状态模型](src/react/voiceVisualModel.ts) 决定，Three 与 SVG 的颜色保持一致。

## Typography

全局使用 Body 的系统字体栈，不下载额外字体。Headline 用于页面标题；Title 用于章节；Body 用于控件和说明；Reading 用于回答正文；Label 用于紧凑状态标签。时间、描述列表与分页使用等宽数字。

回答保留换行，长文件名、来源和错误可折行。手机回答正文降为 `(14px)`；语音记录使用 `(14px / 1.8)`。这些是现有场景覆盖，其他页面继续复用共享层级。

## Layout

- 桌面侧栏固定 `(252px)`，上下文栏最小高度 `(66px)`；通用页面最大宽度 `(1260px)`，工作台最大宽度 `(1120px)`。
- 消息与 composer 在同一阅读列；桌面 composer 贴底，存在 Agent 任务或进入手机布局时恢复普通文档流。
- 常用间距由 frontmatter 的实际尺度组成；桌面页面左右边距为 `clamp(24px, 3.5vw, 52px)`，手机为 `(16px)`。
- `(1100px)` 及以下侧栏收为 `(226px)`，语音阶段与记录改为单列；`(1050px)` 及以下任务列表与详情改为单列。
- `(900px)` 及以下使用导航与聊天记录抽屉，宽度分别限制为 `min(280px, 100vw)` 与 `min(340px, 100vw)`。
- `(600px)` 及以下缩小边距、卡片和头像；语音控件改为两列。资料列表动作移到内容下方，状态能力卡与诊断面板改为单列。
- 知识库卡片采用最小 `(300px)` 的自适应网格；资料与任务沿用服务端分页及历史折叠。详情抽屉始终限制在视口内。

## Elevation & Depth

阅读卡、知识库卡、状态卡与语音阶段卡主要用底色和细边框分层；主/次按钮取消独立阴影。Composer 保留一层轻阴影，精确值见本地 sidecar 与共享样式。浅色区域焦点使用青绿 `(2px)` 轮廓和 `(3px)` 偏移；暗侧栏、手机导航抽屉与语音阶段区改用浅柠绿焦点，任务行在内部显示焦点轮廓。

## Shapes

标签、紧凑元素、控件、内嵌内容、阅读卡与语音阶段采用 frontmatter 中的圆角尺度。导航项保留 `(9px)` 圆角，正文卡保留细边框；Logo 使用现有正式资产。

## Components

- **主题归属**：[theme.ts](src/react/theme.ts) 拥有 Ant Design 与 Ant Design X 的共同主题；[theme.css](src/react/theme.css) 拥有正式入口、导航、文字阅读和抽屉尺度。[management.css](src/react/management.css) 与 [voice-visual.css](src/react/voice-visual.css) 只补充对应场景。`ui-preview.html` 的只读开发预览共用 theme tokens，预览布局仅使用[独立样式](src/preview/preview.css)，由预览入口显式导入；数据和验收边界保持独立。
- **按钮、输入与状态**：保留 Ant Design 行为与真实 disabled/loading 条件；焦点、失败说明和状态文字必须可读。
- **回答与来源**：正文、来源按钮、重试、共享摘要、图片和工具审批仍使用现有控制器；失效或未确认保存的语音正文按当前规则暂停展示。
- **回答排版**：文字与语音历史共用受限 Markdown 渲染，支持段落、标题、简单列表、强调、代码与表格；不执行模型 HTML，不将模型链接或图片转成可访问的外部资源。表格使用语义表头和可键盘聚焦的局部横向滚动区，手机不隐藏列、不撑宽页面。未知格式、超大历史回答回退为原始文字，不自动改写数值；来源仍由已保存且未失效的服务端记录提供。证据不足时展示已保存的具体说明及已核验来源，旧空记录保留提示，不标为完整回答。
- **语音阶段与记录**：桌面按阶段卡/真实记录双列排列，早前记录折叠。麦克风静音与播放开关分开表示；“播报音频送出中”不能推导实际听见。
- **回响环**：五段振幅仅取自已有 LiveKit 本地麦克风分析器；未连接、静音或分析不可用时归零，有限值限制在 `[0, 1]`。识别、生成及等待状态改变色调，不生成虚构音量。
- **图形降级与资源**：Three/R3F 懒加载且使用 `frameloop="demand"`；无 WebGL2、减少动态效果、加载/渲染错误或上下文丢失时使用 SVG。页面隐藏卸载 Canvas；可见恢复只恢复图形。Fiber 管理声明式几何、材质与 GPU 资源释放，环境监听配对清理。文字状态、字幕、来源及控件保留，唯一 VoiceController 继续拥有媒体生命周期。
- **许可与证据**：[LiveKit 布局参考](vendor/livekit/README.md) 与 [Three/R3F](vendor/three/README.md) 的 MIT 声明保留；自有内容沿用 [Apache-2.0](../LICENSE)。本文件依据源代码提取；图形测试替身、截图或构建不能替代真实 GPU、麦克风、供应商或业务库验收。

## Do's and Don'ts

### Do:

- **Do** 通过共享主题扩展正式页面，并保留消息与输入的共同阅读边界。
- **Do** 让阶段文字、音量与可用操作对应真实状态，保持键盘焦点和手机折行。
- **Do** 将来源、字幕、任务和通话操作保留为可访问的 DOM 控件。

### Don't:

- **Don't** 用回响环或播放轨道数量宣称实际听见、助手就绪或验收成功。
- **Don't** 在图形组件中申请麦克风、连接房间或建立第二套问答/媒体逻辑。
- **Don't** 将只读预览、合成响应或局部验证写成真实业务验收。
