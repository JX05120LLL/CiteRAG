# 本地 Silero VAD

`app/voice/vad.py` 的 ONNX 状态、64 sample context 与 512 sample 输入适配依据 [Silero v6.2.1 官方实现](https://github.com/snakers4/silero-vad/blob/v6.2.1/src/silero_vad/utils_vad.py)。保留 [MIT 许可](LICENSE)。不复制 Torch、下载器或语音助手。

模型由操作者从固定官方版本下载到 `.local/`，不随仓库发布，不运行时下载。SHA256：`1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`。错版、缺失或过大模型拒绝启动 worker。模型与包装器的许可相同；本地验收不证明真人声学质量。
