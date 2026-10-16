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


def validate_model_connection(spec: dict) -> dict:
    """Validate the resolved identity before any credential reaches a transport."""
    selection = str(spec.get("selection") or "")
    account = spec.get("authMode") == "loom-account"
    if is_account_builtin(selection):
        if spec.get("kind") != "builtin" or not account or not is_builtin_gateway(selection, str(spec.get("baseUrl") or "")):
            raise ValueError("Built-in model identity must resolve to its Loom account gateway")
        if spec.get("apiKey") and not str(spec["apiKey"]).startswith("loom_model_"):
            raise ValueError("A provider key cannot authenticate a Loom account connection")
    elif selection.startswith(("profile:", "saved:")):
        if spec.get("kind") != "saved" or account or str(spec.get("apiKey") or "").startswith("loom_model_"):
            raise ValueError("Personal model connection contains incompatible account authentication")
        from services.loom_model_gateway.routes import reject_personal_gateway_url
        reject_personal_gateway_url(str(spec.get("baseUrl") or ""), os.environ)
    else:
        raise ValueError("Unknown model connection identity")
    return spec
