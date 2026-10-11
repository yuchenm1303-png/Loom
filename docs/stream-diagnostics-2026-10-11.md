# Chat 流式异常诊断

本机同一会话两次 Ling-3.0-flash-VL 请求在短句之后产生 4972 / 5058 个换行，均记录输出 5136 token、5138 个分块、无工具参数分片，随后 `stop`。这些是 SDK/运行时观察，不能证明模型或网关哪一层产生了异常。未保存原始网络响应，不应宣称已确定模型根因。

新增请求字段 `requested_max_output_tokens` 区分实际发送的输出预算与上下文预留预算；null 表示请求没有指定输出上限，不表示上游没有默认上限。

OpenAI Chat Completions 流记录固定大小的 `stream_diagnostics`：原始 SDK delta 与规范化文本的字符/换行/空白计数、跨分块最长空白段，文本/推理/工具/工具参数分片数量，usage、结束标记及多 choice 分块数量。原始计数在文本规范化之前采集，帮助区分上游空白与本地累积放大。诊断不存正文、工具参数值、推理、凭据、截图、地址或原始响应；保留原有日志策略。

这份元数据随成功解析的 ModelResponse 进入 durable model_response / model_response_rejected 记录。未完整返回的流仍使用既有 timing/transport 错误记录；本改动不提供原始 SSE 抓包，也不宣称覆盖未到达 SDK 的网关数据。其他传输保持空诊断，不伪造 Chat 观测。

计数只用于定位，不过滤模型文字，不推断任务完成，不强制重试或续跑。`stop` 无调用仍结束回合；不得根据“让我检查”等关键词猜测动作。验证以伪造 SDK 分块运行真实适配器/聚合器，覆盖两次空白规模、空白后工具调用、累积快照、隐私与有界元数据及生产事件持久化。没有发送真实付费模型请求。下次复验应同时核对 request budget、raw/normalized shape、finish_reason、usage 与 step_id；上游终止原因须由网关/provider 日志确认。
