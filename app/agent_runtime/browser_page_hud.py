"""The page-local HUD for browsers Loom drives without its extension.

The extension renders this HUD from a content script and decides visibility from
the tab list its worker publishes. A browser Loom launched itself, or attached
to over CDP, contains no extension: the same automation, the same need to show
whose hands are on the page, and nothing there to draw it. A model that opened
its own browser therefore drove it invisibly.

So the HUD asset stops being an extension-only thing. This module injects the
extension's own file over the protocol and calls it directly, which keeps one
implementation of the overlay rather than a second one that drifts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_RUNTIME_KEY = "__loomBrowserHudRuntimeV2"
_ASSET = Path(__file__).resolve().parents[2] / "extensions" / "browser-current-tab" / "browser-hud.js"

# Takes the element as its second argument when there is one: the HUD wants a
# viewport point, and the element that an action is about knows where it is far
# better than anything Python could work out from a selector index.
_PRESENT = r"""function(payload, target) {
  const runtime = globalThis.__loomBrowserHudRuntimeV2;
  if (!runtime || typeof runtime.present !== 'function') return false;
  const data = Object.assign({}, payload);
  if (target && typeof target.getBoundingClientRect === 'function') {
    const rect = target.getBoundingClientRect();
    if (rect.width > 0 || rect.height > 0) {
      data.x = rect.left + rect.width / 2;
      data.y = rect.top + rect.height / 2;
    }
  }
  return runtime.present(data) !== false;
}"""

_HIDE = f"""(() => {{
  const runtime = globalThis.{_RUNTIME_KEY};
  if (runtime && typeof runtime.hide === 'function') runtime.hide();
  return true;
}})()"""

_script_cache: str | None = None


def hud_script() -> str:
    """The HUD source, or "" when this build ships without the asset.

    A missing file is not an error worth failing an action over: the automation
    still works, it is only unannounced.
    """

    global _script_cache
    if _script_cache is None:
        override = str(os.environ.get("LOOM_BROWSER_HUD_ASSET") or "").strip()
        path = Path(override).expanduser() if override else _ASSET
        try:
            _script_cache = path.read_text(encoding="utf-8")
        except OSError:
            _script_cache = ""
    return _script_cache


def _result_value(response: Any) -> Any:
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if isinstance(result, dict):
        return result.get("value")
    return None


class PageHud:
    """Drives the injected HUD for one browser backend."""

    def __init__(self) -> None:
        self._primed: set[str] = set()

    def forget(self) -> None:
        self._primed.clear()

    async def present(
        self,
        cdp: Any,
        *,
        title: str,
        subtitle: str = "",
        point: tuple[float, float] | None = None,
        object_id: str = "",
        click: bool = False,
        phase: int | None = None,
    ) -> bool:
        script = hud_script()
        if not script:
            return False
        payload: dict[str, Any] = {
            "title": str(title or "")[:120],
            "subtitle": str(subtitle or "")[:220],
            "click": bool(click),
        }
        if point is not None:
            payload["x"] = float(point[0])
            payload["y"] = float(point[1])
        if phase is not None:
            payload["phase"] = int(phase)

        await self._prime(cdp, script)
        if await self._present_once(cdp, payload, object_id):
            return True
        # Either this document was never primed, or it is the page that was
        # already open when the session began. Install into the live document
        # and try the one retry that distinguishes "not injected yet" from
        # "cannot run scripts here at all".
        await self._evaluate(cdp, script)
        return await self._present_once(cdp, payload, object_id)

    async def hide(self, cdp: Any) -> None:
        if not hud_script():
            return
        await self._evaluate(cdp, _HIDE)

    async def _prime(self, cdp: Any, script: str) -> None:
        key = str(getattr(cdp, "session_id", "") or "")
        if key in self._primed:
            return
        self._primed.add(key)
        # A navigation wipes the HUD out of the document along with everything
        # else, and the next action would otherwise be the first to notice.
        await cdp.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
            params={"source": script},
            session_id=cdp.session_id,
        )

    async def _present_once(self, cdp: Any, payload: dict[str, Any], object_id: str) -> bool:
        if object_id:
            response = await cdp.cdp_client.send.Runtime.callFunctionOn(
                params={
                    "objectId": object_id,
                    "functionDeclaration": _PRESENT,
                    "arguments": [{"value": payload}, {"objectId": object_id}],
                    "returnByValue": True,
                },
                session_id=cdp.session_id,
            )
        else:
            expression = f"({_PRESENT})({json.dumps(payload, ensure_ascii=False)}, null)"
            response = await self._evaluate(cdp, expression)
        return bool(_result_value(response))

    async def _evaluate(self, cdp: Any, expression: str) -> Any:
        return await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": expression, "returnByValue": True},
            session_id=cdp.session_id,
        )


_TITLES = {
    "start": "Opening browser",
    "state": "Reading page",
    "navigate": "Navigating",
    "refresh": "Refreshing",
    "go_back": "Back",
    "go_forward": "Forward",
    "click": "Click",
    "click_at": "Click",
    "type": "Type",
    "type_text": "Type",
    "send_text": "Send text",
    "press_key": "Press",
    "hover": "Hover",
    "select_option": "Select",
    "dropdown_options": "Reading dropdown",
    "drag": "Drag",
    "scroll": "Scroll",
    "screenshot": "Screenshot",
    "switch_tab": "Switch tab",
    "new_tab": "New tab",
    "close_tab": "Close tab",
    "find_text": "Find",
    "upload": "Upload",
    "evaluate": "Evaluate",
    "wait": "Wait",
    "tabs": "Reading tabs",
}

_CLICKING_ACTIONS = {"click", "click_at", "drag", "select_option", "upload"}


def hud_presentation(
    action: str,
    args: dict[str, Any],
) -> tuple[str, str, tuple[float, float] | None, bool]:
    """Title, subtitle, viewport point, and whether to pulse the cursor.

    Typed text never reaches the HUD. The overlay is drawn in the page the user
    is watching, which is exactly where a password being typed into a login form
    must not be reprinted in 13px bold.
    """

    name = str(action or "").strip()
    data = dict(args or {})
    title = _TITLES.get(name) or name.replace("_", " ").strip().capitalize() or "Browser Use"
    point: tuple[float, float] | None = None

    index = data.get("index")
    if isinstance(index, int):
        title = f"{title} #{index}"
    x, y = data.get("x"), data.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
        point = (float(x), float(y))
        title = f"{title} {int(x)}, {int(y)}"
    key = str(data.get("key") or "").strip()
    if key:
        title = f"{title} {key[:24]}"

    details: list[str] = []
    url = str(data.get("url") or "").strip()
    if url:
        details.append(url[:160])
    length = data.get("text_length")
    if not isinstance(length, int) and "text" in data:
        length = len(str(data.get("text") or ""))
    if isinstance(length, int) and length:
        details.append(f"{length} characters")
    direction = str(data.get("direction") or "").strip()
    if direction:
        amount = data.get("amount")
        details.append(f"{direction} {int(amount)}px" if isinstance(amount, int) else direction)
    value = str(data.get("value") or data.get("query") or "").strip()
    if value:
        details.append(value[:120])
    seconds = data.get("seconds")
    if isinstance(seconds, (int, float)):
        details.append(f"{seconds:g}s")
    tab_id = str(data.get("tab_id") or "").strip()
    if tab_id:
        details.append(f"tab {tab_id[:24]}")

    subtitle = " · ".join(details)[:220] or "Browser Use"
    return title, subtitle, point, name in _CLICKING_ACTIONS


__all__ = ["PageHud", "hud_presentation", "hud_script"]
