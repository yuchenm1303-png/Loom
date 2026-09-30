from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOTION = ROOT / "desktop-react" / "src" / "components" / "conversation-motion.css"


def test_running_task_uses_one_sheen_layer() -> None:
    source = MOTION.read_text(encoding="utf-8")

    assert ".task-flow-sheen > i" in source
    assert "loom-task-running-sheen" in source
    assert ".turn-process.is-live .task-flow-row::after" not in source
    assert "loom-task-capsule-bloom" not in source


def test_running_sheen_is_single_soft_pass_with_idle_time() -> None:
    source = MOTION.read_text(encoding="utf-8")

    block = source[source.index(".task-flow-sheen > i"):source.index(".task-flow-row.is-resting")]
    assert "width: 30%;" in block
    assert "4.8s" in block
    assert "loom-task-running-sheen" in block
    assert "filter:" not in block
    assert "infinite both paused" in block
    assert "animation-play-state: running" in block
    assert "will-change:" not in block

    keyframes = source[source.index("@keyframes loom-task-running-sheen"):source.index("@keyframes loom-live-anchor-aura")]
    assert "translate3d(450%,0,0)" in keyframes
    assert "48%, 100%" in keyframes
    assert "24% { opacity: .9; }" in keyframes


def test_running_sheen_has_separate_restrained_dark_and_light_palettes() -> None:
    source = MOTION.read_text(encoding="utf-8")

    assert "--loom-live-accent: 153, 145, 226;" in source
    assert "--loom-live-accent-strong: 181, 174, 244;" in source
    assert "--loom-live-accent: 96, 84, 194;" in source
    assert "--loom-live-accent-strong: 112, 100, 210;" in source

    light_start = source.index('html[data-loom-theme="light"] .task-flow-sheen > i')
    light_end = source.index('html[data-loom-theme="light"] .task-flow-live-detail', light_start)
    light_sheen = source[light_start:light_end]
    assert "rgba(255,255,255,.65) 53%" in light_sheen


def test_live_task_anchor_uses_one_small_composited_aura() -> None:
    source = MOTION.read_text(encoding="utf-8")

    aura = source[source.index(".task-flow-group.is-running .task-flow-group-icon::before"):source.index("/* Task-flow copy")]
    assert "loom-live-anchor-aura" in aura
    assert "will-change: opacity, scale;" in aura
    assert "filter:" not in aura
    assert "@keyframes loom-live-anchor-aura" in source


def test_sheen_fades_after_execution_and_obeys_reduced_motion() -> None:
    source = MOTION.read_text(encoding="utf-8")
    transcript = (MOTION.parent / "Transcript.tsx").read_text(encoding="utf-8")
    assert transcript.count('className="task-flow-sheen" aria-hidden="true"') == 1
    assert ".task-flow-row.is-executing::before" not in source
    wrapper = source[source.index(".task-flow-row > .task-flow-sheen {"):source.index(".task-flow-sheen > i {")]
    assert "opacity: 0;" in wrapper and "opacity: 1;" in wrapper
    assert "transition: opacity 240ms ease;" in wrapper
    assert "pointer-events: none;" in wrapper
    reduced = source[source.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".task-flow-row > .task-flow-sheen { display: none;" in reduced
    assert ':root[data-loom-reduced-motion="true"] .task-flow-row > .task-flow-sheen' in reduced


def test_running_band_catches_both_rims_without_a_second_layer() -> None:
    source = MOTION.read_text(encoding="utf-8")

    block = source[source.index(".task-flow-sheen > i"):source.index(".task-flow-row.is-resting")]
    # Two 1px glints (upper and lower rim) live in the same moving element's
    # background, so the sheen is still one layer and one animation.
    assert block.count("no-repeat") == 2
    assert "0 1px / 100% 1px no-repeat" in block
    assert "0 calc(100% - 1px) / 100% 1px no-repeat" in block
    assert block.count("animation:") == 1

    light_start = source.index('html[data-loom-theme="light"] .task-flow-sheen > i')
    light_end = source.index('html[data-loom-theme="light"] .task-flow-live-detail', light_start)
    light = source[light_start:light_end]
    # White cannot read against the light pill's own highlight: both glints tint.
    assert light.count("no-repeat") == 2
    assert "rgba(255,255,255,.55)" not in light
