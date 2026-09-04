from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diff_tracker import TurnDiffTracker
from .tools import ToolContext


_MAX_EDITABLE_BYTES = 1_000_000
_MAX_WRITE_CHARS = 1_000_000


@dataclass(frozen=True, slots=True)
class PlannedFileChange:
    path: str
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class PatchPlan:
    changes: tuple[PlannedFileChange, ...]


@dataclass(frozen=True, slots=True)
class PatchApplyResult:
    paths: tuple[str, ...]
    diff: str
    diff_revision: int
    diff_truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "paths": list(self.paths),
            "diff": self.diff,
            "diff_revision": self.diff_revision,
            "diff_truncated": self.diff_truncated,
        }


class ApplyPatchRuntime:
    """Validate a multi-file text patch completely before touching the filesystem.

    The v1 patch language is structured JSON rather than shell/git syntax. This
    keeps validation deterministic across Windows/Linux and non-git workspaces.
    Supported actions are add, update, delete and move. Update defaults to exact
    one-location replacement and can also replace a whole file via ``content``.
    """

    def plan(self, context: ToolContext, raw_changes: object) -> PatchPlan:
        if not isinstance(raw_changes, list) or not raw_changes:
            raise ValueError("changes must be a non-empty array")
        if len(raw_changes) > 128:
            raise ValueError("patch contains too many file operations")

        workspace = context.workspace.resolve()
        original: dict[str, str | None] = {}
        virtual: dict[str, str | None] = {}
        touched: set[str] = set()

        def canonical(relative: object) -> str:
            value = str(relative or "").strip()
            if not value:
                raise ValueError("patch path must not be empty")
            resolved = context.resolve_workspace_path(value)
            try:
                return resolved.relative_to(workspace).as_posix()
            except ValueError as exc:  # defensive; resolve_workspace_path already checks this
                raise ValueError("patch path escapes the workspace") from exc

        def read_initial(relative: str) -> str | None:
            if relative in original:
                return original[relative]
            target = context.resolve_workspace_path(relative)
            if target.exists() and not target.is_file():
                raise ValueError(f"patch target is not a regular file: {relative}")
            if not target.exists():
                value = None
            else:
                if target.stat().st_size > _MAX_EDITABLE_BYTES:
                    raise ValueError(f"patch target exceeds editable size limit: {relative}")
                try:
                    value = target.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"patch target is not UTF-8 text: {relative}") from exc
            original[relative] = value
            virtual[relative] = value
            return value

        def current(relative: str) -> str | None:
            if relative not in virtual:
                read_initial(relative)
            return virtual[relative]

        for index, raw in enumerate(raw_changes):
            context.raise_if_cancelled()
            if not isinstance(raw, dict):
                raise ValueError(f"changes[{index}] must be an object")
            action = str(raw.get("action") or "").strip().casefold()
            path = canonical(raw.get("path"))
            before = current(path)

            if action == "add":
                if before is not None:
                    raise ValueError(f"add target already exists: {path}")
                content = raw.get("content")
                if not isinstance(content, str):
                    raise ValueError(f"add requires string content: {path}")
                self._validate_content(content)
                virtual[path] = content
                touched.add(path)
                continue

            if action == "update":
                if before is None:
                    raise ValueError(f"update target does not exist: {path}")
                if "content" in raw:
                    content = raw.get("content")
                    if not isinstance(content, str):
                        raise ValueError(f"update content must be a string: {path}")
                    self._validate_content(content)
                    virtual[path] = content
                    touched.add(path)
                    continue
                old_text = raw.get("old_text")
                new_text = raw.get("new_text")
                if not isinstance(old_text, str) or not old_text:
                    raise ValueError(f"update requires non-empty old_text: {path}")
                if not isinstance(new_text, str):
                    raise ValueError(f"update requires string new_text: {path}")
                count = before.count(old_text)
                if count == 0:
                    raise ValueError(f"old_text was not found in {path}")
                if count > 1:
                    raise ValueError(
                        f"old_text matched {count} locations in {path}; make the block more specific"
                    )
                updated = before.replace(old_text, new_text, 1)
                self._validate_content(updated)
                virtual[path] = updated
                touched.add(path)
                continue

            if action == "delete":
                if before is None:
                    raise ValueError(f"delete target does not exist: {path}")
                expected = raw.get("expected_text")
                if expected is not None and str(expected) != before:
                    raise ValueError(f"delete expected_text does not match current file: {path}")
                virtual[path] = None
                touched.add(path)
                continue

            if action == "move":
                if before is None:
                    raise ValueError(f"move source does not exist: {path}")
                destination = canonical(raw.get("move_to"))
                if destination == path:
                    raise ValueError("move destination must differ from source")
                if current(destination) is not None:
                    raise ValueError(f"move destination already exists: {destination}")
                virtual[path] = None
                virtual[destination] = before
                touched.add(path)
                touched.add(destination)
                continue

            raise ValueError(f"unsupported patch action at changes[{index}]: {action!r}")

        planned = tuple(
            PlannedFileChange(path, original.get(path), virtual.get(path))
            for path in sorted(touched)
            if original.get(path) != virtual.get(path)
        )
        if not planned:
            raise ValueError("patch has no effective file changes")
        return PatchPlan(planned)

    def apply(
        self,
        context: ToolContext,
        raw_changes: object,
        *,
        diff_tracker: TurnDiffTracker,
    ) -> PatchApplyResult:
        plan = self.plan(context, raw_changes)
        context.raise_if_cancelled()

        # Optimistic preimage check: fail before mutation if a file changed since planning.
        for change in plan.changes:
            current = self._read_current(context, change.path)
            if current != change.before:
                raise RuntimeError(f"patch preimage changed before apply: {change.path}")

        temp_files: dict[str, Path] = {}
        committed: list[PlannedFileChange] = []
        try:
            for change in plan.changes:
                if change.after is None:
                    continue
                target = context.resolve_workspace_path(change.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.parent / f".{target.name}.loom-patch-{uuid.uuid4().hex}.tmp"
                with temp.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(change.after)
                    handle.flush()
                    os.fsync(handle.fileno())
                temp_files[change.path] = temp

            context.raise_if_cancelled()
            for change in plan.changes:
                target = context.resolve_workspace_path(change.path)
                if change.after is None:
                    if target.exists():
                        target.unlink()
                else:
                    os.replace(temp_files[change.path], target)
                committed.append(change)
        except Exception:
            # Best-effort rollback to the exact validated preimages.
            for change in reversed(committed):
                try:
                    self._restore(context, change.path, change.before)
                except Exception:
                    pass
            raise
        finally:
            for temp in temp_files.values():
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

        for change in plan.changes:
            diff_tracker.record_text_change(
                change.path,
                before=change.before,
                after=change.after,
            )
        snapshot = diff_tracker.snapshot()
        return PatchApplyResult(
            paths=tuple(change.path for change in plan.changes),
            diff=snapshot.diff,
            diff_revision=snapshot.revision,
            diff_truncated=snapshot.truncated,
        )

    @staticmethod
    def _validate_content(content: str) -> None:
        if len(content) > _MAX_WRITE_CHARS:
            raise ValueError(f"patched text exceeds {_MAX_WRITE_CHARS:,} characters")

    @staticmethod
    def _read_current(context: ToolContext, relative: str) -> str | None:
        target = context.resolve_workspace_path(relative)
        if not target.exists():
            return None
        if not target.is_file():
            raise RuntimeError(f"patch target is no longer a regular file: {relative}")
        if target.stat().st_size > _MAX_EDITABLE_BYTES:
            raise RuntimeError(f"patch target exceeds editable size limit: {relative}")
        return target.read_text(encoding="utf-8")

    @staticmethod
    def _restore(context: ToolContext, relative: str, content: str | None) -> None:
        target = context.resolve_workspace_path(relative)
        if content is None:
            target.unlink(missing_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / f".{target.name}.loom-rollback-{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


__all__ = [
    "ApplyPatchRuntime",
    "PatchApplyResult",
    "PatchPlan",
    "PlannedFileChange",
]
