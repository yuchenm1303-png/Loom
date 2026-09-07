from __future__ import annotations

from dataclasses import dataclass

from app.ai import ToolCall

from .contracts import PermissionMode
from .permissions import PermissionDecision, PermissionEngine
from .step import StepContext
from .tools import AgentTool, ToolPolicy, validate_tool_arguments


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call: ToolCall
    tool: AgentTool
    decision: PermissionDecision
    reason: str


class ToolOrchestrator:
    """Central preparation boundary for every model-originated tool call."""

    def __init__(self, *, permission_engine: PermissionEngine | None = None) -> None:
        self.permission_engine = permission_engine or PermissionEngine()

    def evaluate_tool(
        self,
        step: StepContext,
        tool: AgentTool,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> tuple[PermissionDecision, str]:
        """Return the exact authorization decision used by real execution.

        The frozen StepContext permission snapshot is authoritative. Capability
        grounding and execution deliberately share this path so model-facing
        claims cannot drift from the actual permission boundary.
        """

        evaluation = self.permission_engine.evaluate(
            effect=tool.effect,
            snapshot=step.permissions,
        )
        decision = evaluation.decision
        reason = evaluation.reason

        if (
            step.permissions.mode is PermissionMode.APPROVAL
            and legacy_policy is not None
        ):
            decision = (
                PermissionDecision.APPROVAL
                if legacy_policy.requires_approval(tool)
                else PermissionDecision.ALLOW
            )
            reason = (
                f"Compatibility approval policy requires approval for {tool.effect.value}."
                if decision is PermissionDecision.APPROVAL
                else f"Compatibility approval policy auto-approves {tool.effect.value}."
            )
        return decision, reason

    def capability_contract(
        self,
        step: StepContext,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> str:
        """Build a compact, authoritative capability contract for one model step."""

        grouped: dict[PermissionDecision, list[str]] = {
            PermissionDecision.ALLOW: [],
            PermissionDecision.APPROVAL: [],
            PermissionDecision.DENY: [],
        }
        decisions_by_name: dict[str, PermissionDecision] = {}
        for tool in step.tool_router.all():
            decision, _ = self.evaluate_tool(step, tool, legacy_policy=legacy_policy)
            grouped[decision].append(f"{tool.name}[{tool.effect.value}]")
            decisions_by_name[tool.name] = decision

        def names(decision: PermissionDecision) -> str:
            values = grouped[decision]
            return ", ".join(values) if values else "(none)"

        sandbox = step.world_state.sandbox
        if sandbox is None:
            sandbox_line = "sandbox=not-reported"
        else:
            sandbox_line = (
                f"sandbox={sandbox.backend.value}:{sandbox.mode.value}; "
                f"enforced={str(sandbox.enforced).lower()}; scope=process-execution"
            )

        intent_routes: list[str] = []
        exec_decision = decisions_by_name.get("exec")
        if exec_decision is not None:
            intent_routes.append(
                f"host_system_read=exec:{exec_decision.value}; "
                "use for CLI-queryable host/system facts such as memory, CPU, disk, processes, and network status"
            )
        computer_status_decision = decisions_by_name.get("computer_status")
        if computer_status_decision is not None:
            intent_routes.append(
                f"windows_gui_status=computer_status:{computer_status_decision.value}; "
                "scope=GUI Computer Use only; it does not report exec/browser/filesystem availability"
            )
        if not intent_routes:
            intent_routes.append("(no core intent routes exposed in this step)")

        return "\n".join(
            (
                "<loom_capability_contract>",
                "This block is generated from the current Loom runtime state and is authoritative for capability claims.",
                f"permission_mode={step.permissions.mode.value}",
                f"filesystem_access={step.permissions.file_system_access.value}",
                sandbox_line,
                f"allow={names(PermissionDecision.ALLOW)}",
                f"approval={names(PermissionDecision.APPROVAL)}",
                f"deny={names(PermissionDecision.DENY)}",
                "Intent routes:",
                *intent_routes,
                "Rules:",
                "1. Tool names are not a capability ontology. Match the user's intent to tool semantics and schemas; a general-purpose tool may satisfy a request even when no specialist tool has a matching name.",
                "2. Authorization is not capability. A tool listed under approval is available to attempt; call it when appropriate and let the harness request user approval. A tool under deny is blocked by the current permission mode, not evidence that Loom never supports that capability.",
                "3. Before claiming that Loom cannot access, inspect, control, read, write, browse, execute, or observe something, check all suitable exposed tools. If tool_search is available, use it to discover deferred tools before declaring that no matching capability exists.",
                "4. Do not infer host, OS, network, filesystem, process, browser, or GUI inaccessibility merely because access is tool-mediated or because sandboxing exists. Sandbox state describes process-execution isolation only unless a tool result explicitly states a broader limitation.",
                "5. Disambiguate user vocabulary from Loom subsystem names by context. Do not map a user noun to a same-named internal subsystem unless the request actually refers to that subsystem.",
                "6. Say a capability is unavailable only from runtime evidence: no suitable exposed/deferred tool exists, a relevant status/tool result reports it unavailable, or permission/runtime policy explicitly blocks it with no usable alternative. Distinguish unavailable, approval-required, denied, and execution-failed.",
                "7. For read-only host/system facts that an OS command can query (for example RAM, CPU, disk, process, or network status), use exec when it is allowed or approval-required. Computer Use, screen capture, and GUI input are not prerequisites for CLI-queryable system facts.",
                "8. A computer_status result is scoped only to the Windows GUI Computer Use subsystem. If it reports disabled or unavailable, do not infer that exec, browser, filesystem, MCP, or other independent tools are disabled; evaluate each subsystem separately.",
                "9. Do not ask the user in prose to pre-authorize an approval-required tool. Issue the appropriate tool call and let Loom's approval mechanism request consent. Ask the user only when required tool input is genuinely missing or a tool explicitly requests user assistance.",
                "10. When the user asks broadly whether Loom can control or inspect their computer, answer by capability surface (for example GUI, shell/processes, filesystem, browser) from current runtime evidence rather than collapsing all surfaces into one yes/no based on computer_status.",
                "</loom_capability_contract>",
            )
        )

    def prepare(
        self,
        step: StepContext,
        call: ToolCall,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> PreparedToolCall:
        tool = step.tool_router.get(call.name)
        if tool is None:
            raise ValueError(f"Unknown or unavailable tool: {call.name}")
        validate_tool_arguments(tool.input_schema, call.arguments)

        decision, reason = self.evaluate_tool(
            step,
            tool,
            legacy_policy=legacy_policy,
        )

        return PreparedToolCall(
            call=call,
            tool=tool,
            decision=decision,
            reason=reason,
        )


__all__ = ["PreparedToolCall", "ToolOrchestrator"]
