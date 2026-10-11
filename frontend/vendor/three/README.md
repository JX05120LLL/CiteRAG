# 语音回响环与 Three.js

正式工作台和独立 `voice.html` 共用 `src/react/Voice.tsx`、`VoiceVisual.tsx` 与懒加载的 `VoiceThreeEcho.tsx`。回响环是本项目编写的视觉组件，没有复制演示助手、RAG 或媒体实现。

## 锁定版本与许可

| 依赖 | 版本 | 用途 | 许可 |
| --- | --- | --- | --- |
| `three` | `0.186.1` | WebGL 三维几何与渲染 | [MIT](./LICENSE-three) |
| `@react-three/fiber` | `9.8.1` | React 声明式 Three.js 渲染 | [MIT](./LICENSE-react-three-fiber) |
| `@types/three` | `0.186.0` | 编译期类型 | MIT |

具体版本与间接依赖固定在 `frontend/pnpm-lock.yaml`。接入时核对：Fiber `9.8.1` 的 React peer 为 `>=19 <19.4`，与项目的 React `19.3.0` 匹配。未引入 Drei、官方 React 通话组件或新的音频分析依赖。

`LICENSE-three` 来自已安装的 `three@0.186.1/LICENSE`；`LICENSE-react-three-fiber` 来自上游 [`v9.8.1/LICENSE`](https://github.com/pmndrs/react-three-fiber/blob/v9.8.1/LICENSE)。CiteRAG 自有代码仍按根目录 [Apache License 2.0](../../../LICENSE) 使用。

## 状态与资源边界

- 五段音量只来自既有 LiveKit **本地麦克风**分析器；没有创建第二个采集器或 `AudioContext`，也没有读取远端播放音量。
- 未连接、重连、挂断、静音或分析失败时，输入振幅归零。关闭播放独立于麦克风，不抑制正常输入。
- 识别、生成、发送播报音频与待审批改变颜色与文字状态，不生成虚构音量。发送音频、播放开关或轨道数量均不代表用户实际听见。
- Three 模块按语音页面需要懒加载，使用 `frameloop="demand"`，没有持续的装饰动画或自建 RAF。按颜色状态和五段频值进行语义 memo，相同频段不会因新数组而重复更新 Canvas；输入与颜色状态实际改变时更新。
- 减少动态效果偏好、不可用的 WebGL2、渲染错误或上下文丢失时退回 SVG；通话控件、字幕与来源继续工作。
- 懒加载完成到 WebGL 首帧之间继续展示 SVG 占位。仅在 Fiber 渲染循环结束、此 renderer 的真实 draw calls 大于零时撤下占位；`onCreated` 或 Canvas DOM 存在不代表已经绘制。首帧监听只报告一次，隐藏、卸载或失败后不接收旧回调。
- 页面隐藏时卸载 Canvas，恢复可见只恢复图形，不开始通话或重播音频。声明式几何、材质、Canvas 与 GPU 上下文由 Fiber 管理和卸载清理；可见性、偏好与上下文事件监听有对应清理。
- 打开页面和加载图形不申请麦克风、不签发 Token、不连接房间、不调用供应商。唯一 `VoiceController` 继续拥有媒体和通话生命周期。

## 核验依据

- [Fiber 安装与 React 主版本兼容](https://r3f.docs.pmnd.rs/getting-started/introduction)
- [按需渲染](https://r3f.docs.pmnd.rs/advanced/scaling-performance)
- [Fiber 对象卸载与资源释放](https://r3f.docs.pmnd.rs/api/objects#disposal)
- [Three.js 资源释放](https://threejs.org/manual/pages/how-to-dispose-of-objects.html)

`voiceVisualModel.test.ts` 检查真实状态到振幅映射；`VoiceVisual.test.tsx` 使用替身渲染器检查隐藏、迟到模块与首帧、StrictMode、偏好、错误回退及事件清理；`VoiceFirstFrame.test.tsx` 用真实首帧 guard 配替身 Fiber 循环检查初始化与实际绘制分开、一次性通知和监听清理；`VoiceThreeEcho.test.tsx` 用真实组件配替身 Canvas 检查重复频段不进入渲染器、音量与状态实际变化仍更新；`VoicePhase.test.tsx` 检查处理中、播报音频送出和等待状态不冒充助手就绪、关闭播放不关闭输入。它们不能证明真实 GPU、真实麦克风或供应商通话通过，浏览器与业务验证须单独记录。
