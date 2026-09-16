from __future__ import annotations

import re


_ISOLATED_BROWSER = "isolated"
_ISOLATED_INTENT_RE = re.compile(
    r"(?:"
    r"干净(?:的)?浏览器|隔离(?:的)?浏览器|独立(?:的)?浏览器|新(?:开|的)?浏览器|"
    r"无痕(?:浏览|模式)?|隐私(?:浏览|模式)?|未登录(?:状态)?|登出(?:状态)?|退出登录(?:状态)?|"
    r"不要用我的(?:账号|登录|cookie|cookies)|不用我的(?:账号|登录|cookie|cookies)|"
    r"clean\s+browser|isolated\s+browser|separate\s+browser|fresh\s+browser|new\s+browser|"
    r"incognito|private\s+brows(?:er|ing)|signed[- ]?out|logged[- ]?out|"
    r"without\s+(?:my\s+)?(?:login|account|cookies?)|not\s+logged\s+in"
    r")",
    flags=re.IGNORECASE,
)


def user_explicitly_requests_isolated_browser(text: object) -> bool:
    """Conservative turn-local intent check for changing browser identity.

    Current-browser is allowed to fail, but failure is never authority to launch a
    different browser.  An isolated browser is selectable from a current/external
    backend only when the user explicitly asks for a clean/signed-out/separate
    browser in this turn. Users can always choose Isolated Browser in Settings if
    they want it to be the default instead.
    """

    return bool(_ISOLATED_INTENT_RE.search(str(text or "")))


class BrowserBackendIntentMixin:
    _browser_turn_allows_isolated_selection: bool = False

    def start_turn(self, session_id, user_text, *, turn_id: str | None = None):
        self._browser_turn_allows_isolated_selection = user_explicitly_requests_isolated_browser(user_text)
        return super().start_turn(session_id, user_text, turn_id=turn_id)

    def _backend_model_selectable(self, backend: str) -> bool:
        if str(backend) == _ISOLATED_BROWSER:
            selected = str(getattr(self, "_browser_requested_backend", ""))
            return selected == _ISOLATED_BROWSER or bool(
                getattr(self, "_browser_turn_allows_isolated_selection", False)
            )
        return bool(super()._backend_model_selectable(backend))

    def browser_session_connection(self, connect: str, *, cdp_url: str = ""):
        normalized = str(connect or "").strip().casefold().replace("-", "_")
        selected = str(getattr(self, "_browser_requested_backend", ""))
        wants_isolated = normalized in {"isolated", "launch"}
        if wants_isolated and selected != _ISOLATED_BROWSER:
            if not bool(getattr(self, "_browser_turn_allows_isolated_selection", False)):
                raise PermissionError(
                    "opening Loom's isolated browser would change browser identity. The current user request did not "
                    "explicitly ask for a clean, isolated, signed-out, private, or separate browser. Keep the selected "
                    "browser backend and report its connection error instead of falling back."
                )
        return super().browser_session_connection(connect, cdp_url=cdp_url)


__all__ = ["BrowserBackendIntentMixin", "user_explicitly_requests_isolated_browser"]
