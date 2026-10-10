"""Desktop contracts for Browser Use screenshot feedback."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
WEAVE_FLOW = ROOT / "desktop-react" / "src" / "components" / "WeaveFlow.tsx"
WEAVE_CSS = ROOT / "desktop-react" / "src" / "components" / "weave.css"
ACTIVITY_MODEL = ROOT / "desktop-react" / "src" / "components" / "activityModel.ts"
BROWSER_RUNTIME = ROOT / "app" / "agent_runtime" / "browser_runtime_v1.py"


def test_browser_screenshot_activity_is_user_visible_and_previewable() -> None:
    model = ACTIVITY_MODEL.read_text(encoding="utf-8")
    flow = WEAVE_FLOW.read_text(encoding="utf-8")

    assert 'String(item.toolName ?? "").trim().toLowerCase() === "browser_screenshot"' in model
    copy = (TRANSCRIPT.parent / "runtimeCopy.ts").read_text(encoding="utf-8")
    assert 'items.push(`查看 ${parts.imagesViewed} 张图片`);' in copy
    assert 'imagesViewed: (count) => `${count} 张图片`,' in copy
    assert "screenshotCount: screenshots," in model
    assert '<BrowserScreenshotDetail paths={screenshotPaths} workspace={workspace} />' in flow
    assert 'window.loom.readLocalImage(path, workspaceRoot)' in flow
    assert 'workspace={workspace}' in flow


def test_browser_screenshot_preview_has_bounded_thumbnail_surface() -> None:
    source = WEAVE_CSS.read_text(encoding="utf-8")

    assert ".wv-shots {" in source
    assert "grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));" in source
    assert ".wv-shot img {" in source
    assert "object-fit: contain;" in source


def test_turn_start_tolerates_minimal_runtime_instances() -> None:
    source = BROWSER_RUNTIME.read_text(encoding="utf-8")

    assert 'getattr(self, "_browser_baselines", {}).pop(session_id, None)' in source
