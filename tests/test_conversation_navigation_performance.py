from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "desktop-react/src/state/useLoomCore.ts").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "desktop-react/src/components/Sidebar.tsx").read_text(encoding="utf-8")
APP = (ROOT / "desktop-react/src/App.tsx").read_text(encoding="utf-8")


def test_recent_idle_conversations_stay_hot_long_enough_for_real_navigation() -> None:
    assert "const THREAD_READ_CACHE_LIMIT = 12;" in CORE
    assert "const THREAD_READ_CACHE_BYTES = 64 * 1024 * 1024;" in CORE
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


def test_a_conversation_that_was_read_before_is_built_after_the_click_is_answered() -> None:
    assert 'import { afterNextPaint } from "./afterNextPaint";' in CORE
    cached = CORE[CORE.index("const cached = cachedThreadRead(normalized);"):]
    apply = cached.index("applyThreadRead(cached);")
    assert "await afterNextPaint();" in cached[:apply]
    assert "if (openRequestRef.current !== requestId) return;" in cached[:apply]


def test_a_conversation_opens_with_its_newest_turns_and_the_rest_follow_when_the_page_is_quiet() -> None:
    transcript = (ROOT / "desktop-react/src/components/Transcript.tsx").read_text(encoding="utf-8")
    progressive = (ROOT / "desktop-react/src/components/progressiveTurns.ts").read_text(encoding="utf-8")
    assert "useProgressiveTurns(blockKeys, threadId, scrollRef, Boolean(running))" in transcript
    assert "turnBlocks.slice(firstMountedBlock).map(" in transcript
    assert 'data-older-pending={firstMountedBlock > 0 ? "true" : undefined}' in transcript
    assert "export const INITIAL_TURN_BLOCKS = 2;" in progressive
    assert "export const BACKFILL_DELAY_MS = 1200;" in progressive
    assert "scrollRef: RefObject<HTMLElement | null>, paused = false" in progressive, "older turns are not built while a turn runs"
