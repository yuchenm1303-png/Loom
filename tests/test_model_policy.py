from __future__ import annotations

from pathlib import Path

from services.loom_model_policy.policy import PolicyStore


MODELS = ("m1", "m2", "m3")


def test_global_off_is_hard_deny(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db", MODELS)
    group = store.create_group(1, "team")
    store.set_membership(1, group["id"], 7, True)
    store.set_group_rule(1, group["id"], "m1", True)
    store.set_global_rule(1, "m1", False)

    access = store.effective_access(7, {"enabled": True, "models": [], "source": "default"})

    assert "m1" not in access["models"]
    assert next(item for item in access["decisions"] if item["model_id"] == "m1")["source"] == "global"


def test_explicit_user_rule_beats_access_group_but_not_global(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db", MODELS)
    group = store.create_group(1, "blocked")
    store.set_membership(1, group["id"], 9, True)
    store.set_group_rule(1, group["id"], "m1", False)
    store.set_user_rule(1, 9, "m1", True)
    store.set_user_rule(1, 9, "m2", False)

    access = store.effective_access(9, {"enabled": True, "models": [], "source": "default"})
    m1 = next(item for item in access["decisions"] if item["model_id"] == "m1")
    m2 = next(item for item in access["decisions"] if item["model_id"] == "m2")
    assert (m1["enabled"], m1["source"]) == (True, "user")
    assert (m2["enabled"], m2["source"]) == (False, "user")

    store.set_global_rule(1, "m1", False)
    access = store.effective_access(9, {"enabled": True, "models": [], "source": "default"})
    m1 = next(item for item in access["decisions"] if item["model_id"] == "m1")
    assert (m1["enabled"], m1["source"]) == (False, "global")


def test_multi_group_deny_wins(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db", MODELS)
    allow = store.create_group(1, "allow")
    deny = store.create_group(1, "deny")
    store.set_membership(1, allow["id"], 11, True)
    store.set_membership(1, deny["id"], 11, True)
    store.set_group_rule(1, allow["id"], "m2", True)
    store.set_group_rule(1, deny["id"], "m2", False)

    access = store.effective_access(11, {"enabled": True, "models": [], "source": "default"})
    decision = next(item for item in access["decisions"] if item["model_id"] == "m2")

    assert decision["enabled"] is False
    assert decision["source"] == "group"
    assert decision["groups"] == ["allow", "deny"]


def test_disabled_access_group_does_not_apply(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db", MODELS)
    group = store.create_group(1, "paused")
    store.set_membership(1, group["id"], 13, True)
    store.set_group_rule(1, group["id"], "m3", False)
    store.update_group(1, group["id"], enabled=False)

    access = store.effective_access(13, {"enabled": True, "models": [], "source": "default"})

    assert "m3" in access["models"]
    assert access["groups"] == []


def test_new_access_group_copies_current_global_switches(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db", MODELS)
    store.set_global_rule(1, "m2", False)

    group = store.create_group(1, "new")
    rules = {item["model_id"]: item["enabled"] for item in group["models"]}

    assert rules == {"m1": True, "m2": False, "m3": True}


def test_provider_group_off_is_hard_deny(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db")
    store.set_user_rule(1, 15, "builtin:deepseek", True)
    store.set_global_model_group(1, "deepseek", False)

    access = store.effective_access(15, {"enabled": True, "models": [], "source": "default"})
    decision = next(item for item in access["decisions"] if item["model_id"] == "builtin:deepseek")

    assert decision["enabled"] is False
    assert decision["source"] == "global_group"


def test_legacy_ant_ling_override_does_not_disable_other_providers(tmp_path: Path) -> None:
    store = PolicyStore(tmp_path / "policy.db")
    access = store.effective_access(
        17,
        {"enabled": True, "models": ["Ling-3.0-flash"], "source": "override"},
    )
    ant_default = next(item for item in access["decisions"] if item["model_id"] == "builtin:ant-ling")
    ant_vl = next(item for item in access["decisions"] if item["model_id"] == "builtin:ant-ling:Ling-3.0-flash-VL")
    deepseek = next(item for item in access["decisions"] if item["model_id"] == "builtin:deepseek")

    assert (ant_default["enabled"], ant_default["source"]) == (True, "user_legacy")
    assert (ant_vl["enabled"], ant_vl["source"]) == (False, "user_legacy")
    assert (deepseek["enabled"], deepseek["source"]) == (True, "global")
