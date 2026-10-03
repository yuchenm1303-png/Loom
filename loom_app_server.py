from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TextIO


def _sanitize_desktop_bridge_environment(*, fallback_home: Path | None = None) -> None:
    """Keep Desktop browser pairing data out of Loom's global runtime environment.

    The Electron desktop briefly has to hand the App Server the per-install
    Current Tab Bridge credential.  That credential must not remain in the
    environment inherited by model-run shell commands, and Electron's userData
    directory must not replace Loom's long-standing ``~/.loom`` runtime root.

    Mirror the pairing token into the normal browser credential file, then drop
    both desktop-only environment variables before importing the runtime stack.
    """

    if not str(os.environ.get("LOOM_DESKTOP_PYTHON") or "").strip():
        return
    token = str(os.environ.pop("LOOM_BROWSER_EXTENSION_TOKEN", "") or "").strip()
    # PR #149 used LOOM_HOME only to make the browser token path line up with
    # Electron userData.  Keeping it would relocate settings, sessions, memory,
    # connector metadata, and every other FileAgentSessionStore consumer.
    os.environ.pop("LOOM_HOME", None)
    if not token:
        return

    root = Path(fallback_home).expanduser().resolve() if fallback_home is not None else (Path.home() / ".loom").resolve()
    target = root / "browser" / "current-tab-bridge.token"
    target.parent.mkdir(parents=True, exist_ok=True)
    current = ""
    try:
        current = target.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if current == token:
        return
    temporary = target.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(token, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)


# This has to happen before app.agent_runtime / loom_cli are imported: those
# modules construct runtime paths and later spawn model-controlled processes.
_sanitize_desktop_bridge_environment()

from app.agent_runtime import PermissionMode
from app.ai import ReasoningKind, ReasoningRequest
from app.ai.reasoning_catalog import reasoning_capability
from app.app_server_browser_policy import serve_browser_policy_managed_streaming_stdio
from loom_cli import _build_runtime, _resolve_new_permission_mode


def _reconfigure_utf8(stream: TextIO, *, errors: str) -> None:
    """Force the stdio protocol stream to UTF-8 regardless of Windows locale."""

    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors=errors)


def _configure_protocol_stdio() -> None:
    """Make the JSONL wire encoding deterministic on every host.

    Python normally inherits the Windows active code page for redirected stdio.
    Loom Desktop always speaks UTF-8 JSON-RPC, so a Chinese/GBK locale could
    otherwise corrupt both incoming prompts and outgoing assistant text before
    Qt ever sees them.
    """

    _reconfigure_utf8(sys.stdin, errors="strict")
    _reconfigure_utf8(sys.stdout, errors="strict")
    # stderr is diagnostic rather than protocol data; preserving the process is
    # more useful than failing on an unencodable diagnostic character.
    _reconfigure_utf8(sys.stderr, errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom local app-server (stdio JSON-RPC)")
    parser.add_argument("--provider", choices=["openai", "openai-compatible", "opencode-go"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--selection")
    parser.add_argument("--reasoning-kind", choices=[kind.value for kind in ReasoningKind])
    parser.add_argument("--reasoning-value")
    parser.add_argument("--home", help="runtime state root; defaults to ~/.loom")
    parser.add_argument("--workspace", help="default workspace for new threads")
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        help="default permission mode for new threads",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--local-ipc",
        action="store_true",
        help="publish an authenticated loopback endpoint for trusted local remote clients",
    )
    parser.add_argument(
        "--vision",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="declare that the selected model can read attached images",
    )
    return parser


def _validate_reasoning_for_runtime(
    *,
    model: str,
    provider: str,
    base_url: str,
    reasoning: ReasoningRequest | None,
) -> dict[str, object] | None:
    capability = reasoning_capability(
        model=model,
        adapter=provider,
        base_url=base_url,
    )
    if reasoning is None:
        return capability
    if capability is None:
        raise SystemExit(f"Model {model!r} does not advertise a supported reasoning control")
    if reasoning.kind.value != str(capability.get("kind") or ""):
        raise SystemExit(
            f"Reasoning kind {reasoning.kind.value!r} is not supported by model {model!r}"
        )
    supported = {
        str(option.get("value") or "")
        for option in capability.get("options") or []
        if isinstance(option, dict)
    }
    if reasoning.value not in supported:
        raise SystemExit(
            f"Reasoning value {reasoning.value!r} is not supported by model {model!r}"
        )
    return capability


def main(argv: list[str] | None = None) -> int:
    # JSON-RPC v1 explicitly uses UTF-8 JSONL. Configure this before parsing or
    # runtime startup so locale-dependent redirected stdio cannot enter the
    # protocol path on Windows.
    _configure_protocol_stdio()

    args = build_parser().parse_args(argv)
    workspace = Path(args.workspace or Path.cwd()).expanduser().resolve()
    if not workspace.exists():
        raise SystemExit(f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")

    # stdout is reserved exclusively for JSON-RPC protocol frames. Runtime
    # construction is intentionally reused from the CLI so credentials and
    # provider configuration never enter client-visible protocol state.
    runtime, store, model = _build_runtime(args)
    reasoning = ReasoningRequest.from_values(args.reasoning_kind, args.reasoning_value)
    capability = _validate_reasoning_for_runtime(
        model=model,
        provider=str(args.provider or ""),
        base_url=str(args.base_url or ""),
        reasoning=reasoning,
    )
    runtime.reasoning = reasoning
    runtime.reasoning_capability = capability
    permission_mode = _resolve_new_permission_mode(args)
    return serve_browser_policy_managed_streaming_stdio(
        runtime=runtime,
        store=store,
        model=model,
        default_model_selection=str(args.selection or ""),
        default_model_provider=str(args.provider or ""),
        default_model_base_url=str(args.base_url or ""),
        default_workspace=workspace,
        default_permission_mode=permission_mode,
        vision=bool(args.vision),
        local_ipc=bool(args.local_ipc),
    )


if __name__ == "__main__":
    raise SystemExit(main())
