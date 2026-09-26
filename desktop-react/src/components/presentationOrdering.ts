type PresentationBlock =
  | { kind: "item"; item: { id: string; type: string } }
  | { kind: "activity" };

/** Hold only activity after unfinished prose; approvals and prose stay mounted. */
export function deferredActivityIndices(blocks: readonly PresentationBlock[], pending: ReadonlySet<string>): Set<number> {
  const deferred = new Set<number>();
  let prosePending = false;
  blocks.forEach((block, index) => {
    if (block.kind === "item" && block.item.type === "assistant_message") {
      prosePending ||= pending.has(`${block.item.id}:answer`) || pending.has(`${block.item.id}:reasoning`);
    }
    if (block.kind === "activity" && prosePending) deferred.add(index);
  });
  return deferred;
}
