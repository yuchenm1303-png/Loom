from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    group_id: str
    group_name: str
    legacy_account_id: str = ""

    def payload(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "group_id": self.group_id,
            "group_name": self.group_name,
        }


@dataclass(frozen=True)
class CatalogGroup:
    id: str
    name: str
    selection_prefixes: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "selection_prefixes": list(self.selection_prefixes)}


def _selection(prefix: str, model: str, default_model: str) -> str:
    value = str(model).strip()
    if value.casefold() == default_model.casefold():
        return prefix.rstrip(":")
    return prefix + quote(value, safe="")


_MINIMAX = (
    "MiniMax-M3", "MiniMax-M2", "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
    "MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
)
_DEEPSEEK = ("deepseek-flash", "deepseek-v4-pro")
_ANT_LING = (
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "Ling-3.0-tiny",
    "Ling-2.6-1T",
    "Ring-2.6-1T",
    "Ling-2.6-flash",
)
_OPENCODE_GO = (
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
    "longcat-2.0",
    "glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5",
    "deepseek-v4-pro", "deepseek-v4.1-flash", "deepseek-v4-flash",
    "deepseek-flash", "deepseek-v4-flash-vision-exp",
    "qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus",
    "qwen3.6-plus", "qwen3.5-plus",
    "mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-pro", "mimo-v2-omni",
    "hy4-preview", "hy3", "hy3-preview",
    "gpt-5.6-luna", "grok-4.6", "grok-4.5",
    "muse-spark-1.3-contributor", "muse-spark-1.2-contributor",
    "omen-alpha",
)


def _title_model(value: str) -> str:
    return str(value).replace("-", " ").replace("_", " ").title()


GROUPS = (
    CatalogGroup("minimax", "MiniMax", ("builtin:minimax",)),
    CatalogGroup("deepseek", "DeepSeek", ("builtin:deepseek",)),
    CatalogGroup("ant-ling", "Ant Ling", ("builtin:ant-ling",)),
    CatalogGroup("opencode-go", "OpenCode Go", ("builtin:opencode-go:",)),
    CatalogGroup("managed-relay", "Muxway Relay", ("managed:", "builtin:cqu")),
)

MODELS = tuple(
    [
        CatalogModel(
            _selection("builtin:minimax:", model, "MiniMax-M3"),
            "MiniMax" if model == "MiniMax-M3" else model.replace("MiniMax-", "MiniMax "),
            "minimax",
            "MiniMax",
        )
        for model in _MINIMAX
    ]
    + [
        CatalogModel(
            _selection("builtin:deepseek:", model, "deepseek-flash"),
            "DeepSeek Flash" if model == "deepseek-flash" else "DeepSeek V4 Pro",
            "deepseek",
            "DeepSeek",
        )
        for model in _DEEPSEEK
    ]
    + [
        CatalogModel(
            _selection("builtin:ant-ling:", model, "Ling-3.0-flash"),
            model.replace("Ling-", "Ling ").replace("Ring-", "Ring ").replace("-", " "),
            "ant-ling",
            "Ant Ling",
            legacy_account_id=model,
        )
        for model in _ANT_LING
    ]
    + [
        CatalogModel(
            "builtin:opencode-go:" + quote(model, safe=""),
            _title_model(model),
            "opencode-go",
            "OpenCode Go",
        )
        for model in _OPENCODE_GO
    ]
    + [CatalogModel("builtin:cqu", "CQU 弘深", "managed-relay", "Muxway Relay")]
)

MODEL_BY_ID = {item.id: item for item in MODELS}
GROUP_BY_ID = {item.id: item for item in GROUPS}
LEGACY_ACCOUNT_MODEL_TO_POLICY = {
    item.legacy_account_id.casefold(): item.id for item in MODELS if item.legacy_account_id
}


def group_for_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    model = MODEL_BY_ID.get(value)
    if model is not None:
        return model.group_id
    for group in GROUPS:
        if any(value == prefix or value.startswith(prefix if prefix.endswith(":") else prefix + ":") for prefix in group.selection_prefixes):
            return group.id
    return None


def ant_ling_policy_id(model: str) -> str:
    value = str(model or "").strip()
    if not value:
        raise ValueError("model is required")
    return _selection("builtin:ant-ling:", value, "Ling-3.0-flash")


def is_managed_builtin_selection(selection: str) -> bool:
    value = str(selection or "").strip()
    return value.startswith("builtin:") or value.startswith("managed:")
