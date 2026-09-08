"""Serializable approval identity: names alone never identify executable bindings."""
from __future__ import annotations

import hashlib
import json
import marshal


def binding_digest(step, tool, platform) -> str:
    handler = tool.handler
    function = getattr(handler, "__func__", handler)
    code = getattr(function, "__code__", None)
    payload = {
        "version": 1,
        "workspace": step.world_state.workspace_dir,
        "profile": step.world_state.profile_id,
        "environment_policy": repr(step.environment_policy),
        "permission": {"mode": step.permissions.mode.value, "effects": sorted(x.value for x in step.permissions.profile.allowed_effects), "approval": step.permissions.approval_policy.value, "filesystem": step.permissions.file_system_access.value},
        "sandbox": step.world_state.sandbox.to_dict() if step.world_state.sandbox else None,
        "tool": tool.name,
        "binding_key": tool.binding_key,
        "defaults": repr(getattr(function, "__kwdefaults__", None)),
        "description": tool.description,
        "schema": tool.input_schema,
        "effect": tool.effect.value,
        "handler": f"{getattr(function, '__module__', '')}:{getattr(function, '__qualname__', '')}",
        "code": hashlib.sha256(marshal.dumps(code)).hexdigest() if code else str(type(handler)),
    }
    registry = getattr(platform, "registry", None)
    if registry is not None:
        payload["model"] = registry.get(step.world_state.profile_id).as_safe_dict()
    # Primitive captured configuration identifies factories without persisting secrets.
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
