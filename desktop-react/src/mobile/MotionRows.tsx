import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { DUR, EASE, RETAIN, RETAIN_MARGIN_MS } from "../motion/tokens";
import { playEntrance } from "./motion";
import { Crossfade } from "../components/Crossfade";

type Row<T> = { id: string; item: T; until: number | null };
/** Keep only disappearing rows for their fold. Live records come directly from props, so polling never replays
 * entrances or delays status/permission changes. A list that first appears is simply there; only a row added later
 * opens its own room, and a row that changes place glides to it. */
export function MotionRows<T>({ items, itemKey, children, as = "div" }: {
  items: readonly T[]; itemKey(item: T): string; children(item: T): ReactNode; as?: "div" | "li";
}) {
  const reduce = useReducedMotion();
  const ids = items.map(itemKey);
  const signature = JSON.stringify(ids);
  const [previous, setPrevious] = useState(signature);
  const [rows, setRows] = useState<Row<T>[]>(() => items.map(item => ({ id: itemKey(item), item, until: null })));
  const current = new Map(items.map(item => [itemKey(item), item]));
  const latest = useRef(current);
  for (const [id, item] of current) latest.current.set(id, item);
  let shown = rows;
  if (signature !== previous) {
    const now = performance.now();
    // Respect the live ordering (recent conversations move to the front). Only
    // exiting records keep an old slot while they give their height back.
    shown = items.map(item => ({ id: itemKey(item), item, until: null }));
    rows.forEach((row, index) => {
      if (!current.has(row.id)) shown.splice(Math.min(index, shown.length), 0, {
        ...row, item: latest.current.get(row.id) ?? row.item, until: row.until ?? now + RETAIN.fold + RETAIN_MARGIN_MS,
      });
    });
    setPrevious(signature); setRows(shown);
  }
  useEffect(() => {
    const retained = new Set(rows.map(row => row.id));
    for (const id of latest.current.keys()) if (!retained.has(id)) latest.current.delete(id);
    const exiting = rows.filter(row => row.until !== null);
    if (!exiting.length) return;
    if (reduce) { setRows(value => value.filter(row => row.until === null)); return; }
    const due = Math.min(...exiting.map(row => row.until!));
    const timer = window.setTimeout(() => setRows(value => value.filter(row => row.until === null || row.until > performance.now())), Math.max(0, due - performance.now()) + 8);
    return () => clearTimeout(timer);
  }, [rows, reduce]);

  const nodes = useRef(new Map<string, HTMLElement>());
  const places = useRef<{ order: string[]; tops: Map<string, number> } | null>(null);
  useLayoutEffect(() => {
    const before = places.current;
    const added = before ? ids.filter(id => !before.tops.has(id)) : [];
    if (added.length && !reduce) playEntrance(...added.map(id => nodes.current.get(id)));
    const tops = new Map<string, number>();
    for (const id of ids) { const node = nodes.current.get(id); if (node) tops.set(id, node.offsetTop); }
    if (before && !reduce) {
      // Rows only glide when the order of the ones that stayed changed; an insertion is the fold's to absorb.
      const stayed = ids.filter(id => before.tops.has(id));
      const was = before.order.filter(id => current.has(id));
      if (stayed.some((id, index) => id !== was[index])) {
        for (const id of stayed) {
          const delta = before.tops.get(id)! - tops.get(id)!;
          if (Math.abs(delta) > 1) nodes.current.get(id)?.animate([{ transform: `translateY(${delta}px)` }, { transform: "none" }], { duration: DUR.flow, easing: EASE.move });
        }
      }
    }
    places.current = { order: ids, tops };
    // `signature` is `ids`; `current` and `reduce` are read at the moment the list changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const Tag = as;
  return <>{shown.filter(row => !reduce || current.has(row.id)).map(row => <Tag
    key={row.id}
    ref={(node: HTMLElement | null) => { if (node) nodes.current.set(row.id, node); else nodes.current.delete(row.id); }}
    className="m-motion-row"
    data-open={current.has(row.id)}
    inert={!current.has(row.id)}
    aria-hidden={!current.has(row.id)}
  >
    <div className="m-motion-row-clip"><div className="m-motion-row-content">{children(current.get(row.id) ?? row.item)}</div></div>
  </Tag>)}</>;
}

/** A status that changes its words: the old and the new blur across on the same spot (opacity and blur, nothing moves).
 *  The wrapper stays, so a poll that changes nothing replays nothing. */
export function MotionValue({ value, children }: { value: string; children: ReactNode }) {
  return <span className="m-motion-value"><Crossfade soft identity={value}>{children}</Crossfade></span>;
}

type Face = { id: string; node: ReactNode; fresh: boolean };
/**
 * Words that change in place: the old ones go quickly while the new ones come up over them, in the same cell, so a
 * title never blinks through nothing. Whatever is drawn first is simply there.
 */
export function Swap({ id, children }: { id: string; children: ReactNode }) {
  const reduce = useReducedMotion();
  const [face, setFace] = useState<Face>({ id, node: children, fresh: false });
  const [leaving, setLeaving] = useState<Face[]>([]);
  let shown = face;
  if (face.id !== id) {
    shown = { id, node: children, fresh: true };
    setFace(shown);
    if (!reduce) setLeaving(list => [...list.filter(item => item.id !== face.id && item.id !== id), face]);
  } else if (face.node !== children) {
    shown = { ...face, node: children };
    setFace(shown);
  }
  useEffect(() => {
    if (!leaving.length) return;
    const timer = window.setTimeout(() => setLeaving([]), RETAIN.flow);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  return <span className="m-swap">
    {leaving.map(item => <span key={item.id} className="m-swap-face is-leaving" aria-hidden="true">{item.node}</span>)}
    <span key={shown.id} className={`m-swap-face${shown.fresh ? " is-fresh" : ""}`}>{shown.node}</span>
  </span>;
}
