# 上游参考与固定版本

以下为技术文档已采用、并在本地参考克隆中核对的提交，不表示当前最新版，也不表示已完成本项目运行验证。

| 项目 | 固定提交 | 用途 |
| --- | --- | --- |
| [LightRAG](https://github.com/HKUDS/LightRAG) | [`59af311307c7417b342f44850b097648d47e83bd`](https://github.com/HKUDS/LightRAG/tree/59af311307c7417b342f44850b097648d47e83bd) | 计划以 SDK 封装入库、检索与删除 |
| [LiveRAG](https://github.com/YS-BW/LiveRAG) | [`208eede49e76cbb00ed85ee644576bd2520ce8f6`](https://github.com/YS-BW/LiveRAG/tree/208eede49e76cbb00ed85ee644576bd2520ce8f6) | 架构组织、实时语音与界面参考 |

本仓库不复制两个项目的源码或依赖环境。后续锁定 LightRAG 依赖并集成时，单独核对所用版本的许可、接口与兼容性。LiveRAG 锁定的引擎版本与本项目基线不同，不能跨版本照抄初始化参数。

本地历史设计、学校材料与旧多企业 PRD 不随公开仓库发布。学校的实验要求由开发者与导师另行核对，不把未公开材料的本地路径作为公共文档链接。
