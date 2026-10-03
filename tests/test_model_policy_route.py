from scripts.ensure_model_policy_route import ensure_route


def test_route_is_inserted_once_and_preserves_other_hosts():
    original = "other.example {\n respond ok\n}\naccount.smirel.com {\n handle { reverse_proxy loom-account:8787 }\n}\n"
    updated = ensure_route(original)
    assert "reverse_proxy loom-model-policy:8792" in updated
    assert updated.startswith("other.example {\n respond ok\n}\n")
    assert ensure_route(updated) == updated
