from types import SimpleNamespace

import pytest

from app.app_server import LoomAppServerService


def test_model_credential_sync_updates_rotated_token_and_clears_on_signout():
    service = SimpleNamespace(_loom_account_model_credential="old")
    method = LoomAppServerService.account_tool_access_credential_set
    method(service, {"credential": "", "modelCredential": "loom_model_new"})
    assert service._loom_account_model_credential == "loom_model_new"
    method(service, {"credential": ""})
    assert service._loom_account_model_credential == "loom_model_new"
    method(service, {"credential": "", "modelCredential": ""})
    assert service._loom_account_model_credential == ""


def test_invalid_sync_does_not_replace_model_credential():
    service = SimpleNamespace(_loom_account_model_credential="old")
    with pytest.raises(ValueError):
        LoomAppServerService.account_tool_access_credential_set(service, {"modelCredential": 42})
    assert service._loom_account_model_credential == "old"
