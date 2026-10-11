# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

本地单用户，管理自己的知识库与聊天；首版没有管理员、注册或登录。

## Product Purpose

CiteRAG 让本地资料形成有原文可查的回答；文字、语音与图片输入共用现有问答服务。

## Capabilities and Constraints

- 正式工作台包含聊天、知识库/资料、持久任务、系统状态和语音入口；独立语音入口为 `voice.html`。
- 普通聊天与固定知识库聊天、图片观察、来源、共享摘要及可选工具/Agent 沿用现有 API 与控制器。
- UI 维护须保留聊天归属、库维护与修订、来源失效、媒体及附件生命周期规则；真实状态决定可用操作。
- 正式界面保留原有页面布局与交互，采用已确认的墨绿侧栏、暖白页面及浅柠绿选中配色；语音视觉沿用 Three.js 回响环，不引入静态原型中的另一套页面结构或业务逻辑。

## Brand Commitments

CiteRAG 名称、中文界面和无字「回响」Logo 沿用既定资产；视觉实现约定见 [DESIGN.md](DESIGN.md)。

## Evidence on Hand

[项目 README](../README.md) 与 [AGENTS.md](../AGENTS.md) 是公开范围和工作约定的依据；[前端 README](README.md) 说明入口与代码边界。本文件仅为上述已确认事实的索引。源码接线、合成测试或截图不代表真实模型、真人设备或实际业务库验收通过。
