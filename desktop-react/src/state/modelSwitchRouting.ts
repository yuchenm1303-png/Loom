/**
 * Route model changes by conversation lifetime.
 *
 * Draft conversations have no thread ID and update the default model.
 * Persisted conversations MUST use the thread-scoped Host endpoint so the
 * switch changes this conversation without rewriting every other thread.
 */
export interface ModelSwitchBridge {
  switchModelProfile<T>(first: string, second?: string): Promise<T>;
  switchCurrentModel<T>(first: string, second?: string, third?: string): Promise<T>;
}

export function switchModelProfileForThread<T>(
  bridge: ModelSwitchBridge,
  threadId: string | undefined,
  selection: string,
): Promise<T> {
  const target = selection.trim();
  if (!target) throw new Error("Select a model first.");
  return threadId
    ? bridge.switchModelProfile<T>(threadId, target)
    : bridge.switchModelProfile<T>(target);
}

export function switchCurrentModelForThread<T>(
  bridge: ModelSwitchBridge,
  threadId: string | undefined,
  selection: string,
  model: string,
): Promise<T> {
  const target = model.trim();
  if (!target) throw new Error("Enter a model ID first.");
  if (threadId && !selection.trim()) {
    throw new Error("No model profile is associated with this conversation.");
  }
  return threadId
    ? bridge.switchCurrentModel<T>(threadId, selection, target)
    : bridge.switchCurrentModel<T>(target);
}
