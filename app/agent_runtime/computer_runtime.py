from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Sequence

from .browser_runtime_v1 import BrowserRuntime
from .computer_grounding import ComputerGroundingBackend, UITarsGroundingBackend
from .computer_transient import ComputerTransientInputPlatform
from .computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerExecution,
    ComputerObservation,
    ComputerPrediction,
    ComputerTrajectoryEntry,
)
from .computer_windows import ComputerOperator, PyWinAutoWindowsOperator, windows_computer_available
from .memory_store import redact_secrets


@dataclass(frozen=True, slots=True)
class ComputerStateSnapshot:
    state_revision: int
    observation: ComputerObservation

    def to_safe_dict(self, *, control_limit: int = 80) -> dict[str, object]:
        return {
            "state_revision": self.state_revision,
            **self.observation.to_safe_dict(control_limit=control_limit, redactor=redact_secrets),
        }


@dataclass(frozen=True, slots=True)
class ComputerStepOutcome:
    before: ComputerStateSnapshot
    prediction: ComputerPrediction
    execution: ComputerExecution | None
    after: ComputerStateSnapshot | None
    verification: dict[str, object]

    def to_safe_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "before_revision": self.before.state_revision,
            "action": self.prediction.action.safe_dict(),
            "terminal": self.prediction.action.type
            in {ComputerActionType.FINISH, ComputerActionType.CALL_USER},
            "verification": dict(self.verification),
        }
        if self.execution is not None:
            payload["execution"] = self.execution.to_safe_dict()
        if self.after is not None:
            payload["after"] = self.after.to_safe_dict(control_limit=40)
        return payload


