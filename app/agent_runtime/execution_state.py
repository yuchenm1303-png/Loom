"""Pure execution transitions. Task success and presentation are separate."""
from dataclasses import dataclass
from enum import Enum
from app.ai import ModelResponse


class ExecutionAction(str, Enum):
    CANCEL = "cancel"
    WAIT_APPROVAL = "wait_approval"
    EXECUTE_TOOLS = "execute_tools"
    SAMPLE = "sample"
    DELIVER = "deliver"


@dataclass(frozen=True)
class ExecutionDecision:
    action: ExecutionAction
    source: str


def next_execution_action(response: ModelResponse, *, cancelled=False, waiting_approval=False,
                          pending_tools=False, pending_input=False) -> ExecutionDecision:
    if cancelled:
        return ExecutionDecision(ExecutionAction.CANCEL, "cancellation")
    if waiting_approval:
        return ExecutionDecision(ExecutionAction.WAIT_APPROVAL, "approval")
    if pending_tools or response.tool_calls:
        return ExecutionDecision(ExecutionAction.EXECUTE_TOOLS, "tool_calls")
    if pending_input:
        return ExecutionDecision(ExecutionAction.SAMPLE, "pending_input")
    if response.end_turn is False:
        return ExecutionDecision(ExecutionAction.SAMPLE, "provider_end_turn")
    if response.end_turn is True:
        return ExecutionDecision(ExecutionAction.DELIVER, "provider_end_turn")
    # A message phase is presentation metadata, not an end-turn instruction.
    return ExecutionDecision(ExecutionAction.DELIVER, "legacy_finish_compatibility")
