"""Resolved shell environment policy, shared by exec and approval identity."""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import Mapping


_BASE = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME", "USERPROFILE", "TEMP", "TMP", "LANG"}
_SECRETS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")


@dataclass(frozen=True, slots=True)
class ShellEnvironmentPolicy:
    inherit: str = "all"
    include_only: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def __post_init__(self):
        if self.inherit not in {"all", "core", "none"}:
            raise ValueError("environment inherit must be all, core, or none")
        object.__setattr__(self, "include_only", tuple(self.include_only))
        object.__setattr__(self, "exclude", tuple(self.exclude))

    def build(self, overrides: Mapping[str, object] | None = None) -> dict[str, str]:
        def secret(name):
            return any(marker in name.upper() for marker in _SECRETS)
        def matches(name, patterns):
            return any(fnmatch.fnmatchcase(name.upper(), p.upper()) for p in patterns)
        output = {}
        for name, value in os.environ.items():
            if self.inherit == "none" or (self.inherit == "core" and name.upper() not in _BASE):
                continue
            if secret(name) or matches(name, self.exclude):
                continue
            if self.include_only and not matches(name, self.include_only):
                continue
            output[name] = value
        for key, value in (overrides or {}).items():
            key, value = str(key), str(value)
            if not key or "=" in key or "\0" in key or "\0" in value or len(key) > 1024 or len(value) > 64_000:
                raise ValueError("invalid environment override")
            if secret(key):
                raise ValueError(f"secret-like environment override is not allowed: {key}")
            if matches(key, self.exclude) or (self.include_only and not matches(key, self.include_only)):
                raise ValueError(f"environment override denied by policy: {key}")
            # Windows environment variable identity is case-insensitive.
            if os.name == "nt":
                output = {k: v for k, v in output.items() if k.casefold() != key.casefold()}
            output[key] = value
        return output
