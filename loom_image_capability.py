"""Endpoint capability evidence. Missing metadata is never a negative answer.

No model-name/provider heuristics: only explicit declarations or complete input
modality metadata are authoritative. Unknown inputs may be sent to the endpoint,
but must not be advertised as confirmed vision support.
"""
from __future__ import annotations
from collections.abc import Mapping
from contextlib import closing
from enum import Enum


class ImageInput(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"

    @classmethod
    def from_support(cls, value):
        return cls.SUPPORTED if value is True else cls.UNSUPPORTED if value is False else cls.UNKNOWN

    @property
    def supported(self):
        return True if self is ImageInput.SUPPORTED else False if self is ImageInput.UNSUPPORTED else None



def image_support(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def image_capability(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        return {"supported": None, "source": "unknown"}
    normalized = metadata.get("image_input")
    signals: list[tuple[bool, str]] = []
    if isinstance(normalized, Mapping):
        value = image_support(normalized.get("supported"))
        if value is not None:
            signals.append((value, "image_input"))
    for key in ("vision", "supports_vision", "supports_image_input"):
        value = image_support(metadata.get(key))
        if value is not None:
            signals.append((value, key))
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, Mapping):
        for key in ("vision", "image_input"):
            value = image_support(capabilities.get(key))
            if value is not None:
                signals.append((value, "capabilities." + key))
    architecture = metadata.get("architecture")
    for source, modalities in (
        ("input_modalities", metadata.get("input_modalities")),
        ("architecture.input_modalities", architecture.get("input_modalities") if isinstance(architecture, Mapping) else None),
    ):
        if isinstance(modalities, list) and modalities and all(isinstance(item, str) for item in modalities):
            signals.append(("image" in [item.casefold() for item in modalities], source))
    if not signals:
        return {"supported": None, "source": str(normalized.get("source") or "unknown")[:120] if isinstance(normalized, Mapping) else "unknown"}
    if len({value for value, _ in signals}) != 1:
        return {"supported": None, "source": "conflicting-metadata"}
    source = str(normalized.get("source") or "unknown")[:120] if isinstance(normalized, Mapping) else "provider/models:" + signals[0][1]
    return {"supported": signals[0][0], "source": source}


class ImageCapabilityStore:
    """Non-secret, endpoint/model-scoped evidence shared by bridge processes.

    Provider metadata expires; configurations remain explicit in the model store.
    SQLite transactions keep concurrent model refreshes from losing updates.
    """
    def __init__(self, home):
        from pathlib import Path
        self.home = Path(home)

    def _connect(self):
        import sqlite3
        self.home.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.home / "image-capabilities.sqlite3")
        db.execute("CREATE TABLE IF NOT EXISTS evidence(endpoint TEXT, model TEXT, supported INTEGER, source TEXT, observed REAL, PRIMARY KEY(endpoint, model))")
        return db

    @staticmethod
    def _key(endpoint, model):
        return str(endpoint).strip().rstrip("/"), str(model).strip().casefold()

    def put(self, endpoint, model, evidence):
        import time
        supported = image_support(evidence.get("supported"))
        source = str(evidence.get("source") or "unknown")[:120]
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO evidence VALUES (?, ?, ?, ?, ?)", (*self._key(endpoint, model), supported, source, time.time()))

    def get(self, endpoint, model):
        import time
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT supported, source, observed FROM evidence WHERE endpoint=? AND model=?", self._key(endpoint, model)).fetchone()
        if not row or time.time() - row[2] > 86400:
            return None
        return {"supported": None if row[0] is None else bool(row[0]), "source": row[1]}
