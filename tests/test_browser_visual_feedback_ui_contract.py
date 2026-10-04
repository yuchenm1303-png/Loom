"""Desktop contracts for Browser Use screenshot feedback."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
ACTIVITY_FLOW = ROOT / "desktop-react" / "src" / "components" / "activity-flow.css"


def test_browser_screenshot_activity_is_user_visible_and_previewable() -> None:
    source = TRANSCRIPT.read_text(encoding="utf-8")

    assert 'String(item.toolName ?? "").trim().toLowerCase() === "browser_screenshot"' in source
    assert 'parts.push(`查看 ${breakdown.imagesViewed} 张图片`);' in source
    assert '<span className="task-flow-primary">{screenshotPaths.length} 张图片</span>' in source
    assert '<BrowserScreenshotDetail paths={screenshotPaths} workspace={workspace} />' in source
    assert 'window.loom.readLocalImage(path, workspaceRoot)' in source
    assert 'workspace={workspace}' in source


def test_browser_screenshot_preview_has_bounded_thumbnail_surface() -> None:
    source = ACTIVITY_FLOW.read_text(encoding="utf-8")

    assert ".task-flow-image-grid {" in source
    assert "grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));" in source
    assert ".task-flow-image-card img {" in source
    assert "object-fit: contain;" in source