class ComputerSessionStore:
    """Ephemeral state owner for Loom's one physical desktop environment.

    A global revision makes stale GUI targets fail closed when another Loom session
    observes or mutates the desktop between steps. Screenshots, UIA wrappers,
    trajectory instructions and typed values remain process-local only.
    """

    def __init__(
        self,
        operator: ComputerOperator,
        grounder: ComputerGroundingBackend | None = None,
        *,
        trajectory_limit: int = 16,
        settle_delay: float = 0.25,
    ) -> None:
        self.operator = operator
        self.grounder = grounder
        self.trajectory_limit = max(1, int(trajectory_limit))
        self.settle_delay = max(0.0, float(settle_delay))
        self._lock = threading.RLock()
        self._revision = 0
        self._latest_owner = ""
        self._latest: ComputerObservation | None = None
        self._trajectory: dict[str, deque[ComputerTrajectoryEntry]] = defaultdict(
            lambda: deque(maxlen=self.trajectory_limit)
        )

    def observe(self, owner_session_id: str) -> ComputerStateSnapshot:
        owner = self._owner(owner_session_id)
        with self._lock:
            observation = self.operator.observe()
            return self._publish(owner, observation)

    def latest(self, owner_session_id: str) -> ComputerStateSnapshot:
        owner = self._owner(owner_session_id)
        with self._lock:
            if self._latest is None or self._latest_owner != owner:
                raise RuntimeError("no current Computer Use observation for this Loom session")
            return ComputerStateSnapshot(self._revision, self._latest)

    def ensure_revision(self, owner_session_id: str, expected_revision: int) -> ComputerStateSnapshot:
        owner = self._owner(owner_session_id)
        with self._lock:
            if self._latest is None or self._latest_owner != owner or int(expected_revision) != self._revision:
                raise RuntimeError(
                    f"stale computer state_revision {int(expected_revision)}; refresh computer_observe before acting"
                )
            return ComputerStateSnapshot(self._revision, self._latest)

    def execute(
        self,
        owner_session_id: str,
        expected_revision: int,
        action: ComputerAction,
    ) -> ComputerStepOutcome:
        owner = self._owner(owner_session_id)
        with self._lock:
            before = self.ensure_revision(owner, expected_revision)
            execution = self.operator.execute(action, before.observation)
            if self.settle_delay and action.type not in {
                ComputerActionType.WAIT,
                ComputerActionType.FINISH,
                ComputerActionType.CALL_USER,
            }:
                time.sleep(self.settle_delay)
            if action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER}:
                after = before
            else:
                after = self._publish(owner, self.operator.observe())
            verification = self._verify(before, after, action, execution)
            self._trajectory[owner].append(
                ComputerTrajectoryEntry(
                    instruction="direct action",
                    observation_id=before.observation.observation_id,
                    image_sha256=before.observation.image_sha256,
                    action=action,
                    execution_ok=bool(execution.ok),
                )
            )
            return ComputerStepOutcome(before, ComputerPrediction(action=action), execution, after, verification)

    def step(self, owner_session_id: str, instruction: str) -> ComputerStepOutcome:
        owner = self._owner(owner_session_id)
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("computer_step instruction must not be empty")
        if self.grounder is None:
            raise RuntimeError("Computer Use visual grounding backend is not configured")
        with self._lock:
            before = self._publish(owner, self.operator.observe())
            trajectory: Sequence[ComputerTrajectoryEntry] = tuple(self._trajectory[owner])
            prediction = self.grounder.predict(instruction, before.observation, trajectory)
            action = prediction.action
            if action.type not in {ComputerActionType.FINISH, ComputerActionType.CALL_USER} and self._is_stuck(owner, before, action):
                raise RuntimeError(
                    "Computer Use stuck detection blocked a third identical action on an unchanged screenshot; "
                    "re-plan or use computer_observe/computer_action with a different target"
                )
            if action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER}:
                verification = {
                    "method": "policy-terminal",
                    "execution_ok": True,
                    "visual_changed": False,
                    "active_window_changed": False,
                }
                self._trajectory[owner].append(
                    ComputerTrajectoryEntry(
                        instruction=instruction,
                        observation_id=before.observation.observation_id,
                        image_sha256=before.observation.image_sha256,
                        action=action,
                        execution_ok=True,
                    )
                )
                return ComputerStepOutcome(before, prediction, None, before, verification)

            execution = self.operator.execute(action, before.observation)
            if self.settle_delay and action.type is not ComputerActionType.WAIT:
                time.sleep(self.settle_delay)
            after = self._publish(owner, self.operator.observe())
            verification = self._verify(before, after, action, execution)
            self._trajectory[owner].append(
                ComputerTrajectoryEntry(
                    instruction=instruction,
                    observation_id=before.observation.observation_id,
                    image_sha256=before.observation.image_sha256,
                    action=action,
                    execution_ok=bool(execution.ok),
                )
            )
            return ComputerStepOutcome(before, prediction, execution, after, verification)

    def _is_stuck(self, owner: str, before: ComputerStateSnapshot, action: ComputerAction) -> bool:
        history = tuple(self._trajectory[owner])
        if len(history) < 2:
            return False
        recent = history[-2:]
        return all(
            item.execution_ok
            and item.image_sha256 == before.observation.image_sha256
            and item.action == action
            for item in recent
        )

    def clear_owner(self, owner_session_id: str) -> None:
        owner = self._owner(owner_session_id)
        with self._lock:
            self._trajectory.pop(owner, None)
            if self._latest_owner == owner:
                self._latest_owner = ""
                self._latest = None
                self._revision += 1

    def close(self) -> None:
        with self._lock:
            self._trajectory.clear()
            self._latest_owner = ""
            self._latest = None
            self._revision += 1
            self.operator.close()

    def _publish(self, owner: str, observation: ComputerObservation) -> ComputerStateSnapshot:
        self._revision += 1
        self._latest_owner = owner
        self._latest = observation
        return ComputerStateSnapshot(self._revision, observation)

    @staticmethod
    def _verify(
        before: ComputerStateSnapshot,
        after: ComputerStateSnapshot,
        action: ComputerAction,
        execution: ComputerExecution,
    ) -> dict[str, object]:
        before_window = before.observation.active_window.window_id if before.observation.active_window else ""
        after_window = after.observation.active_window.window_id if after.observation.active_window else ""
        visual_changed = before.observation.image_sha256 != after.observation.image_sha256
        active_window_changed = before_window != after_window
        method = "post-action-observation"
        target_confirmed = None
        if action.type is ComputerActionType.SWITCH_WINDOW:
            target_confirmed = after_window == action.window_id
            method = "foreground-window-id"
        return {
            "method": method,
            "execution_ok": bool(execution.ok),
            "visual_changed": visual_changed,
            "active_window_changed": active_window_changed,
            "target_confirmed": target_confirmed,
            "before_image_sha256": before.observation.image_sha256,
            "after_image_sha256": after.observation.image_sha256,
        }

    @staticmethod
    def _owner(value: str) -> str:
        owner = str(value or "").strip()
        if not owner:
            raise ValueError("Computer Use owner session id must not be empty")
        return owner


