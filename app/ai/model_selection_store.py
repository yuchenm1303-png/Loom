from __future__ import annotations

import os
import uuid
from pathlib import Path


class ModelSelectionStore:
    """Persist only the active model selection; never stores credentials."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home).expanduser().resolve()
        self.path = self.home / "active-model-selection"

    def get(self) -> str | None:
        if not self.path.is_file():
            return None
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def set(self, selection: str) -> None:
        value = str(selection or "").strip()
        if not value:
            raise ValueError("model selection must not be empty")
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(value + "\n", encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise


__all__ = ["ModelSelectionStore"]
