from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "desktop-react" / "src" / "components" / "MarkdownMessage.tsx"
ARTIFACTS = ROOT / "desktop-react" / "src" / "components" / "TurnArtifactsPreview.tsx"
ARTIFACT_DOCK = ROOT / "desktop-react" / "src" / "components" / "ArtifactPreviewDock.tsx"
ARTIFACT_SURFACE = ROOT / "desktop-react" / "src" / "components" / "ArtifactRenderSurface.tsx"
ARTIFACT_REGISTRY = ROOT / "desktop-react" / "src" / "artifactRenderers.ts"
APP = ROOT / "desktop-react" / "src" / "App.tsx"
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
MAIN = ROOT / "desktop-react" / "electron" / "main.ts"
PRELOAD = ROOT / "desktop-react" / "electron" / "preload.cts"
TYPES = ROOT / "desktop-react" / "src" / "types" / "global.d.ts"


def test_local_markdown_links_open_the_in_app_artifact_renderer() -> None:
    source = MARKDOWN.read_text(encoding="utf-8")

    assert "function localWorkspacePath" in source
    assert 'node.tagName === "a" && localWorkspacePath(url)' in source
    assert "event.preventDefault();" in source
    assert 'new CustomEvent("loom:artifact-preview-open"' in source
    assert "detail: { path: localTarget, workspace }" in source
    assert "data-loom-local-artifact" in source


def test_desktop_local_artifact_bridge_is_workspace_scoped_and_sandboxed() -> None:
    main = MAIN.read_text(encoding="utf-8")
    preload = PRELOAD.read_text(encoding="utf-8")
    types = TYPES.read_text(encoding="utf-8")

    assert "async function resolveWorkspaceFile" in main
    assert "pathIsInsideWorkspace(root, target)" in main
    assert "async function openLocalArtifact" in main
    assert "async function localArtifactPreviewUrl" in main
    assert "async function serveLocalArtifact" in main
    assert "protocol.registerSchemesAsPrivileged" in main
    assert 'protocol.handle("loom-artifact", serveLocalArtifact)' in main
    assert "LOCAL_BROWSER_ARTIFACT_SUFFIXES" in main
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert 'ipcMain.handle("loom:open-local-artifact"' in main
    assert 'ipcMain.handle("loom:local-artifact-preview-url"' in main
    assert 'ipcRenderer.invoke("loom:open-local-artifact"' in preload
    assert 'ipcRenderer.invoke("loom:local-artifact-preview-url"' in preload
    assert "openLocalArtifact(targetPath: string, workspaceRoot: string): Promise<boolean>" in types
    assert "localArtifactPreviewUrl(targetPath: string, workspaceRoot: string): Promise<string>" in types


def test_main_loom_window_cannot_be_replaced_by_message_navigation() -> None:
    source = MAIN.read_text(encoding="utf-8")

    assert 'window.webContents.on("will-navigate"' in source
    assert "rendererDocumentUrl" in source
    assert "event.preventDefault();" in source
    assert "Local workspace links are handled by the renderer IPC bridge instead." in source


def test_artifact_renderer_registry_covers_primary_agent_artifact_types() -> None:
    source = ARTIFACT_REGISTRY.read_text(encoding="utf-8")

    for kind in ('"web"', '"image"', '"pdf"', '"video"', '"audio"', '"text"', '"code"', '"data"'):
        assert kind in source
    assert "canRenderArtifact" in source
    assert "canInlineRenderArtifact" in source


def test_changed_artifacts_render_inline_and_expand_to_the_side_renderer() -> None:
    artifacts = ARTIFACTS.read_text(encoding="utf-8")
    transcript = TRANSCRIPT.read_text(encoding="utf-8")
    surface = ARTIFACT_SURFACE.read_text(encoding="utf-8")

    assert "canInlineRenderArtifact" in artifacts
    assert "ArtifactRenderSurface" in artifacts
    assert "turn-artifacts-inline-preview" in artifacts
    assert 'new CustomEvent("loom:artifact-preview-open"' in artifacts
    assert "TurnArtifactsPreview items={items} workspace={workspace}" in transcript
    assert "artifactRenderer(path)" in surface
    assert "artifact-render-frame" in surface
    assert "artifact-render-image" in surface
    assert "artifact-render-media" in surface


def test_right_side_artifact_renderer_is_wired_into_the_workspace_shell() -> None:
    app = APP.read_text(encoding="utf-8")
    dock = ARTIFACT_DOCK.read_text(encoding="utf-8")

    assert 'import { ArtifactPreviewDock } from "./components/ArtifactPreviewDock";' in app
    assert "artifactPreviewOpen" in app
    assert 'window.addEventListener("loom:artifact-preview-open"' in app
    assert "with-artifact-preview" in app
    assert "<ArtifactPreviewDock" in app
    assert "<ArtifactRenderSurface" in dock
    assert "openLocalArtifact(path, workspace)" in dock
