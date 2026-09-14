"""Process-local frozen child-environment authority for one sampled Step.

A ``ShellEnvironmentPolicy`` describes how to derive a child environment from
``os.environ``. That policy alone is not a frozen execution world: the parent
environment may change while an approval is pending. ``FrozenShellEnvironment``
resolves the inherited/operator portion once and then only applies the explicit
per-call ``exec.env`` overrides allowed by the captured policy.

The raw values intentionally stay process-local on ``StepContext``. They are not
serialized into durable session state or model-visible metadata.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping

from .shell_environment import ShellEnvironmentPolicy


@dataclass(frozen=True, slots=True)
class FrozenShellEnvironment:
    """Exact base child environment captured at a semantic sampling boundary."""

    policy: ShellEnvironmentPolicy = field(repr=False)
    _base_items: tuple[tuple[str, str], ...] = field(repr=False)

    @classmethod
    def capture(cls, policy: ShellEnvironmentPolicy) -> "FrozenShellEnvironment":
        if not isinstance(policy, ShellEnvironmentPolicy):
            raise TypeError("frozen environment requires ShellEnvironmentPolicy")
        # Sort only for deterministic identity/debug behaviour. Environment map
        # ordering has no execution semantics.
        base = tuple(sorted(policy.build().items(), key=lambda item: item[0].casefold()))
        return cls(policy=policy, _base_items=base)

    def build(self, overrides: Mapping[str, object] | None = None) -> dict[str, str]:
        """Apply one call's explicit overrides without rereading ``os.environ``.

        Replaying the already-resolved base as operator ``set`` entries lets the
        canonical policy implementation keep ownership of override validation,
        case handling, non-inheritable stripping, and Windows PATHEXT fallback.
        Default host-secret filtering is disabled on replay because it was
        already applied when the base snapshot was captured.
        """

        replay = ShellEnvironmentPolicy(
            inherit="none",
            ignore_default_excludes=True,
            exclude=self.policy.exclude,
            set_vars=self._base_items,
            include_only=self.policy.include_only,
            allow_secrets=self.policy.allow_secrets,
        )
        return replay.build(overrides)

    @property
    def inherit(self) -> str:
        return self.policy.inherit

    @property
    def ignore_default_excludes(self) -> bool:
        return self.policy.ignore_default_excludes

    @property
    def exclude(self) -> tuple[str, ...]:
        return self.policy.exclude

    @property
    def set_vars(self) -> tuple[tuple[str, str], ...]:
        return self.policy.set_vars

    @property
    def include_only(self) -> tuple[str, ...]:
        return self.policy.include_only

    @property
    def allow_secrets(self) -> tuple[str, ...]:
        return self.policy.allow_secrets

    def __repr__(self) -> str:
        # Never include captured environment values in diagnostics/repr. The
        # policy digest still makes semantic policy changes visible to callers
        # that use repr as part of an integrity binding.
        policy_digest = hashlib.sha256(repr(self.policy).encode("utf-8")).hexdigest()[:16]
        return (
            "FrozenShellEnvironment("
            f"inherit={self.policy.inherit!r}, variables={len(self._base_items)}, "
            f"policy_sha256={policy_digest!r})"
        )


__all__ = ["FrozenShellEnvironment"]
