from __future__ import annotations

import json
from pathlib import Path

from app.agent_runtime.computer_diagnostics import ComputerDiagnostics


def test_detailed_diagnostics_write_correlated_jsonl(tmp_path):
    diagnostics = ComputerDiagnostics({
        "LOOM_COMPUTER_DIAGNOSTICS": "detailed",
        "LOOM_COMPUTER_LOG_DIR": str(tmp_path),
    })
    operation_id = diagnostics.operation_id()
    with diagnostics.bind(operation_id):
        diagnostics.emit("provider.response", response={"length": 42})

    records = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["operation_id"] == operation_id
    assert records[-1]["response"] == {"length": 42}
    assert records[-1]["sequence"] > records[0]["sequence"]


def test_raw_diagnostics_persist_original_screenshot(tmp_path):
    diagnostics = ComputerDiagnostics({
        "LOOM_COMPUTER_DIAGNOSTICS": "raw",
        "LOOM_COMPUTER_LOG_DIR": str(tmp_path),
    })

    class Observation:
        observation_id = "obs-1"
        image_png = b"original-png-bytes"

    saved = Path(diagnostics.save_screenshot(Observation(), operation_id="op-1", phase="before"))
    assert saved.read_bytes() == b"original-png-bytes"
    assert saved.parent == tmp_path / "computer-snapshots" / "images"
