import { useEffect, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "../motion/useReducedMotion";
import { RETAIN, RETAIN_MARGIN_MS } from "../motion/tokens";
import "./mobile-motion.css";

type Row<T> = { id: string; item: T; until: number | null };
/** Keep only disappearing rows for their fold. Live records come directly from
 * props, so polling never replays entrances or delays status/permission changes. */
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
  const Tag = as;
  return <>{shown.filter(row => !reduce || current.has(row.id)).map(row => <Tag key={row.id} className="m-motion-row" data-open={current.has(row.id)} inert={!current.has(row.id)} aria-hidden={!current.has(row.id)}>
    <div className="m-motion-row-clip"><div className="m-motion-row-content">{children(current.get(row.id) ?? row.item)}</div></div>
  </Tag>)}</>;
}

export function MotionValue({ value, children }: { value: string; children: ReactNode }) {
  return <span className="m-motion-value" key={value}>{children}</span>;
}
