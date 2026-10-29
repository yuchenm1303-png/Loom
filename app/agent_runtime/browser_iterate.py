"""The edit-look loop: reload a preview, see what an edit changed, and measure motion."""
from __future__ import annotations

import json
import statistics
import time
import uuid
from io import BytesIO
from pathlib import Path

from .contracts import ToolEffect
from .tools import AgentTool, ToolExposure, ToolResult

_SETTLE_SCRIPT = """(async () => {
  const settle = %(settle)d, selector = %(selector)s;
  if (document.readyState !== "complete") {
    await Promise.race([new Promise(r => addEventListener("load", r, {once: true})),
                        new Promise(r => setTimeout(r, 10000))]);
  }
  try { await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 3000))]); } catch (_) {}
  // A page that is not on screen (another tab in front) paints no frames.
  let painted = false;
  await Promise.race([new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => { painted = true; r(); }))),
                      new Promise(r => setTimeout(r, 1500))]);
  await new Promise(r => setTimeout(r, settle));
  let box = null;
  if (selector) {
    const element = document.querySelector(selector);
    if (!element) box = false;
    else { const r = element.getBoundingClientRect(); box = {x: r.left, y: r.top, width: r.width, height: r.height}; }
  }
  return {width: innerWidth, height: innerHeight, dpr: devicePixelRatio, box, painted,
          visibility: document.visibilityState};
})()"""

# Installed before the trigger, read back after: every rAF timestamp, long
# tasks and layout shifts, all in the page's own clock.
_RECORD_SCRIPT = """(() => {
  const old = window.__loomMotion; if (old) old.stop();
  const m = {start: performance.now(), frames: [], longTasks: [], shift: 0, observers: [], running: true};
  const tick = t => { m.frames.push(t); if (m.running) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  for (const [type, take] of [["longtask", e => m.longTasks.push([e.startTime, e.duration])],
                              ["layout-shift", e => { if (!e.hadRecentInput) m.shift += e.value; }]]) {
    try { const o = new PerformanceObserver(l => l.getEntries().forEach(take)); o.observe({type, buffered: false});
          m.observers.push(o); } catch (_) {}
  }
  m.stop = () => { m.running = false; m.observers.forEach(o => o.disconnect()); };
  window.__loomMotion = m;
  return {visibility: document.visibilityState};
})()"""

_MARK_SCRIPT = "(() => { const m = window.__loomMotion; if (!m) return false; m.trigger = performance.now(); return true; })()"

_ANIMATIONS_SCRIPT = """(() => {
  const name = e => !e ? "" : e.tagName.toLowerCase() + (e.id ? "#" + e.id : "")
    + [...(e.classList || [])].slice(0, 3).map(c => "." + c).join("");
  return document.getAnimations().slice(0, 24).map(a => {
    const t = a.effect && a.effect.getTiming ? a.effect.getTiming() : {};
    return {kind: a.constructor.name, target: name(a.effect && a.effect.target),
            name: a.transitionProperty || a.animationName || a.id || "",
            duration_ms: typeof t.duration === "number" ? Math.round(t.duration) : null,
            delay_ms: Math.round(t.delay || 0), easing: t.easing || "", state: a.playState};
  });
})()"""

_COLLECT_SCRIPT = """(() => {
  const m = window.__loomMotion; if (!m) return null; m.stop(); window.__loomMotion = null;
  return {start: m.start, trigger: m.trigger ?? null, end: performance.now(), frames: m.frames,
          longTasks: m.longTasks, shift: m.shift, visibility: document.visibilityState};
})()"""


def _evaluate(store, context, browser_id, expression):
    item = store._owned(context.session_id, browser_id)
    runner = getattr(item.backend, "evaluate", None)
    if not callable(runner):
        raise RuntimeError(f"browser backend {item.backend.backend_name!r} cannot run page scripts")
    outcome = dict(runner(expression, await_promise=True))
    if not outcome.get("ok"):
        raise RuntimeError("page script failed: " + str(outcome.get("error") or "unknown error")[:500])
    value = outcome.get("value")
    if isinstance(value, str) and outcome.get("truncated") is not True:
        # Some bridges hand JSON back as text.
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return value


def _page_key(url: str, selector: str) -> str:
    return url.split("#", 1)[0] + "\n" + selector


