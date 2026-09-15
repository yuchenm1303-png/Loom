from __future__ import annotations

"""Secret-free connector provenance captured at the semantic Step boundary.

Execution authority still lives in the Step-scoped AgentTool handler closures.
This projection exists for integrity diagnostics and auditability: it records
which Loom connector identity was sampled without inventing upstream Codex
plugin/link identifiers or persisting credential material.
"""

import json
from dataclasses import replace
from typing import Any


def _binding_snapshot(manager: Any) -> dict[str, object]:
    status = manager.github_status()
    return {
        "version": 1,
        "connectors": [
            {
                "connector_id": "github",
                "connected": bool(status.get("connected")),
                "enabled": bool(status.get("enabled", True)),
                "account": str(status.get("account") or ""),
                "credential_source": str(status.get("credentialSource") or ""),
                "binding_id": str(status.get("bindingId") or "github:disconnected"),
                "scopes": str(status.get("scopes") or ""),
            }
        ],
    }


def install_connector_step_provenance(manager: Any, runtime: Any) -> None:
    if getattr(runtime, "_loom_connector_step_provenance_installed", False):
        return
    original = runtime._build_step_context

    def build_step_context(session: Any, *, next_model_step: bool, step_id: str | None = None):
        # `original` already includes ConnectorManager's pre-Step refresh hook.
        # Capture provenance *after* that refresh so the metadata describes the
        # same binding whose AgentTool handlers were sampled into this Step.
        step = original(session, next_model_step=next_model_step, step_id=step_id)
        snapshot = _binding_snapshot(manager)
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return replace(
            step,
            request_state=replace(
                step.request_state,
                connector_binding_json=encoded,
            ),
        )

    runtime._build_step_context = build_step_context
    runtime._loom_connector_step_provenance_installed = True


__all__ = ["install_connector_step_provenance"]
