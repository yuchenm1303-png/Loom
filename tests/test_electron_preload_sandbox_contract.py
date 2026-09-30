import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ELECTRON = ROOT / "desktop-react" / "electron"

# Each sandboxed window and the preload it loads. Both entries declare
# `sandbox: true` in their webPreferences.
SANDBOXED_PRELOADS = (
    (ELECTRON / "main.ts", ELECTRON / "preload.cts", "preload.cjs"),
    (ELECTRON / "hudWindow.ts", ELECTRON / "hudPreload.cts", "hudPreload.cjs"),
)

# The exact module set a `sandbox: true` preload may require. Electron documents
# this allowlist: the sandboxed `require` resolves Electron's own subset plus a
# handful of Node builtins, optionally `node:`-prefixed.
SANDBOX_ALLOWED_MODULES = {"electron", "events", "timers", "url"}

# Static `import`/`export ... from`, bare `import "x"`, `require("x")`, and
# dynamic `import("x")`.
_SPECIFIER = re.compile(
    r"""^\s*(?:import|export)\b[^'"\n]*?from\s*["']([^"']+)["']"""
    r"""|^\s*import\s*["']([^"']+)["']"""
    r"""|\brequire\s*\(\s*["']([^"']+)["']\s*\)"""
    r"""|\bimport\s*\(\s*["']([^"']+)["']\s*\)""",
    re.MULTILINE,
)


def _specifiers(source: str) -> list[str]:
    return [next(value for value in match.groups() if value) for match in _SPECIFIER.finditer(source)]


def _bare_module(specifier: str) -> str:
    without_prefix = specifier[len("node:"):] if specifier.startswith("node:") else specifier
    return without_prefix.split("/")[0]


def _violations(specifiers: list[str]) -> list[str]:
    return sorted(
        specifier
        for specifier in specifiers
        if specifier.startswith(".") or _bare_module(specifier) not in SANDBOX_ALLOWED_MODULES
    )


def test_preload_import_scan_is_not_vacuous() -> None:
    # Guards the checks below: a scanner that silently matches nothing would let a
    # relative import through unnoticed.
    for _window, preload, _name in SANDBOXED_PRELOADS:
        assert _specifiers(preload.read_text(encoding="utf-8")), (
            f"{preload.name}: no import specifiers found — the scanner is broken"
        )


def test_scanner_detects_the_shipped_pr_225_breakage() -> None:
    # Regression evidence for the actual outage: preload.cts imported
    # "./remoteRelay.cjs", which in turn imported the "ws" package. Both must
    # register as violations, so the live-tree test below can never pass merely
    # because the scanner stopped matching anything.
    assert _violations(_specifiers('import { contextBridge } from "electron";\nimport "./remoteRelay.cjs";\n')) == ["./remoteRelay.cjs"]
    assert _violations(_specifiers('import WebSocket from "ws";\n')) == ["ws"]
    assert _violations(_specifiers('import path from "node:path";\n')) == ["node:path"]
    assert _violations(_specifiers('import { ipcRenderer } from "electron";\nimport { EventEmitter } from "node:events";\n')) == []


def test_sandboxed_preloads_only_import_sandbox_allowed_modules() -> None:
    # A relative import or npm package in a sandboxed preload throws at load time:
    # `preload-error` fires, `showRendererFailure()` replaces the window with a
    # "Loom startup error" page, and startup never reaches the point where the
    # Python app server is launched — so the app comes up with no backend at all.
    # Nothing else catches this: there is no bundler in this project to inline the
    # import away, `tsc` happily compiles it, and no test executes `preload.cjs`.
    for _window, preload, _name in SANDBOXED_PRELOADS:
        violations = _violations(_specifiers(preload.read_text(encoding="utf-8")))
        assert not violations, (
            f"{preload.name} imports {violations}, which a sandboxed preload cannot resolve. "
            "Move that code into the main process and reach it over IPC, or bundle it into the preload."
        )


def test_preloads_are_still_loaded_by_sandboxed_windows() -> None:
    # The contract above only makes sense while these preloads run sandboxed. If a
    # window stops being sandboxed, fail loudly so the tests get revisited rather
    # than silently enforcing a restriction that no longer applies.
    for window_path, _preload, preload_name in SANDBOXED_PRELOADS:
        source = window_path.read_text(encoding="utf-8")
        assert f'preload: path.join(__dirname, "{preload_name}")' in source
        assert "sandbox: true" in source, f"{window_path.name} no longer uses a sandboxed preload"