def _compare(before, after):
    """Changed share of pixels and their bounding box, or None when sizes differ."""
    from PIL import ImageChops
    if before.size != after.size:
        return None
    difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB")).convert("L")
    # Ignore anti-aliasing and compression noise.
    mask = difference.point(lambda value: 255 if value > 12 else 0)
    box = mask.getbbox()
    changed = sum(mask.histogram()[255:]) / float(after.size[0] * after.size[1])
    return changed, box


def _side_by_side(before, after, box):
    from PIL import Image, ImageDraw
    gap, label = 12, 24
    width = before.width + after.width + gap
    height = max(before.height, after.height) + label
    sheet = Image.new("RGB", (width, height), "#202225")
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 4), "previous look", fill="white")
    draw.text((before.width + gap + 8, 4), "now (changed region outlined)", fill="white")
    sheet.paste(before.convert("RGB"), (0, label))
    sheet.paste(after.convert("RGB"), (before.width + gap, label))
    if box:
        x0, y0, x1, y1 = box
        offset = before.width + gap
        draw.rectangle((offset + x0, label + y0, offset + x1 - 1, label + y1 - 1), outline="#ff3b6b", width=3)
    return sheet


def browser_look_tool(runtime):
    looks: dict[tuple[str, str, str], dict] = {}

    def look(context, arguments):
        from PIL import Image
        context.raise_if_cancelled()
        store = runtime.browser_sessions
        if store is None:
            raise RuntimeError("browser runtime is unavailable")
        browser_id = arguments["browser_id"]
        selector = str(arguments.get("selector") or "").strip()
        settle = int(arguments.get("settle_ms", 300))
        with store.operation(context.session_id, browser_id):
            target = str(arguments.get("url") or "").strip()
            if not target:
                target = store.state(context.session_id, browser_id).url
                if not target or target == "about:blank":
                    raise ValueError("this browser has no page yet; pass url")
            if target != store.snapshot(context.session_id, browser_id).state.url or arguments.get("reload", True):
                store.navigate(context.session_id, browser_id, target)
            context.raise_if_cancelled()
            try:
                page = _evaluate(store, context, browser_id,
                                 _SETTLE_SCRIPT % {"settle": settle, "selector": json.dumps(selector or None)})
            except RuntimeError as exc:
                page = {"script_error": str(exc)[:300]}
            if not isinstance(page, dict) or "width" not in page:
                # No page scripts, or a bridge that does not await promises:
                # the look still works, it only waits a fixed time.
                time.sleep(settle / 1000 + 0.5)
            raw = store.screenshot(context.session_id, browser_id, full_page=False)
            snapshot = store.snapshot(context.session_id, browser_id)
        page = page if isinstance(page, dict) else {}
        image = Image.open(BytesIO(raw))
        image.load()
        notes = []
        if selector:
            box = page.get("box")
            if box is False:
                notes.append(f"No element matches {selector!r}; showing the whole viewport.")
            elif isinstance(box, dict) and page.get("width"):
                scale = image.width / float(page["width"])
                pad = 16 * scale
                left = max(0, int(box["x"] * scale - pad))
                top = max(0, int(box["y"] * scale - pad))
                right = min(image.width, int((box["x"] + box["width"]) * scale + pad))
                bottom = min(image.height, int((box["y"] + box["height"]) * scale + pad))
                if right - left >= 4 and bottom - top >= 4:
                    image = image.crop((left, top, right, bottom))
                else:
                    notes.append(f"{selector!r} has no visible area; showing the whole viewport.")
        if page.get("visibility") == "hidden" or page.get("painted") is False:
            notes.append("The page is not on screen (hidden, or another tab is in front) and painted no frames, "
                         "so the image may not show the latest state.")

        directory = Path("browser-screenshots") / "looks"
        output = context.resolve_workspace_path(directory.as_posix())
        output.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:6]
        now_path = (directory / f"look-{stamp}.png").as_posix()
        image.save(context.resolve_workspace_path(now_path))

        key = (context.session_id, browser_id, _page_key(snapshot.state.url or target, selector))
        previous = looks.get(key)
        attach = [{"path": now_path, "label": "Current look"}]
        comparison = {"first_look": previous is None}
        if previous is None:
            summary = "First look at this page; later looks of it are compared with this one."
        else:
            try:
                with Image.open(context.resolve_workspace_path(previous["path"])) as before:
                    before.load()
                    result = _compare(before, image)
                    sheet = _side_by_side(before, image, result[1] if result else None)
            except (OSError, ValueError):
                result, sheet = None, None
            age = round(time.monotonic() - previous["at"])
            if sheet is None:
                summary = "The previous look could not be read; this is a fresh baseline."
            elif result is None:
                summary = (f"Viewport size changed since the previous look {age}s ago "
                           f"({previous['size'][0]}x{previous['size'][1]} -> {image.width}x{image.height}); "
                           "pixels were not compared.")
            else:
                changed, box = result
                comparison.update(changed_ratio=round(changed, 5), changed_box=list(box) if box else None,
                                  previous_path=previous["path"], seconds_since_previous=age)
                if not box:
                    summary = (f"Identical to the previous look {age}s ago. If you edited this view since then, "
                               "the edit did not reach the page: check that the server serves this checkout and "
                               "rebuilt, that the page reloaded, and that the changed rule applies in this state.")
                else:
                    summary = (f"{changed:.2%} of pixels changed since the previous look {age}s ago, within "
                               f"x={box[0]} y={box[1]} w={box[2] - box[0]} h={box[3] - box[1]}. Check that this is "
                               "the change you intended and nothing else moved.")
            if sheet is not None:
                compare_path = (directory / f"compare-{stamp}.png").as_posix()
                sheet.save(context.resolve_workspace_path(compare_path))
                attach = [{"path": compare_path, "label": "Previous look beside the current one"}]
                comparison["compare_path"] = compare_path
        looks[key] = {"path": now_path, "at": time.monotonic(), "size": [image.width, image.height]}
        visual = runtime.visual_feedback.stage(context, attach)
        return ToolResult(True, " ".join([summary, *notes]) + " Inspect the attached image.",
                          {**snapshot.to_dict(), **visual, "look": {"path": now_path, **comparison,
                           "viewport": {k: page.get(k) for k in ("width", "height", "dpr")}}})

    return AgentTool("browser_look",
        "Look at a page while you edit it: reload it (reload=false keeps in-page state, e.g. with hot reload), "
        "wait until it has loaded, fonts are ready and two frames have painted, screenshot it, and attach the "
        "image. From the second look on, the same page (and selector) is compared with the previous look: the "
        "result says what share of pixels changed and where, attaches both side by side, and says so when "
        "nothing changed, which means the edit did not reach the page. selector crops to one element. Use it "
        "after each UI edit instead of browser_screenshot + view_images.",
        {"type": "object", "additionalProperties": False, "properties": {
            "browser_id": {"type": "string", "minLength": 1},
            "url": {"type": "string", "maxLength": 8000},
            "reload": {"type": "boolean"},
            "selector": {"type": "string", "maxLength": 500},
            "settle_ms": {"type": "integer", "minimum": 0, "maximum": 5000}},
         "required": ["browser_id"]},
        look, effect=ToolEffect.SENSITIVE)


