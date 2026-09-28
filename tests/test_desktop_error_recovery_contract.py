from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "desktop-react" / "src" / "state" / "useLoomCore.ts"
WRAPPER = ROOT / "desktop-react" / "src" / "state" / "useLoom.ts"


def test_terminal_error_item_ends_live_turn_even_without_turn_completed() -> None:
    source = CORE.read_text(encoding="utf-8")

    assert "function itemIsTerminalTurnError" in source
    assert "if (itemIsTerminalTurnError(completed)) {" in source
    terminal_start = source.index("if (itemIsTerminalTurnError(completed)) {")
    terminal_end = source.index('} else if (message.method === "thread/resync")', terminal_start)
    terminal = source[terminal_start:terminal_end]

    assert "setTurnActive(false);" in terminal
    assert "setTurnStartedAt(null);" in terminal
    assert 'status: "failed"' in terminal
    assert "pendingApproval: null" in terminal
    assert "terminalErrorTurnRef.current" in terminal


def test_stale_running_thread_update_cannot_resurrect_failed_turn() -> None:
    source = CORE.read_text(encoding="utf-8")

    update_start = source.index('if (message.method === "thread/updated") {')
    update_end = source.index('if (message.method === "thread/deleted")', update_start)
    update = source[update_start:update_end]

    assert "staleRunningAfterTerminalError" in update
    assert "terminalErrorTurnRef.current" in update
    assert 'status: "failed"' in update
    assert 'terminalErrorTurnRef.current = "";' in source[source.index('if (message.method === "turn/started")'):]


def test_thread_read_recovers_from_stale_running_snapshot_with_error_item() -> None:
    source = CORE.read_text(encoding="utf-8")

    start = source.index("const applyThreadRead = useCallback")
    end = source.index("const openThread = useCallback", start)
    block = source[start:end]

    assert "const terminalTurnId = terminalErrorTurnId(nextItems, result.thread.currentTurnId);" in block
    assert "terminalErrorTurnRef.current = terminalTurnId;" in block
    assert "terminalTurnId && threadIsRunning(result.thread)" in block
    assert 'status: "failed"' in block


def test_retry_after_terminal_error_starts_new_turn_instead_of_steering_dead_turn() -> None:
    source = WRAPPER.read_text(encoding="utf-8")

    assert "function currentTurnHasTerminalError" in source
    assert "activeTurnHasTerminalError" in source
    send_start = source.index("const send = useCallback")
    send_end = source.index("return useMemo(", send_start)
    send = source[send_start:send_end]

    assert "&& !activeTurnHasTerminalError;" in send
    assert "if (!running) {" in send
    assert "await loom.send(input, attachments);" in send
    assert 'window.loom.call<SteeringReceipt>("turn/steer"' in send
