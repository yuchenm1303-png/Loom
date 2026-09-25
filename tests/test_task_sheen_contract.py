from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"


def test_running_task_uses_one_sheen_layer() -> None:
    source = MOTION.read_text(encoding="utf-8")

    assert ".task-flow-row.is-executing::before" in source
    assert "loom-task-running-sheen" in source
    assert ".turn-process.is-live .task-flow-row::after" not in source
    assert "loom-task-capsule-bloom" not in source


def test_running_sheen_is_single_soft_pass_with_idle_time() -> None:
    source = MOTION.read_text(encoding="utf-8")

    block = source[source.index(".task-flow-row.is-executing::before"):source.index(".task-flow-row.is-resting")]
    assert "width: 26%;" in block
    assert "4.8s" in block
    assert "loom-task-running-sheen" in block
    assert "filter:" not in block
    assert "will-change: transform, opacity;" in block

    keyframes = source[source.index("@keyframes loom-task-running-sheen"):source.index("@keyframes loom-live-anchor-aura")]
    assert "translate3d(500%,0,0)" in keyframes
    assert "66%, 100%" in keyframes


def test_live_task_anchor_uses_one_small_composited_aura() -> None:
    source = MOTION.read_text(encoding="utf-8")

    aura = source[source.index(".task-flow-group.is-running .task-flow-group-icon::before"):source.index("/* Task-flow copy")]
    assert "loom-live-anchor-aura" in aura
    assert "will-change: opacity, scale;" in aura
    assert "filter:" not in aura
    assert "@keyframes loom-live-anchor-aura" in source
