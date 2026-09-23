from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"


def test_running_task_uses_one_sheen_layer() -> None:
    source = MOTION.read_text(encoding="utf-8")

    assert ".task-flow-row.is-executing::before" in source
    assert "loom-task-running-sheen" in source
    assert ".turn-process.is-live .task-flow-row::after" not in source
    assert "loom-task-capsule-bloom" not in source


def test_running_sheen_is_narrow_and_low_frequency() -> None:
    source = MOTION.read_text(encoding="utf-8")

    block = source[source.index(".task-flow-row.is-executing::before"):source.index(".task-flow-row.is-resting")]
    assert "width: 16%;" in block
    assert "3.7s" in block
    assert "filter:" not in block
