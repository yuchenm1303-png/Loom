from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "desktop-react" / "src" / "state" / "useLoomCore.ts"
APP = ROOT / "desktop-react" / "src" / "App.tsx"
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"


def test_conversation_navigation_is_immediate_and_race_safe() -> None:
    source = CORE.read_text(encoding="utf-8")

    assert "const navigationRef = useRef(0);" in source
    assert "const threadListRequestRef = useRef(0);" in source
    assert "const navigationId = ++navigationRef.current;" in source
    assert "navigationRef.current !== navigationId" in source
    assert "includeMessages: false" in source
    assert "includeEvents: false" in source

    # A new empty thread is already authoritative. The UI must not put a full
    # library scan plus another thread/read in its critical path.
    start = source.index("const newThread = useCallback")
    end = source.index("const renameThread = useCallback", start)
    new_thread = source[start:end]
    assert '"thread/start"' in new_thread
    assert 'refreshThreads("active")' not in new_thread
    assert "await openThread(result.thread.id)" not in new_thread
    assert "setActive({" in new_thread
    assert "installItems([]);" in new_thread


def test_conversation_hydration_does_not_flash_the_empty_state() -> None:
    app = APP.read_text(encoding="utf-8")
    transcript = TRANSCRIPT.read_text(encoding="utf-8")

    assert "loom.threadLoading" in app
    assert "loading={loom.threadLoading}" in app
    assert "aria-busy={loading}" in transcript
    assert "loading ? null : !items.length" in transcript
