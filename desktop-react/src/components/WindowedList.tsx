import { useCallback, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";

/** Variable-height rows: only the viewport and a small overscan own DOM. */
export function WindowedList<T extends { id: string }>({ items, scrollRef, renderItem, estimate = 54 }: {
  items: T[];
  scrollRef: RefObject<HTMLDivElement | null>;
  renderItem(item: T): ReactNode;
  estimate?: number;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<string, number>());
  const [revision, setRevision] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 800 });
  const offsets = useMemo(() => {
    const next = [0];
    for (const item of items) next.push(next[next.length - 1] + (heights.current.get(item.id) ?? estimate));
    return next;
  }, [items, estimate, revision]);

  // Binary search means scrolling does not scan every historical event.
  const rowAt = (position: number) => {
    let low = 0, high = items.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (offsets[middle + 1] <= position) low = middle + 1;
      else high = middle;
    }
    return low;
  };
  const start = Math.max(0, rowAt(viewport.top) - 8);
  const end = Math.min(items.length, rowAt(viewport.top + viewport.height) + 9);

  useLayoutEffect(() => {
    const ids = new Set(items.map(item => item.id));
    for (const id of heights.current.keys()) if (!ids.has(id)) heights.current.delete(id);
  }, [items]);

  useLayoutEffect(() => {
    const scroller = scrollRef.current, root = rootRef.current;
    if (!scroller || !root) return;
    let frame = 0;
    let origin = 0;
    const sync = () => {
      frame = 0;
      const top = Math.max(0, scroller.scrollTop - origin);
      const height = scroller.clientHeight;
      setViewport(previous => previous.top === top && previous.height === height ? previous : { top, height });
    };
    const measure = () => {
      origin = root.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      sync();
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(sync); };
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    measure();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      scroller.removeEventListener("scroll", onScroll);
    };
  }, [scrollRef]);

  const measured = useCallback((entries: ResizeObserverEntry[]) => {
    let changed = false;
    for (const entry of entries) {
      const id = (entry.target as HTMLElement).dataset.windowedId;
      const height = entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height;
      if (id && height > 0 && Math.abs((heights.current.get(id) ?? estimate) - height) > .5) {
        heights.current.set(id, height);
        changed = true;
      }
    }
    if (changed) setRevision(value => value + 1);
  }, [estimate]);

  useLayoutEffect(() => {
    const observer = new ResizeObserver(measured);
    rootRef.current?.querySelectorAll("[data-windowed-id]").forEach(row => observer.observe(row));
    return () => observer.disconnect();
  }, [start, end, items, measured]);

  return (
    <div ref={rootRef} className="windowed-list" role="list" style={{ position: "relative", height: offsets[items.length], overflowAnchor: "none" }}>
      <div style={{ position: "absolute", top: offsets[start], left: 0, right: 0 }}>
        {items.slice(start, end).map((item, index) => (
          <div key={item.id} role="listitem" aria-posinset={start + index + 1} aria-setsize={items.length}
            data-windowed-id={item.id} data-windowed-last={start + index === items.length - 1 || undefined}
            style={{ display: "flow-root" }}>{renderItem(item)}</div>
        ))}
      </div>
    </div>
  );
}
