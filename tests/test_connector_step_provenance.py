from __future__ import annotations

import json

from app.agent_runtime import PermissionMode, RequestStateSnapshot, StepContext, ToolRegistry
from app.connector_step_provenance import install_connector_step_provenance


class FakeManager:
    secret = "github-secret-never-persist"

    def github_status(self):
        return {
            "id": "github",
            "connected": True,
            "enabled": True,
            "account": "alice",
            "credentialSource": "device-oauth-keyring",
            "bindingId": "github:binding-safe-id",
            "scopes": "repo, read:org",
        }


class FakeRuntime:
    def _build_step_context(self, _session, *, next_model_step: bool, step_id: str | None = None):
        _ = next_model_step
        return StepContext.build(
            step_id=step_id or "step-1",
            session_id="thread-1",
            turn_id="turn-1",
            model_step=1,
            workspace_dir=".",
            profile_id="test",
            permission_mode=PermissionMode.APPROVAL,
            tool_router=ToolRegistry(()).router(),
            request_state=RequestStateSnapshot.build(system_prompt="system"),
        )


def test_connector_identity_is_frozen_into_secret_free_step_request_state() -> None:
    runtime = FakeRuntime()
    manager = FakeManager()
    baseline = runtime._build_step_context(None, next_model_step=True).request_state.digest()

    install_connector_step_provenance(manager, runtime)
    step = runtime._build_step_context(None, next_model_step=True, step_id="step-2")

    payload = json.loads(step.request_state.connector_binding_json)
    assert payload == {
        "version": 1,
        "connectors": [
            {
                "connector_id": "github",
                "connected": True,
                "enabled": True,
                "account": "alice",
                "credential_source": "device-oauth-keyring",
                "binding_id": "github:binding-safe-id",
                "scopes": "repo, read:org",
            }
        ],
    }
    assert FakeManager.secret not in step.request_state.connector_binding_json
    assert step.request_state.digest() != baseline


def test_connector_provenance_is_optional_for_status_only_runtime() -> None:
    class StatusOnlyRuntime:
        pass

    runtime = StatusOnlyRuntime()
    install_connector_step_provenance(FakeManager(), runtime)
    assert not hasattr(runtime, "_loom_connector_step_provenance_installed")
