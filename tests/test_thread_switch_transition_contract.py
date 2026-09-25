from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop-react" / "src" / "App.tsx"
LOOM_CORE = ROOT / "desktop-react" / "src" / "state" / "useLoomCore.ts"
LOOM_STATE = ROOT / "desktop-react" / "src" / "state" / "useLoom.ts"
APP_SERVER = ROOT / "app" / "app_server.py"
MOTION = ROOT / "desktop-react" / "src" / "components" / "thread-switch-transition.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_thread_switch_selects_immediately_and_keeps_latest_request_authoritative() -> None:
    core = read(LOOM_CORE)
    open_start = core.index("const openThread = useCallback")
    open_end = core.index("const ensureSelection", open_start)
    open_thread = core[open_start:open_end]

    assert 'const [openingThreadId, setOpeningThreadId] = useState("")' in core
    assert "setOpeningThreadId(normalized)" in open_thread
    assert 'call<ThreadReadResult>("thread/read", { threadId: normalized, presentationOnly: true })' in open_thread
    assert open_thread.index("setOpeningThreadId(normalized)") < open_thread.index('"thread/read"')
    assert "if (openRequestRef.current !== requestId) return;" in open_thread
    assert 'openingThreadId,' in core


def test_recent_settled_threads_use_a_small_revalidated_snapshot_cache() -> None:
    core = read(LOOM_CORE)

    assert "const THREAD_READ_CACHE_LIMIT = 3;" in core
    assert "const THREAD_READ_CACHE_TTL_MS = 45_000;" in core
    assert "threadReadCacheRef = useRef<Map<string, ThreadReadCacheEntry>>(new Map())" in core
    assert "if (threadIsRunning(result.thread)) return;" in core
    assert "Date.now() - entry.cachedAt > THREAD_READ_CACHE_TTL_MS" in core
    assert "listed.updatedAt !== entry.result.thread.updatedAt" in core
    assert "threadReadCacheRef.current.delete(active.thread.id);" in core
    assert "threadReadCacheRef.current.delete(thread.id);" in core


def test_workspace_uses_loom_native_delayed_transition_instead_of_blocking_sidebar() -> None:
    app = read(APP)
    css = read(MOTION)

    assert 'activeId={selectedThreadId}' in app
    assert 'const selectedThreadId = loom.openingThreadId || activeThreadId;' in app
    assert 'setThreadSwitchIndicatorVisible(true), 72' in app
    assert '"is-thread-switching"' in app
    assert '"is-thread-entering"' in app
    assert 'className="thread-switch-orbit thread-switch-orbit-a"' in app
    assert 'className="thread-switch-orbit thread-switch-orbit-b"' in app

    assert ".workspace.is-thread-switching > .conversation-stage" in css
    assert ".thread-switch-overlay.is-indicator-visible .thread-switch-mark" in css
    assert "pointer-events: auto;" in css
    assert "backdrop-filter" not in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ':root[data-loom-reduced-motion="true"]' in css


def test_desktop_thread_reads_skip_duplicate_diagnostics_on_hot_paths() -> None:
    core = read(LOOM_CORE)
    state = read(LOOM_STATE)
    server = read(APP_SERVER)

    assert 'presentationOnly: true' in core
    assert 'threadOnly: true' in state
    assert 'if bool(params.get("threadOnly", False)):' in server
    assert 'if not bool(params.get("presentationOnly", False)):' in server
    assert 'result["messages"]' in server
    assert 'result["events"]' in server
