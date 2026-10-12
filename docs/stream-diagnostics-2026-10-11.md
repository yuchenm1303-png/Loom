# Chat 流式异常诊断

本机同一会话两次 Ling-3.0-flash-VL 请求在短句之后产生 4972 / 5058 个换行，均记录输出 5136 token、5138 个分块、无工具参数分片，随后 `stop`。这些是 SDK/运行时观察，不能证明模型或网关哪一层产生了异常。未保存原始网络响应，不应宣称已确定模型根因。

新增请求字段 `requested_max_output_tokens` 区分实际发送的输出预算与上下文预留预算；null 表示请求没有指定输出上限，不表示上游没有默认上限。

OpenAI Chat Completions 流记录固定大小的 `stream_diagnostics`：原始 SDK delta 与规范化文本的字符/换行/空白计数、跨分块最长空白段，文本/推理/工具/工具参数分片数量，usage、结束标记及多 choice 分块数量。原始计数在文本规范化之前采集，帮助区分上游空白与本地累积放大。诊断不存正文、工具参数值、推理、凭据、截图、地址或原始响应；保留原有日志策略。

这份元数据随成功解析的 ModelResponse 进入 durable model_response / model_response_rejected 记录。未完整返回的流仍使用既有 timing/transport 错误记录；本改动不提供原始 SSE 抓包，也不宣称覆盖未到达 SDK 的网关数据。其他传输保持空诊断，不伪造 Chat 观测。

计数只用于定位，不过滤模型文字，不推断任务完成，不强制重试或续跑。`stop` 无调用仍结束回合；不得根据“让我检查”等关键词猜测动作。验证以伪造 SDK 分块运行真实适配器/聚合器，覆盖两次空白规模、空白后工具调用、累积快照、隐私与有界元数据及生产事件持久化。没有发送真实付费模型请求。下次复验应同时核对 request budget、raw/normalized shape、finish_reason、usage 与 step_id；上游终止原因须由网关/provider 日志确认。

## 生产网关追加核查

最新浏览器验收会话 `10e2d4f9-f82c-474c-9b48-b023ea513d3a` 的 step `4dec6324-3b81-433f-a203-4651070449a9` 于 2026-10-12 03:22:41 UTC 返回。请求没有指定输出上限；响应输出 5136 token、5138 分块，工具和工具参数分片均为 0，结束原因为 `stop`。原始 SDK 文本与规范化文本的统计完全一致：5156 字符、5090 换行、最长连续空白 5090 字符。因此这次空白不是 Loom 规范化过程累积放大造成的。

通过只读 SSH 核对了生产 `loom-model-gateway` 容器实际加载的 `LoomModelGateway.upstream_request`、`GatewayRequestHandler.do_POST` 和 `main`，不是仅依据仓库版本推断。请求 body 原样转发，响应按字节读写并 flush，没有设置输出 token 上限、解析 SSE 或改写 finish_reason。生产未设置上游地址覆盖，使用代码默认的官方 `https://api.ant-ling.com/v1`。生产文件与本地文件哈希不同；上述结论只针对已逐项查看的部署方法，不能声称整个版本相同。

对应时间段网关只有访问日志，存在多个并发的 HTTP 200 请求，没有关联 step 的请求 ID 或流式终止记录。HTTP 200 本身不能证明流完整成功。未重启服务、修改生产配置或发送真实模型请求。

[百灵流式文档](https://developer.ant-ling.com/zh-CN/docs/tutorials/streaming/) 列出了 stop、length、content_filter 等结束原因；[OpenAI 兼容接口文档](https://developer.ant-ling.com/zh-CN/docs/api-reference/openai/) 提供 Chat 接口说明。现有证据不能将三次相同的 5136 token 解释为已经确认的默认输出上限，更不能把其他接口的默认值套用到本次请求。

当前结论：已排除所检查的 Loom 输出上限、文本规范化放大和生产网关结束原因改写；异常在 SDK 收到的数据中已经存在。尚无法区分模型重复生成、官方推理服务的限制或其他上游终止机制。进一步定因需要供应商针对具体响应的追踪记录，或另行授权的真实请求复验。不得用关键词强制续跑掩盖此问题。
