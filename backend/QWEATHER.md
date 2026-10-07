# 和风天气工具

天气适配器复用现有 httpx/Pydantic、ToolGateway、审批和 LangGraph 循环，不新增依赖、数据库 schema、HTTP 接口、RAG 实例或聊天历史。两类聊天均可使用，结果标记 `source_type=tool`，不成为知识库引用。

## 配置

默认关闭。只有显式启用且 Host/Key 齐全时加入目录；注册、配置 Key 或打开页面不会发送请求。

| 后端变量 | 作用 | 默认 |
| --- | --- | --- |
| `CITERAG_QWEATHER_ENABLED` | 注册天气工具 | `false` |
| `CITERAG_QWEATHER_API_HOST` | 控制台「设置」中的专属主机名，不含协议/端口/路径 | 未配置 |
| `CITERAG_QWEATHER_API_KEY` | 受控后端 API Key，以 SecretStr 保存 | 未配置 |

可按 [本地天气配置](QWEATHER-LOCAL-CONFIG.md) 使用已存在的受忽略 `backend/.env.weather`，启动时自动加载。也可在**启动后端的同一 PowerShell 会话**中设置环境变量。不要把真实 Key 粘贴到聊天、代码、命令参数、Git 或前端；本项目不会读取通用 `.env`。

```powershell
$env:CITERAG_QWEATHER_ENABLED = 'true'
$env:CITERAG_QWEATHER_API_HOST = Read-Host '和风 API Host（仅主机名）'
$qweatherSecureKey = Read-Host '和风 API Key' -AsSecureString
$env:CITERAG_QWEATHER_API_KEY = [System.Net.NetworkCredential]::new('', $qweatherSecureKey).Password
uv run --no-env-file uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

先确认端口空闲并准备隔离业务库。已有后端须关闭自己启动的服务，再向新进程注入配置；不要停止未知服务。退出后端后可用 `Remove-Item Env:CITERAG_QWEATHER_API_KEY` 清理本会话 Key，移除其余两个变量与 `qweatherSecureKey`；不输出变量内容。

手动「工具与调用记录」填参数并审批即可调用，不依赖回答模型。自动模型选工具另需显式启用原 Agent/回答模型及其检查点配置；天气本身无需 MCP extra。新变量不修改已有默认值；关闭天气开关可撤回注册，历史记录继续可查看。本轮没有迁移实际业务库。

## 工具与协议

| ID | 参数 | 供应商接口 |
| --- | --- | --- |
| `weather.city_search` | `location` 城市名/LocationID，可选 `adm` 上级行政区 | `/geo/v2/city/lookup`，最多 5 个候选 |
| `weather.current` | `latitude`、`longitude` | `/weather/v1/current/{latitude}/{longitude}` |
| `weather.forecast` | 经纬度；`days` 默认 3，范围 1—7 | `/weather/v1/daily/{latitude}/{longitude}` |

没有明确地点先补充；重名城市保留候选，不在适配器中选择首项。天气坐标应来自用户明确输入或城市搜索确认，不猜测用户所在地。按供应商协议将坐标四舍五入至两位小数，纬度在前、经度在后，审批与结果保留实际查询坐标。模型能否正确选择和澄清需真实验收。

每次工具调用最多 **1 次供应商 GET 请求**，无自动重试；搜城市再查天气通常为 2 次，每次独立审批。仅发送地点、语言和预报天数，不发送聊天正文、知识库正文或文件。Key 仅通过 `X-QW-Api-Key` Header 发送。固定 HTTPS Host 限制在专属 `qweatherapi.com` 域名，不使用系统代理、不跟随重定向。

执行器是本地 Python 适配器（`backend=local`），目标明确为 QWeather；这不表示没有外发。Host 改变会改变工具版本，使旧审批失效。目录、模型上下文、审批与持久记录不包含 Key 或专属 Host。提供凭证不等于授权助手主动探测；调用受实际数据、费用和用户审批范围约束。

## 结果、失败与取消

- 只保存校验后的天气字段、单位、应用查询时间、预报 UTC 区间、候选和归属声明；不保存原始 HTTP 响应、Header 或供应商诊断。
- 湿度和降水概率从 `[0,1]` 转为明确百分比；保留供应商单位。`queried_at` 不代表供应商观测/更新时间，历史结果只反映当时查询。
- 页面同时显示结果与 `metadata.attributions`；必需归属信息缺失则失败。远端内容是不可信数据，不能执行 HTML 或成为模型指令。
- 拒绝额外字段、布尔/非有限/越界坐标、不支持天数、无效格式、凭证回显和超过 64 KiB 的解压响应；不把失败写成成功。
- 401 认证失败；403 提示检查额度、权限、Host 和请求限制；429 限流；超时、跳转、无地点和协议不兼容分别保存安全错误代码。页面给出建议，不编造天气降级结果。
- 沿用现有持久审批、拒绝、幂等、取消、重启中断和聊天保留期。取消实际异步 HTTP 工作；不自动重放。Agent 保持最多 6 决策轮、4 工具尝试、60 秒活动预算，暂停/恢复不重置预算。
- 手动审批请求等待上限为 20 秒，覆盖网关至多 15 秒的工具执行预算；前端连接中断不代表供应商未执行，先刷新持久记录，不自动重新提交。

## 高德官方 MCP

高德已有官方 MCP，推荐 Streamable HTTP：`https://mcp.amap.com/mcp?key=<高德 Web 服务 Key>`，另有 Node.js I/O。高德与和风的 Key 不通用。

当前 MCP 适配器拒绝带查询参数的端点，尚未实现高德认证注入，**不要把带 Key 的完整地址写入 registry.json**。后续应固定无凭证端点，在受控请求中注入 Key，审查实际服务版本、descriptor 和只读白名单。本轮未注册或调用真实高德。

## 验证与集中验收

公开测试使用合成 HTTP 响应、实际 API/网关/LangGraph 和隔离 PostgreSQL；浏览器也使用隔离合成边界。覆盖缺配置、聊天范围、审批前零请求、幂等/拒绝、实际异步取消、持久失败、模型替身取得工具结果后保存普通回答且无知识库引用。真实 Key、账户额度、新接口支持和模型规划均未因此通过。

1. 在受控后端配置 Host/Key/开关，打开隔离聊天，确认出现三个天气工具。
2. 手动搜明确城市并审批，核对行政区与坐标；歧义先确认。
3. 查实况与 3 天预报，各自审批；核对真实单位、UTC 区间、归属和查询时间。
4. 经另外授权启用回答模型/Agent，测「杭州现在天气怎么样」的城市搜索 → 审批 → 天气 → 审批 → 普通回答与空知识库引用。
5. 测拒绝、取消、无效 Key 和供应商限流，确认不自动重试、不伪造结果、不回显凭证。

官方资料：[API Host](https://dev.qweather.com/docs/configuration/api-host/) · [认证](https://dev.qweather.com/docs/configuration/authentication/) · [城市搜索](https://dev.qweather.com/docs/api/geoapi/city-lookup/) · [实况](https://dev.qweather.com/docs/api/weather/weather-current/) · [每日预报](https://dev.qweather.com/docs/api/weather/weather-daily-forecast/) · [错误码](https://dev.qweather.com/docs/resource/error-code/) · [高德 MCP](https://lbs.amap.com/api/mcp-server/gettingstarted)。
