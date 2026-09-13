from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai import ToolCall


_MAX_PATCH_CHARS = 2_000_000
_MAX_PATCH_OPERATIONS = 128
_PATCH_ACTIONS = frozenset({"add", "update", "delete", "move"})
_PATCH_ARGUMENT_NAMES = frozenset({"changes", "patch"})
_PATCH_CHANGE_NAMES = frozenset(
    {
        "action",
        "path",
        "content",
        "old_text",
        "new_text",
        "expected_text",
        "move_to",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_workspace_path(workspace: str | Path, raw_path: object) -> str:
    if not isinstance(raw_path, str):
        raise ValueError("patch path must be a string")
    value = raw_path.strip()
    if not value:
        raise ValueError("patch path must not be empty")
    root = Path(workspace).expanduser().resolve()
    resolved = (root / value).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("patch path escapes the workspace") from exc


def _structured_request(workspace: str | Path, raw_changes: object) -> tuple[str, tuple[str, ...]]:
    if not isinstance(raw_changes, list) or not raw_changes:
        raise ValueError("changes must be a non-empty array")
    if len(raw_changes) > _MAX_PATCH_OPERATIONS:
        raise ValueError("patch contains too many file operations")

    normalized: list[dict[str, object]] = []
    paths: set[str] = set()
    for index, raw in enumerate(raw_changes):
        if not isinstance(raw, dict):
            raise ValueError(f"changes[{index}] must be an object")
        extras = sorted(set(raw) - _PATCH_CHANGE_NAMES)
        if extras:
            raise ValueError(f"changes[{index}] contains unsupported fields: {', '.join(extras)}")

        action = raw.get("action")
        if not isinstance(action, str) or action not in _PATCH_ACTIONS:
            raise ValueError(f"unsupported patch action at changes[{index}]: {action!r}")
        path = _canonical_workspace_path(workspace, raw.get("path"))

        item: dict[str, object] = dict(raw)
        item["action"] = action
        item["path"] = path
        paths.add(path)

        for name in ("content", "old_text", "new_text", "expected_text"):
            if name in item and not isinstance(item[name], str):
                raise ValueError(f"changes[{index}].{name} must be a string")
        if "move_to" in item:
            destination = _canonical_workspace_path(workspace, item["move_to"])
            item["move_to"] = destination
            paths.add(destination)
        normalized.append(item)
    return _digest(normalized), tuple(sorted(paths))


def _text_request(workspace: str | Path, raw_patch: object) -> tuple[str, tuple[str, ...]]:
    if not isinstance(raw_patch, str) or len(raw_patch) > _MAX_PATCH_CHARS:
        raise ValueError("patch must be a string of at most 2,000,000 characters")
    lines = raw_patch.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("patch requires Begin Patch and End Patch markers")

    paths: set[str] = set()
    prefixes = (
        "*** Add File: ",
        "*** Delete File: ",
        "*** Update File: ",
        "*** Move to: ",
    )
    for line in lines[1:-1]:
        for prefix in prefixes:
            if line.startswith(prefix):
                paths.add(_canonical_workspace_path(workspace, line[len(prefix):]))
                break

    canonical_text = "\n".join(lines)
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(), tuple(sorted(paths))


@dataclass(frozen=True, slots=True)
class ApplyPatchActionIdentity:
    """Secret-minimized identity for the requested apply_patch transformation.

    The identity intentionally does not include live file preimages. A preceding
    tool in the same sampled batch may legitimately change the workspace before
    this call executes. Atomic preimage validation remains the responsibility of
    ApplyPatchRuntime at execution time.
    """

    call_id: str
    input_format: str
    paths: tuple[str, ...]
    request_digest: str

    @property
    def kind(self) -> str:
        return "apply_patch"

    @classmethod
    def build(cls, step, call: ToolCall) -> "ApplyPatchActionIdentity":
        if str(call.name or "") != "apply_patch":
            raise ValueError("ApplyPatchActionIdentity requires an apply_patch tool call")
        arguments = dict(call.arguments)
        extras = sorted(set(arguments) - _PATCH_ARGUMENT_NAMES)
        if extras:
            raise ValueError(f"apply_patch contains unsupported arguments: {', '.join(extras)}")
        has_patch = "patch" in arguments
        has_changes = "changes" in arguments
        if has_patch == has_changes:
            raise ValueError("apply_patch requires exactly one of patch or changes")

        workspace = step.world_state.workspace_dir
        if has_patch:
            request_digest, paths = _text_request(workspace, arguments["patch"])
            input_format = "text"
        else:
            request_digest, paths = _structured_request(workspace, arguments["changes"])
            input_format = "structured"
        return cls(
            call_id=str(call.call_id or "").strip(),
            input_format=input_format,
            paths=paths,
            request_digest=request_digest,
        )

    def binding_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "input_format": self.input_format,
            "paths": list(self.paths),
            "request_digest": self.request_digest,
        }

    def instance_payload(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            **self.binding_payload(),
        }

    def digest(self) -> str:
        return _digest(self.binding_payload())


__all__ = ["ApplyPatchActionIdentity"]
