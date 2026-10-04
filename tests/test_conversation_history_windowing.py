from pathlib import Path
from types import SimpleNamespace

import pytest

from app.app_server import _window_turn_events


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "desktop-react" / "src" / "state" / "useLoomCore.ts"
SCROLL = ROOT / "desktop-react" / "src" / "components" / "TranscriptScrollController.tsx"
APP = ROOT / "desktop-react" / "src" / "App.tsx"
SERVER = ROOT / "app" / "app_server.py"
TYPES = ROOT / "desktop-react" / "src" / "types" / "loom.ts"
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"


def test_turn_event_window_is_exclusive_and_reports_more_history() -> None:
    events = tuple(
        SimpleNamespace(turn_id=f"turn-{turn}", marker=f"{turn}:{event}")
        for turn in range(1, 6)
        for event in range(2)
    )

    latest, has_more = _window_turn_events(events, limit=2)
    assert [event.marker for event in latest] == ["4:0", "4:1", "5:0", "5:1"]
    assert has_more is True

    older, has_more = _window_turn_events(events, limit=2, before_turn_id="turn-4")
    assert [event.marker for event in older] == ["2:0", "2:1", "3:0", "3:1"]
    assert has_more is True

    oldest, has_more = _window_turn_events(events, limit=2, before_turn_id="turn-2")
    assert [event.marker for event in oldest] == ["1:0", "1:1"]
    assert has_more is False

    with pytest.raises(ValueError, match="beforeTurnId"):
        _window_turn_events(events, limit=2, before_turn_id="missing")


def test_thread_read_protocol_exposes_bounded_turn_windows() -> None:
    server = SERVER.read_text(encoding="utf-8")
    types = TYPES.read_text(encoding="utf-8")

    assert 'params.get("turnLimit")' in server
    assert 'params.get("beforeTurnId")' in server
    assert 'result["hasMoreTurns"] = has_more_turns' in server
    assert 'result["oldestTurnId"]' in server
    assert "hasMoreTurns?: boolean" in types
    assert "oldestTurnId?: string | null" in types


def test_renderer_loads_latest_window_then_pages_older_turns() -> None:
    core = CORE.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    scroll = SCROLL.read_text(encoding="utf-8")

    assert "const THREAD_READ_WINDOW_TURNS = 20;" in core
    assert "turnLimit: THREAD_READ_WINDOW_TURNS" in core
    assert "...(beforeTurnId ? { beforeTurnId } : {})" in core
    assert "normalizeThreadReadWindow(result, beforeTurnId)" in core
    assert "const loadOlderTurns = useCallback" in core
    assert "const next = [...prependedItems, ...current];" in core
    assert "hasOlderTurns: Boolean(active?.hasMoreTurns)" in core

    assert "hasOlder={loom.hasOlderTurns}" in app
    assert "loadingOlder={loom.loadingOlderTurns}" in app
    assert "onLoadOlder={loom.loadOlderTurns}" in app

    assert "const HISTORY_LOAD_THRESHOLD_PX = 220;" in scroll
    assert "requestOlderHistory(scroller)" in scroll
    assert "scroller.scrollTop = anchor.scrollTop + addedHeight;" in scroll


def test_gateway_bounds_old_host_thread_reads_before_browser_delivery() -> None:
    gateway = GATEWAY.read_text(encoding="utf-8")

    assert "pending_invokes: dict[int, tuple[str, list[Any]]]" in gateway
    assert "def _window_legacy_thread_read_result(" in gateway
    assert 'args[0] != "thread/read"' in gateway
    assert 'bounded["hasMoreTurns"] = start > 0' in gateway
    assert 'bounded.pop("messages", None)' in gateway
    assert 'bounded.pop("events", None)' in gateway
    assert "_window_legacy_thread_read_result(result, invoke_meta[0], invoke_meta[1])" in gateway


def test_folded_historical_turns_do_not_mount_heavy_process_subtrees() -> None:
    transcript = TRANSCRIPT.read_text(encoding="utf-8")

    assert "const [processVisited, setProcessVisited] = useState(active || open);" in transcript
    assert "const renderProcessContent = active || open || processVisited;" in transcript
    assert "{renderProcessContent ? (" in transcript
    assert "<Sequence items={active ? progress.current : items}" in transcript
