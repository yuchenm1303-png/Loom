"""Account-owned built-ins share one credential-free desktop launch contract."""
import os
from services.loom_model_policy.catalog import group_for_selection
from services.loom_model_gateway.routes import PROVIDERS, gateway_base_url


def account_builtin_profile(profile: dict, environ=None) -> dict:
    group = group_for_selection(str(profile.get("selection") or ""))
    if group is None or profile.get("kind") == "saved":
        return profile
    return {**profile, "baseUrl": gateway_base_url(group, os.environ if environ is None else environ),
        "adapter": "opencode-go" if group == "opencode-go" else "openai-compatible",
        "authMode": "loom-account", "configured": True, "managed": True,
        "personalConnection": {"adapter": "openai-compatible", "baseUrl": PROVIDERS[group][0]}}


def is_account_builtin(selection: str) -> bool:
    return group_for_selection(selection) is not None


def is_builtin_gateway(selection: str, base_url: str) -> bool:
    group = group_for_selection(selection)
    return group is not None and base_url.rstrip("/") == gateway_base_url(group, os.environ)
