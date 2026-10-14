"""Shared built-in routes. Upstream credentials are resolved only by the server."""
from urllib.parse import urlparse

PROVIDERS = {
    "intern": ("https://discovery-api.intern-ai.org.cn/v1", ("LOOM_INTERN_API_KEY",), ("LOOM_INTERN_BASE_URL",)),
    "minimax": ("https://api.minimaxi.com/v1", ("MINIMAX_API_KEY", "LOOM_PRIMARY_API_KEY"), ("LOOM_MINIMAX_BASE_URL", "MINIMAX_BASE_URL")),
    "deepseek": ("https://api.deepseek.com", ("DEEPSEEK_API_KEY", "LOOM_DEEPSEEK_API_KEY"), ("LOOM_DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL")),
    "ant-ling": ("https://api.ant-ling.com/v1", ("LOOM_ANT_LING_API_KEY", "ANT_LING_API_KEY"), ("LOOM_ANT_LING_BASE_URL",)),
    "opencode-go": ("https://opencode.ai/zen/go/v1", ("OPENCODE_GO_API_KEY", "LOOM_OPENCODE_GO_API_KEY"), ("LOOM_OPENCODE_GO_BASE_URL",)),
    "managed-relay": ("https://muxway.dev/v1", ("LOOM_RELAY_API_KEY", "SMIREL_RELAY_API_KEY", "LOOM_CQU_API_KEY"), ("LOOM_RELAY_BASE_URL", "SMIREL_RELAY_BASE_URL")),
}


def gateway_path(group_id: str) -> str:
    if group_id not in PROVIDERS:
        raise ValueError("unknown built-in provider group")
    return "" if group_id == "ant-ling" else "/" + group_id


def gateway_base_url(group_id: str, environ) -> str:
    base = str(environ.get("LOOM_MODEL_GATEWAY_BASE_URL") or "https://account.smirel.com/model/v1").strip().rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"})) or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Loom model gateway must use HTTPS or loopback HTTP without URL credentials")
    return base + gateway_path(group_id)


def request_route(path: str) -> tuple[str, str]:
    path = path.rstrip("/")
    if not path.startswith("/v1/"):
        raise ValueError("unknown model endpoint")
    rest = path[len("/v1/"):]
    group = "ant-ling"
    prefix, _, suffix = rest.partition("/")
    if prefix in PROVIDERS:
        group, rest = prefix, suffix
    allowed = {"models", "chat/completions"}
    if group == "opencode-go":
        allowed.update({"responses", "messages"})
    if rest not in allowed:
        raise ValueError("unknown model endpoint")
    return group, rest


def server_upstreams(environ) -> dict[str, tuple[str, str]]:
    result = {}
    for group, (default, keys, urls) in PROVIDERS.items():
        base = next((environ[name].strip() for name in urls if environ.get(name, "").strip()), default)
        key = next((environ[name].strip() for name in keys if environ.get(name, "").strip()), "")
        result[group] = (base.rstrip("/"), key)
    return result
