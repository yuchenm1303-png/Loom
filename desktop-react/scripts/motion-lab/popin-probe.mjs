// Motion lab: the pop-in probe, shared by tour.mjs and popin.mjs.
// Installed into a page before it loads, it watches the DOM for what appears and what goes with nothing to carry it:
//   pop-in:  a sizeable element added to the page that, two frames later, is visible and has no animation or transition
//            on it, on any ancestor, or on any descendant (it is simply there);
//   pop-out: a sizeable element removed while nothing had been animating on it or above it for a quarter second (an
//            exit keeps its node on screen while it plays, so a node that goes without one went at once).
// window.__popin.on switches it; window.__popin.take() returns and clears what it has found.
export function popinProbe() {
  const state = { on: false, popins: [], popouts: [], take() { return { popins: this.popins.splice(0), popouts: this.popouts.splice(0) }; } };
  window.__popin = state;
  const describe = (el) => {
    const cls = String(el.className?.baseVal ?? el.className ?? "").trim().split(/\s+/).filter(Boolean).slice(0, 3).join(".");
    return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}`;
  };
  const hidden = (el) => {
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) < 0.05) return true;
    }
    return false;
  };
  const animatedAbove = (el) => {
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) if (node.getAnimations().length) return true;
    return el.getAnimations({ subtree: true }).length > 0;
  };
  const boxes = new Map();
  new MutationObserver((records) => {
    if (!state.on) return;
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1 || !node.isConnected) continue;
        const rect = node.getBoundingClientRect();
        if (rect.width * rect.height < 900 || rect.width < 24 || rect.height < 14) continue;
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (!node.isConnected || !state.on || hidden(node)) return;
          if (!animatedAbove(node)) state.popins.push({ what: describe(node), parent: describe(node.parentElement ?? document.body), w: Math.round(rect.width), h: Math.round(rect.height) });
        }));
      }
      for (const node of record.removedNodes) {
        if (node.nodeType !== 1 || !record.target.isConnected) continue;
        const before = boxes.get(node);
        if (before && before.w * before.h >= 900 && performance.now() - before.animatedAt > 260) {
          state.popouts.push({ what: describe(node), parent: describe(record.target), w: Math.round(before.w), h: Math.round(before.h) });
        }
      }
    }
  }).observe(document, { childList: true, subtree: true });
  // Sizes, and whether something was animating, for what is on screen, so a node removed later still has a known box.
  setInterval(() => {
    if (!state.on) return;
    const now = performance.now();
    for (const el of document.querySelectorAll(".turn-block *, .composer-wrap *, .sidebar *, .settings-host *, .inspector *, [role=dialog], [role=menu], .thread-context-menu, .composer-popover *, .conversation-stage *")) {
      const rect = el.getBoundingClientRect();
      if (rect.width * rect.height < 900 || rect.top >= innerHeight || rect.bottom <= 0) continue;
      const known = boxes.get(el);
      boxes.set(el, { w: rect.width, h: rect.height, animatedAt: animatedAbove(el) ? now : (known?.animatedAt ?? 0) });
    }
  }, 90);
}

/** Fold what one step found into running tallies. */
export function tally(maps, found, label) {
  for (const key of ["popins", "popouts"]) {
    for (const item of found[key]) {
      const id = `${item.what}  (${key === "popins" ? "in" : "from"} ${item.parent})`;
      const entry = maps[key].get(id) ?? { steps: new Set(), w: item.w, h: item.h };
      entry.steps.add(label);
      maps[key].set(id, entry);
    }
  }
}

export function report(maps) {
  const lines = (map) => [...map.entries()].sort((a, b) => b[1].w * b[1].h - a[1].w * a[1].h).slice(0, 40)
    .map(([key, entry]) => `  ${String(entry.w).padStart(4)}x${String(entry.h).padEnd(4)} ${key.slice(0, 92).padEnd(92)} ${[...entry.steps].join(", ")}`);
  console.log("\nappeared with no animation on it, above it or inside it:");
  for (const line of lines(maps.popins)) console.log(line);
  console.log("\nremoved at once (nothing had been animating on it for a quarter second):");
  for (const line of lines(maps.popouts)) console.log(line);
}