def _motion_stats(record, budget_hint=None):
    frames = [float(t) for t in record.get("frames") or []]
    intervals = [b - a for a, b in zip(frames, frames[1:])]
    if len(intervals) < 3:
        return {"valid": False, "frames": len(frames),
                "reason": "the page painted fewer than 4 frames: it is hidden, in a background tab, or blocked"}
    budget = budget_hint or statistics.median(intervals)
    if budget > 50:
        return {"valid": False, "frames": len(frames), "median_frame_ms": round(budget, 2),
                "reason": "frames arrive slower than 20 per second: the page is throttled (hidden or background), "
                          "so smoothness cannot be measured"}
    dropped = sum(max(0, round(i / budget) - 1) for i in intervals if i > budget * 1.5)
    worst = max(range(len(intervals)), key=intervals.__getitem__)
    ordered = sorted(intervals)
    origin = record.get("trigger") if record.get("trigger") is not None else record.get("start")
    long_tasks = record.get("longTasks") or []
    return {"valid": True, "frames": len(frames), "refresh_hz": round(1000 / budget, 1),
            "median_frame_ms": round(budget, 2), "p95_frame_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 2),
            "longest_frame_ms": round(intervals[worst], 2),
            "longest_frame_at_ms": round(frames[worst] - float(origin), 1),
            "dropped_frames": int(dropped),
            "long_tasks": len(long_tasks), "long_task_ms": round(sum(d for _, d in long_tasks), 1),
            "layout_shift": round(float(record.get("shift") or 0), 4)}


