from __future__ import annotations

from pathlib import Path

from app.agent_runtime import ufo_sidecar_supervisor as supervisor


def test_supervisor_starts_the_loom_patched_sidecar_entry(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeProcess:
        pass

    def fake_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        return FakeProcess()

    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)
    process = supervisor.spawn_child(tmp_path)

    assert isinstance(process, FakeProcess)
    argv = captured["argv"]
    assert Path(argv[1]).name == "ufo_sidecar_entry.py"
    assert argv[2:] == ["--ufo-root", str(tmp_path)]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
