type PresentationBlock =
  | { kind: "item"; item: { id: string; type: string } }
  | { kind: "activity"; items?: readonly { id: string }[] };

/** Hold only activity after unfinished prose; approvals and prose stay mounted. */
export function deferredActivityIndices(
  blocks: readonly PresentationBlock[],
  pending: ReadonlySet<string>,
  revealed: ReadonlySet<string> = new Set(),
): Set<number> {
  const deferred = new Set<number>();
  let prosePending = false;
  blocks.forEach((block, index) => {
    if (block.kind === "item" && block.item.type === "assistant_message") {
      prosePending ||= pending.has(`${block.item.id}:answer`) || pending.has(`${block.item.id}:reasoning`);
    }
    // Deferral controls the first entrance only. A later streaming chunk must
    // not unmount an existing group and replay its entrance or reset disclosure.
    if (block.kind === "activity" && prosePending
      && !block.items?.some((item) => revealed.has(item.id))) deferred.add(index);
  });
  return deferred;
}