def browser_measure_motion_tool(runtime):
    def measure(context, arguments):
        from .browser_tools import _backend_snapshot_action
        context.raise_if_cancelled()
        store = runtime.browser_sessions
        if store is None:
            raise RuntimeError("browser runtime is unavailable")
        browser_id = arguments["browser_id"]
        trigger = arguments.get("trigger")
        duration = int(arguments.get("duration_ms", 1000))
        with store.operation(context.session_id, browser_id):
            if trigger:
                store.ensure_revision(context.session_id, browser_id, arguments["state_revision"])
            installed = _evaluate(store, context, browser_id, _RECORD_SCRIPT)
            time.sleep(0.1)  # a few frames of baseline before the trigger
            started = time.monotonic()
            if trigger:
                _evaluate(store, context, browser_id, _MARK_SCRIPT)
                if trigger["type"] == "click":
                    store.click(context.session_id, browser_id, trigger["index"])
                else:
                    _backend_snapshot_action(store, context.session_id, browser_id, "press_key", trigger["key"])
            sample_at = min(0.15, duration / 3000)
            while time.monotonic() - started < sample_at:
                context.raise_if_cancelled()
                time.sleep(0.02)
            animations = _evaluate(store, context, browser_id, _ANIMATIONS_SCRIPT)
            while time.monotonic() - started < duration / 1000:
                context.raise_if_cancelled()
                time.sleep(0.02)
            record = _evaluate(store, context, browser_id, _COLLECT_SCRIPT)
            snapshot = store.snapshot(context.session_id, browser_id, refresh=bool(trigger))
        if not isinstance(record, dict):
            raise RuntimeError("the frame recorder was lost (the page navigated or reloaded during the measurement)")
        stats = _motion_stats(record)
        animations = animations if isinstance(animations, list) else []
        if not stats["valid"]:
            summary = "No valid measurement: " + stats["reason"] + "."
        else:
            summary = (f"{stats['frames']} frames at ~{stats['refresh_hz']} Hz; {stats['dropped_frames']} dropped; "
                       f"longest frame {stats['longest_frame_ms']} ms at {stats['longest_frame_at_ms']} ms after "
                       f"{'the trigger' if trigger else 'the start'}; p95 {stats['p95_frame_ms']} ms; "
                       f"{stats['long_tasks']} long tasks ({stats['long_task_ms']} ms); layout shift {stats['layout_shift']}.")
        summary += f" {len(animations)} animations were running {round(sample_at * 1000)} ms in."
        if (installed or {}).get("visibility") == "hidden":
            summary += " The page reported itself hidden."
        return ToolResult(stats["valid"], summary + " Timings come from the page's own frame clock; an automation "
                          "overlay or a busy machine adds cost of its own, so compare runs taken the same way.",
                          {**snapshot.to_dict(), "motion": {**stats, "duration_ms": duration,
                           "animations": animations, "triggered": bool(trigger)}})

    return AgentTool("browser_measure_motion",
        "Measure how smoothly a page animates: record every painted frame (requestAnimationFrame), long tasks "
        "and layout shifts for duration_ms, optionally around one indexed click or key press from the given "
        "state_revision, and list the animations running shortly after (target, property, duration, easing). "
        "Reports frame rate, dropped frames, the longest frame and when it happened. Screenshots cannot show "
        "smoothness; use this for motion and use browser_capture_sequence to see the frames.",
        {"type": "object", "additionalProperties": False, "properties": {
            "browser_id": {"type": "string", "minLength": 1},
            "duration_ms": {"type": "integer", "minimum": 200, "maximum": 5000},
            "state_revision": {"type": "integer", "minimum": 1},
            "trigger": {"oneOf": [
                {"type": "object", "additionalProperties": False,
                 "properties": {"type": {"const": "click"}, "index": {"type": "integer", "minimum": 0}},
                 "required": ["type", "index"]},
                {"type": "object", "additionalProperties": False,
                 "properties": {"type": {"const": "press"}, "key": {"type": "string", "minLength": 1, "maxLength": 100}},
                 "required": ["type", "key"]}]}},
         "required": ["browser_id"], "dependentRequired": {"trigger": ["state_revision"]}},
        measure, effect=ToolEffect.SENSITIVE, exposure=ToolExposure.DEFERRED)
