"""Bounded browser motion evidence, with honest capture times and a contact sheet."""
from __future__ import annotations

import json
import math
import time
import uuid
from io import BytesIO
from pathlib import Path

from .contracts import ToolEffect
from .tools import AgentTool, ToolExposure, ToolResult


def browser_capture_sequence_tool(runtime):
    def capture(context, arguments):
        from PIL import Image, ImageDraw
        from .browser_tools import _with_fresh_view, _backend_snapshot_action
        context.raise_if_cancelled()
        store = runtime.browser_sessions
        if store is None:
            raise RuntimeError("browser runtime is unavailable")
        browser_id = arguments["browser_id"]
        trigger = arguments.get("trigger")
        partial = {}

        def record():
            if trigger:
                store.ensure_revision(context.session_id, browser_id, arguments["state_revision"])
            directory = Path("browser-screenshots") / ("sequence-" + uuid.uuid4().hex[:16])
            output = context.resolve_workspace_path(directory.as_posix())
            output.mkdir(parents=True)
            count, interval = arguments.get("frames", 8), arguments.get("interval_ms", 100)
            frames, tiles, total = [], [], 0
            partial.update(directory=directory, output=output, frames=frames, trigger_attempted=False)
            started = time.perf_counter()
            trigger_started = trigger_finished = None
            # A lease prevents idle reclamation for the entire capture, including
            # the intervals between screenshots. It does not claim OS isolation.
            with store.operation(context.session_id, browser_id):
                for index in range(count):
                    context.raise_if_cancelled()
                    if index:
                        due = frames[-1]["capture_started_ms"] / 1000 + interval / 1000
                        while (remaining := due - (time.perf_counter() - started)) > 0:
                            context.raise_if_cancelled()
                            time.sleep(min(remaining, 0.05))
                    begin = (time.perf_counter() - started) * 1000
                    raw = store.screenshot(context.session_id, browser_id, full_page=False)
                    context.raise_if_cancelled()
                    end = (time.perf_counter() - started) * 1000
                    total += len(raw)
                    if total > 32 * 1024 * 1024:
                        raise ValueError("capture exceeds 32 MiB; use fewer frames")
                    with Image.open(BytesIO(raw)) as image:
                        if image.width * image.height > 40_000_000:
                            raise ValueError("browser frame exceeds 40 million pixels")
                        source_size = [image.width, image.height]
                        tile = image.convert("RGB")
                        tile.thumbnail((560, 340))
                        tile = tile.copy()
                    name = f"frame-{index:03d}.png"
                    (output / name).write_bytes(raw)
                    frames.append({"path": (directory / name).as_posix(),
                                   "capture_started_ms": round(begin, 2), "capture_finished_ms": round(end, 2),
                                   "source_size": source_size})
                    tiles.append(tile)
                    if index == 0 and trigger:
                        context.raise_if_cancelled()
                        partial["trigger_attempted"] = True
                        trigger_started = (time.perf_counter() - started) * 1000
                        if trigger["type"] == "click":
                            store.click(context.session_id, browser_id, trigger["index"])
                        else:
                            _backend_snapshot_action(store, context.session_id, browser_id, "press_key", trigger["key"])
                        trigger_finished = (time.perf_counter() - started) * 1000
                context.raise_if_cancelled()
                snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
            columns = min(4, count)
            sheet = Image.new("RGB", (columns * 576, math.ceil(count / columns) * 376), "#202225")
            draw = ImageDraw.Draw(sheet)
            for index, (frame, tile) in enumerate(zip(frames, tiles)):
                x, y = (index % columns) * 576 + 8, (index // columns) * 376 + 8
                draw.text((x, y), f"{index}: {frame['capture_started_ms']:.0f}-{frame['capture_finished_ms']:.0f} ms", fill="white")
                sheet.paste(tile, (x, y + 24))
            sheet_path = (directory / "contact-sheet.png").as_posix()
            sheet.save(output / "contact-sheet.png")
            manifest = {"kind": "sampled_screenshots", "requested_interval_ms": interval,
                        "frames": frames, "trigger_started_ms": trigger_started,
                        "trigger_finished_ms": trigger_finished, "contact_sheet": sheet_path,
                        "limitations": "Not every compositor frame. Capture and action latency can leave gaps or miss short transitions. Times bound each screenshot, not exact presentation timestamps."}
            (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            visual = runtime.visual_feedback.stage(context, [{"path": sheet_path, "label": "Sampled browser sequence; read actual time ranges"}])
            return ToolResult(True,
                "Browser sequence captured with actual capture time ranges. Inspect the attached contact sheet; "
                "use view_images on original frame paths or crops for details. Short transitions may fall between samples. "
                "This is observation evidence, not a functional pass.",
                {**snapshot.to_dict(), **visual, "capture": manifest, "manifest_path": (directory / "manifest.json").as_posix()})

        try:
            return _with_fresh_view(context, store, browser_id, record)
        except Exception as exc:
            context.raise_if_cancelled()
            if not partial:
                raise
            manifest_path = (partial["directory"] / "manifest.json").as_posix()
            manifest = {"kind": "sampled_screenshots", "complete": False,
                        "frames": partial["frames"], "trigger_attempted": partial["trigger_attempted"],
                        "error": f"{type(exc).__name__}: {exc}"[:2000]}
            (partial["output"] / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return ToolResult(False,
                "Browser capture did not complete. Partial frame paths are in the manifest. "
                "The trigger may already have executed; inspect current browser_state before another action. "
                "Use view_images to inspect saved partial frames.",
                {"browser_id": browser_id, "manifest_path": manifest_path, "capture": manifest,
                 "execution_status": "failed_after_capture_started"})

    return AgentTool("browser_capture_sequence",
        "Capture 2-16 timestamped viewport screenshots and a contact sheet for visual/motion inspection, attaching "
        "the sheet to the current model. Optionally capture one baseline then trigger one indexed click or key press "
        "from the supplied state_revision. Actual sampling includes screenshot and action latency: this is not "
        "frame-accurate video and may miss short transitions. For finer-grained compositor evidence use a project CDP "
        "screencast script, check timestamps and gaps, and inspect its output with view_images. Requires Pillow. Discover with tool_search.",
        {"type": "object", "additionalProperties": False, "properties": {
            "browser_id": {"type": "string", "minLength": 1},
            "frames": {"type": "integer", "minimum": 2, "maximum": 16},
            "interval_ms": {"type": "integer", "minimum": 40, "maximum": 1000},
            "state_revision": {"type": "integer", "minimum": 1},
            "trigger": {"oneOf": [
                {"type": "object", "additionalProperties": False,
                 "properties": {"type": {"const": "click"}, "index": {"type": "integer", "minimum": 0}}, "required": ["type", "index"]},
                {"type": "object", "additionalProperties": False,
                 "properties": {"type": {"const": "press"}, "key": {"type": "string", "minLength": 1, "maxLength": 100}}, "required": ["type", "key"]}]}},
         "required": ["browser_id"], "dependentRequired": {"trigger": ["state_revision"]}},
        capture, effect=ToolEffect.SENSITIVE, exposure=ToolExposure.DEFERRED)
