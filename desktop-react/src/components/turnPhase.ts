/** Runtime facts, shared by the waiting capsule and the progress strip. */
export function turnPhaseLabel(phase: string | undefined, language: string): string | undefined {
  const labels: Record<string, [string, string]> = {
    preparing: ["正在准备对话…", "Preparing conversation…"],
    compacting: ["正在整理历史上下文…", "Summarizing conversation history…"],
    waiting_model: ["正在等待模型响应…", "Waiting for the model…"],
    retrying: ["正在重新连接模型…", "Reconnecting to the model…"],
  };
  const label = labels[phase ?? ""];
  return label?.[language === "zh" ? 0 : 1];
}
