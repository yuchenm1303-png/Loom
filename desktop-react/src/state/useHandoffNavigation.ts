import { useEffect, useRef, useState } from "react";
import { openHandoff, type HandoffNavigation, type ThreadHandoff } from "./threadHandoff";

export function useHandoffNavigation(threadId: string | undefined | readonly string[], navigation: HandoffNavigation) {
  const ids: readonly string[] = typeof threadId === "string" ? [threadId] : threadId || [];
  const current = useRef<readonly string[]>(ids);
  current.current = ids;
  const actions = useRef(navigation);
  actions.current = navigation;
  const handled = useRef(new Set<string>());
  const [error, setError] = useState({ source: "", message: "" });
  useEffect(() => window.loom.onNotification(message => {
    if (message.method !== "thread/handoff") return;
    const record = message.params?.handoff as ThreadHandoff | undefined;
    if (!record?.id || record.created === false || !current.current.includes(record.sourceThreadId) || record.display === "background" || handled.current.has(record.id)) return;
    handled.current.add(record.id);
    if (handled.current.size > 128) handled.current.delete(handled.current.values().next().value!);
    void openHandoff(record, actions.current, window.loom).catch(cause => setError({ source: record.sourceThreadId, message: cause instanceof Error ? cause.message : String(cause) }));
  }), []);
  return ids.includes(error.source) ? error.message : "";
}
