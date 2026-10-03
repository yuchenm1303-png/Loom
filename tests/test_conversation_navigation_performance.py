from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "desktop-react/src/state/useLoomCore.ts").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "desktop-react/src/components/Sidebar.tsx").read_text(encoding="utf-8")
APP = (ROOT / "desktop-react/src/App.tsx").read_text(encoding="utf-8")


def test_recent_idle_conversations_stay_hot_long_enough_for_real_navigation() -> None:
    assert "const THREAD_READ_CACHE_LIMIT = 8;" in CORE
    assert "const THREAD_READ_CACHE_TTL_MS = 5 * 60_000;" in CORE
    assert "if (cached) {" in CORE
    assert "applyThreadRead(cached);" in CORE
    assert "return;" in CORE


def test_hover_prefetch_and_click_share_one_inflight_thread_read() -> None:
    assert "threadReadInflightRef" in CORE
    assert "const existing = threadReadInflightRef.current.get(requestKey);" in CORE
    assert "if (existing) return existing;" in CORE
    assert "const prefetchThread = useCallback" in CORE
    assert "const result = await readThread(normalized);" in CORE
    assert "onPointerEnter={() => schedulePrefetch(thread.id)}" in SIDEBAR
    assert "}, 90);" in SIDEBAR
    assert "onFocus={() => onPrefetch?.(thread.id)}" in SIDEBAR
    assert "onPrefetch={loom.prefetchThread}" in APP
