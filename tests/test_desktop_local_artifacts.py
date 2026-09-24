from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "desktop-react" / "src" / "components" / "MarkdownMessage.tsx"
ARTIFACTS = ROOT / "desktop-react" / "src" / "components" / "TurnArtifactsPreview.tsx"
TRANSCRIPT = ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx"
MAIN = ROOT / "desktop-react" / "electron" / "main.ts"
PRELOAD = ROOT / "desktop-react" / "electron" / "preload.cts"
TYPES = ROOT / "desktop-react" / "src" / "types" / "global.d.ts"


def test_local_markdown_links_use_workspace_artifact_bridge() -> None:
    source = MARKDOWN.read_text(encoding="utf-8")

    assert "function localWorkspacePath" in source
    assert 'node.tagName === "a" && localWorkspacePath(url)' in source
    assert "event.preventDefault();" in source
    assert "window.loom.openLocalArtifact(localTarget, workspace)" in source
    assert "data-loom-local-artifact" in source


def test_desktop_local_artifact_bridge_is_workspace_scoped_and_sandboxed() -> None:
    main = MAIN.read_text(encoding="utf-8")
    preload = PRELOAD.read_text(encoding="utf-8")
    types = TYPES.read_text(encoding="utf-8")

    assert "async function resolveWorkspaceFile" in main
    assert "pathIsInsideWorkspace(root, target)" in main
    assert "async function openLocalArtifact" in main
    assert "LOCAL_BROWSER_ARTIFACT_SUFFIXES" in main
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert 'ipcMain.handle("loom:open-local-artifact"' in main
    assert 'ipcRenderer.invoke("loom:open-local-artifact"' in preload
    assert "openLocalArtifact(targetPath: string, workspaceRoot: string): Promise<boolean>" in types


def test_main_loom_window_cannot_be_replaced_by_message_navigation() -> None:
    source = MAIN.read_text(encoding="utf-8")

    assert 'window.webContents.on("will-navigate"' in source
    assert "rendererDocumentUrl" in source
    assert "event.preventDefault();" in source
    assert "Local workspace links are handled by the renderer IPC bridge instead." in source


def test_changed_web_artifacts_offer_rendered_preview() -> None:
    artifacts = ARTIFACTS.read_text(encoding="utf-8")
    transcript = TRANSCRIPT.read_text(encoding="utf-8")

    assert "function previewableArtifact" in artifacts
    assert "turn-artifacts-preview-action" in artifacts
    assert "window.loom.openLocalArtifact(previewFile.path, workspace)" in artifacts
    assert "TurnArtifactsPreview items={items} workspace={workspace}" in transcript
