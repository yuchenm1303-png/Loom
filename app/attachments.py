"""Turning files a user attached into something a turn can carry.

Two different things are called "an attachment" and they need opposite
treatment:

- an **image** has to reach the model as pixels, because that is the only way
  the model can answer a question about it;
- **any other file** must not. Inlining a PDF or a 40 MB CSV is a way to fill a
  context window with bytes nobody reads. The agent already owns file tools and
  a workspace, so the useful thing to hand it is a *path*.

So every attachment is staged into ``<workspace>/.loom/attachments/<turn>/``
first. Images additionally become an ``ImagePart`` carrying a data URL. The
staged copy is what makes an attachment durable: the source may be a clipboard
temp file that is gone an hour later, while a conversation is expected to still
make sense when it is reopened next week.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.ai import ImagePart, TextPart
from app.ai.contracts import ContentPart


ATTACHMENT_DIRNAME = ".loom/attachments"

# Roughly a 4K screenshot as PNG. Past this an image costs more to transport
# than it is worth, and most providers reject it anyway.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_ATTACHMENTS = 10

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._一-鿿-]+")


class AttachmentError(ValueError):
    """An attachment cannot be accepted, with a reason worth showing a user."""


def is_image(name: str | Path) -> bool:
    return Path(name).suffix.casefold() in IMAGE_SUFFIXES


def safe_name(name: str) -> str:
    """A filename that survives every filesystem without losing its identity.

    CJK is preserved deliberately: a user who attached ``季度报告.pdf`` should
    still recognise it in the agent's reply.
    """
    cleaned = _UNSAFE_NAME.sub("_", Path(str(name or "")).name).strip("._") or "attachment"
    stem, dot, suffix = cleaned.rpartition(".")
    if dot and len(stem) > 96:
        cleaned = f"{stem[:96]}.{suffix}"
    elif not dot and len(cleaned) > 100:
        cleaned = cleaned[:100]
    return cleaned


@dataclass(frozen=True, slots=True)
class StagedAttachment:
    """One attachment after it has landed inside the workspace."""

    name: str
    path: Path
    relative_path: str
    size: int
    kind: str  # "image" | "file"

    @property
    def is_image(self) -> bool:
        return self.kind == "image"

    def data_url(self) -> str:
        suffix = self.path.suffix.casefold()
        mime = _IMAGE_MIME.get(suffix) or mimetypes.guess_type(self.path.name)[0] or "image/png"
        try:
            payload = base64.b64encode(self.path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise AttachmentError(f"could not read the staged image {self.name!r}: {exc}") from exc
        return f"data:{mime};base64,{payload}"

    def as_record(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.relative_path,
            "size": self.size,
            "kind": self.kind,
        }


def _unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise AttachmentError(f"could not find a free filename for {name!r}")


def stage_attachments(
    sources: object,
    *,
    workspace: str | Path,
    turn_id: str,
    allow_images: bool = True,
) -> tuple[StagedAttachment, ...]:
    """Copy each requested source into the workspace attachment directory.

    ``sources`` is the protocol payload: a list of ``{"path": ..., "name": ...}``
    objects. Nothing here trusts the client's ``name`` as a path component; it
    is a display label and is sanitised before it touches the filesystem.
    """
    if not sources:
        return ()
    if not isinstance(sources, (list, tuple)):
        raise AttachmentError("attachments must be a list")
    if len(sources) > MAX_ATTACHMENTS:
        raise AttachmentError(f"at most {MAX_ATTACHMENTS} attachments per message")

    root = Path(workspace).expanduser().resolve()
    folder = root.joinpath(*ATTACHMENT_DIRNAME.split("/")) / (str(turn_id or "turn")[:8] or "turn")

    staged: list[StagedAttachment] = []
    for entry in sources:
        if not isinstance(entry, dict):
            raise AttachmentError("each attachment must be an object")
        source = Path(str(entry.get("path") or "").strip()).expanduser()
        if not str(source):
            raise AttachmentError("attachment path must not be empty")
        if not source.is_file():
            raise AttachmentError(f"attachment file does not exist: {source}")

        display = str(entry.get("name") or source.name)
        name = safe_name(display)
        size = source.stat().st_size
        image = is_image(name) or is_image(source)
        if image and not allow_images:
            raise AttachmentError(
                f"the current model cannot read images, so {display!r} was not sent"
            )
        limit = MAX_IMAGE_BYTES if image else MAX_FILE_BYTES
        if size > limit:
            raise AttachmentError(
                f"{display!r} is {size / 1_048_576:.1f} MB; the limit is "
                f"{limit // 1_048_576} MB for {'images' if image else 'files'}"
            )

        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = _unique_path(folder, name)
            shutil.copyfile(source, target)
        except OSError as exc:
            raise AttachmentError(f"could not save {display!r} into the workspace: {exc}") from exc
        staged.append(
            StagedAttachment(
                name=display,
                path=target,
                relative_path=target.relative_to(root).as_posix(),
                size=size,
                kind="image" if image else "file",
            )
        )
    return tuple(staged)


def attachment_manifest(staged: tuple[StagedAttachment, ...]) -> str:
    """The lines appended to the user's message describing what they attached.

    Paths are workspace-relative because that is the shape the agent's own file
    tools take, and because an absolute path leaks the user's home directory
    into the transcript for no benefit.
    """
    if not staged:
        return ""
    lines = ["Attached files (already saved in this workspace):"]
    for item in staged:
        detail = "image, shown above" if item.is_image else "read it with the file tools"
        lines.append(f"- {item.name} — {item.relative_path} ({detail})")
    return "\n".join(lines)


def build_turn_content(
    text: str,
    staged: tuple[StagedAttachment, ...],
) -> str | tuple[ContentPart, ...]:
    """Assemble the message content for a turn that carries attachments."""
    prompt = str(text or "").strip()
    if not staged:
        if not prompt:
            raise AttachmentError("a message needs text or at least one attachment")
        return prompt

    manifest = attachment_manifest(staged)
    body = f"{prompt}\n\n{manifest}" if prompt else manifest
    images = [ImagePart(image_url=item.data_url()) for item in staged if item.is_image]
    # With no image there is nothing multipart about the message. Collapsing it
    # keeps a file attachment byte-identical to an ordinary text turn all the
    # way down to the provider payload.
    if not images:
        return body
    parts: list[ContentPart] = [TextPart(body), *images]
    return tuple(parts)


__all__ = [
    "ATTACHMENT_DIRNAME",
    "IMAGE_SUFFIXES",
    "MAX_ATTACHMENTS",
    "MAX_FILE_BYTES",
    "MAX_IMAGE_BYTES",
    "AttachmentError",
    "StagedAttachment",
    "attachment_manifest",
    "build_turn_content",
    "is_image",
    "safe_name",
    "stage_attachments",
]
