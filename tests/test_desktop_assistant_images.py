from __future__ import annotations

from pathlib import Path

from app.agent_runtime.runtime import DEFAULT_AGENT_SYSTEM_PROMPT


ROOT = Path(__file__).resolve().parents[1]


def test_agent_is_told_to_embed_generated_workspace_images() -> None:
    prompt = DEFAULT_AGENT_SYSTEM_PROMPT

    assert "Markdown image syntax" in prompt
    assert "workspace-relative path" in prompt
    assert "Do not embed local images as base64 or file:// URLs" in prompt
    assert "do not leave the user with only a path" in prompt


def test_desktop_renders_assistant_local_images_through_workspace_scoped_ipc() -> None:
    markdown = (ROOT / "desktop-react" / "src" / "components" / "MarkdownMessage.tsx").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "desktop-react" / "electron" / "main.ts").read_text(encoding="utf-8")
    preload = (ROOT / "desktop-react" / "electron" / "preload.cts").read_text(encoding="utf-8")
    transcript = (ROOT / "desktop-react" / "src" / "components" / "Transcript.tsx").read_text(
        encoding="utf-8"
    )

    assert "readLocalImage(target, workspace)" in markdown
    assert "singleLineImagePath" in markdown
    assert "LocalImagePreview" in markdown
    assert 'urlTransform={markdownUrlTransform}' in markdown
    assert 'ipcRenderer.invoke("loom:read-local-image"' in preload
    assert 'ipcMain.handle("loom:read-local-image"' in main
    assert "resolveWorkspaceLocalPath" in main
    assert 'relative.startsWith(`..${path.sep}`)' in main
    assert 'realRelative.startsWith(`..${path.sep}`)' in main
    assert "MAX_INLINE_IMAGE_BYTES" in main
    assert "workspace={workspace}" in transcript