class ComputerUseRuntime(BrowserRuntime):
    """Runtime v2 Computer Use layer between Browser and MCP.

    Loom remains the only outer agent loop. `computer_step` delegates exactly one
    visual policy decision to a grounding backend, executes at most one OS action,
    re-observes the desktop, and returns bounded verification data to the canonical
    Runtime tool loop.
    """

    def __init__(
        self,
        *args,
        computer_operator: ComputerOperator | None = None,
        computer_grounder: ComputerGroundingBackend | None = None,
        computer_model_profile: str | None = None,
        auto_configure_computer: bool = True,
        computer_settle_delay: float = 0.25,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.platform = ComputerTransientInputPlatform(self.platform)

        operator = computer_operator
        backend_name = str(getattr(operator, "name", "custom") or "custom") if operator is not None else "disabled"
        if operator is None and auto_configure_computer and windows_computer_available():
            operator = PyWinAutoWindowsOperator()
            backend_name = operator.name

        profile = str(
            computer_model_profile
            or os.environ.get("LOOM_COMPUTER_MODEL_PROFILE")
            or ""
        ).strip().casefold()
        grounder = computer_grounder
        if grounder is None and profile:
            grounder = UITarsGroundingBackend(self.platform, profile)

        self.computer_backend_name = backend_name
        self.computer_model_profile = profile
        self.computer_grounder_name = str(getattr(grounder, "name", "") or "disabled")
        self.computer_sessions = (
            ComputerSessionStore(
                operator,
                grounder,
                settle_delay=computer_settle_delay,
            )
            if operator is not None
            else None
        )

        from .computer_tools import computer_tools

        for tool in computer_tools(self):
            if self.tools.get(tool.name) is None:
                self.tools.register(tool)

    def computer_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        store = self.computer_sessions
        operator_status = dict(store.operator.status()) if store is not None else {}
        return {
            "enabled": store is not None,
            "operator": self.computer_backend_name,
            "grounder": self.computer_grounder_name,
            "model_profile": self.computer_model_profile,
            "policy_step_enabled": bool(store is not None and store.grounder is not None),
            "state_persistence": "ephemeral",
            "screenshot_persistence": "none unless computer_observe save_screenshot=true",
            "typed_text_persistence": "transient_only for model-produced tool calls",
            "observation_mode": "screenshot + UIA hybrid when Windows backend is enabled",
            "verification": "post-action re-observation; deterministic foreground-window check for switch_window",
            **operator_status,
        }

    def consume_computer_transient(self, value: str) -> str:
        consumer = getattr(self.platform, "consume", None)
        if not callable(consumer):
            raise RuntimeError("computer transient input boundary is unavailable")
        return str(consumer(value))

    def set_permission_mode(self, session_id, mode):
        current = self.get_session(session_id)
        if self.computer_sessions is not None and str(current.permission_mode.value) != str(getattr(mode, "value", mode)):
            self.computer_sessions.clear_owner(session_id)
        return super().set_permission_mode(session_id, mode)

    def recover_interrupted(self, session_id):
        if self.computer_sessions is not None:
            self.computer_sessions.clear_owner(session_id)
        return super().recover_interrupted(session_id)

    def close(self) -> None:
        clearer = getattr(self.platform, "clear", None)
        if callable(clearer):
            clearer()
        if self.computer_sessions is not None:
            self.computer_sessions.close()
        super().close()


__all__ = [
    "ComputerSessionStore",
    "ComputerStateSnapshot",
    "ComputerStepOutcome",
    "ComputerUseRuntime",
]
