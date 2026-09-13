"""Serializable approval identity: prompt packaging never changes executable identity."""
from __future__ import annotations

import hashlib
import hmac
import json
import marshal
import secrets

from .json_schema_semantics import validating_schema


_PROCESS_ENV_BINDING_KEY = secrets.token_bytes(32)


def _exec_environment_identity(step, tool) -> str:
    if str(getattr(tool, "name", "") or "") != "exec":
        return ""
    environment = step.environment_policy.build()
    raw = json.dumps(
        environment,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_PROCESS_ENV_BINDING_KEY, raw, hashlib.sha256).hexdigest()


def binding_digest(step, tool, platform) -> str:
    """Hash executable semantics against the exact sampling-step identity.

    Production/default steps bind to their frozen request-state snapshot. Lower-
    level embedders that still construct an uncaptured StepContext retain the
    previous live-registry model identity fallback instead of silently losing that
    approval protection.

    Tool descriptions and JSON Schema annotation keywords help the model choose a
    tool but do not change what arguments validate or what handler executes. They
    are intentionally excluded so full/compact/structural prompt projections all
    represent the same approval binding.

    Exec also binds to the resolved child environment through a process-local
    fingerprint. The environment map itself is not persisted. A changed inherited
    environment therefore invalidates the pending exec binding, and a process
    restart intentionally requires a fresh exec approval.
    """

    handler = tool.handler
    function = getattr(handler, "__func__", handler)
    code = getattr(function, "__code__", None)
    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
    payload = {
        "version": 4,
        "workspace": step.world_state.workspace_dir,
        "profile": step.world_state.profile_id,
        "environment_policy": repr(step.environment_policy),
        "exec_environment_identity": _exec_environment_identity(step, tool),
        "permission": {
            "mode": step.permissions.mode.value,
            "effects": sorted(x.value for x in step.permissions.profile.allowed_effects),
            "approval": step.permissions.approval_policy.value,
            "filesystem": step.permissions.file_system_access.value,
        },
        "sandbox": step.world_state.sandbox.to_dict() if step.world_state.sandbox else None,
        "tool": tool.name,
        "binding_key": tool.binding_key,
        "defaults": repr(getattr(function, "__kwdefaults__", None)),
        "schema": validating_schema(tool.input_schema),
        "effect": tool.effect.value,
        "handler": f"{getattr(function, '__module__', '')}:{getattr(function, '__qualname__', '')}",
        "code": hashlib.sha256(marshal.dumps(code)).hexdigest() if code else str(type(handler)),
    }
    if captured:
        payload["request_state"] = request_state.digest()
    else:
        registry = getattr(platform, "registry", None)
        if registry is not None:
            try:
                profile = registry.get(step.world_state.profile_id)
            except (AttributeError, KeyError, TypeError, ValueError):
                profile = None
            safe = getattr(profile, "as_safe_dict", None)
            if callable(safe):
                payload["model"] = safe()

    closure = getattr(function, "__closure__", None) or ()
    values = []
    for cell in closure:
        value = cell.cell_contents
        if isinstance(value, (str, int, float, bool, type(None))):
            values.append(value)
        elif hasattr(value, "config"):
            values.append(repr(value.config))
    payload["closure"] = values
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


__all__ = ["binding_digest"]
