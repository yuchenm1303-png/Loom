from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Sequence

from .browser_runtime_v1 import BrowserRuntime
from .computer_alibaba import AlibabaGUIPlusGroundingBackend, GUI_PLUS_GROUNDER_ALIASES
from .computer_diagnostics import ComputerDiagnostics
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

    The runtime keeps a small per-session observation history so deterministic
    actions can still run when the outer agent performed a harmless observe/status
    call after planning. Different sessions still fail closed, but same-session
    revision drift is handled as a compatibility boundary rather than a hard wall.
    """

    def __init__(
        self,
        operator: ComputerOperator,
        grounder: ComputerGroundingBackend | None = None,
        *,
        trajectory_limit: int = 16,
        revision_history_limit: int = 8,
        settle_delay: float = 0.25,
        diagnostics: ComputerDiagnostics | None = None,
    ) -> None:
        self.operator = operator
        self.grounder = grounder
        self.trajectory_limit = max(1, int(trajectory_limit))
        self.revision_history_limit = max(2, int(revision_history_limit))
        self.settle_delay = max(0.0, float(settle_delay))
        self.diagnostics = diagnostics or ComputerDiagnostics()
        self._lock = threading.RLock()
        self._revision = 0
        self._latest_owner = ""
        self._latest: ComputerObservation | None = None
        self._history: dict[str, deque[ComputerStateSnapshot]] = defaultdict(
            lambda: deque(maxlen=self.revision_history_limit)
        )
        self._trajectory: dict[str, deque[ComputerTrajectoryEntry]] = defaultdict(
            lambda: deque(maxlen=self.trajectory_limit)
        )

    def observe(self, owner_session_id: str) -> ComputerStateSnapshot:
        owner = self._owner(owner_session_id)
        operation_id = self.diagnostics.operation_id()
        started = time.perf_counter()
        with self._lock:
            self.diagnostics.emit("observe.started", operation_id=operation_id, owner=owner)
            try:
                observation = self.operator.observe()
                snapshot = self._publish(owner, observation)
                self._log_observation(operation_id, "observed", snapshot, started)
                return snapshot
            except Exception as exc:
                self._log_failure("observe.failed", operation_id, started, exc)
                raise

    def latest(self, owner_session_id: str) -> ComputerStateSnapshot:
        owner = self._owner(owner_session_id)
        with self._lock:
            if self._latest is None or self._latest_owner != owner:
                raise RuntimeError("no current Computer Use observation for this Loom session")
            return ComputerStateSnapshot(self._revision, self._latest)

    def ensure_revision(self, owner_session_id: str, expected_revision: int) -> ComputerStateSnapshot:
        owner = self._owner(owner_session_id)
        with self._lock:
            revision = int(expected_revision)
            if self._latest is not None and self._latest_owner == owner and revision == self._revision:
                return ComputerStateSnapshot(self._revision, self._latest)

            for snapshot in reversed(tuple(self._history.get(owner, ()))):
                if snapshot.state_revision == revision:
                    self.diagnostics.emit(
                        "revision.history_reused",
                        owner=owner,
                        expected_revision=revision,
                        current_revision=self._revision,
                        image_sha256=snapshot.observation.image_sha256,
                    )
                    return snapshot

            if self._latest is not None and self._latest_owner == owner:
                fallback = ComputerStateSnapshot(self._revision, self._latest)
                self.diagnostics.emit(
                    "revision.fallback_latest",
                    owner=owner,
                    expected_revision=revision,
                    current_revision=self._revision,
                    image_sha256=fallback.observation.image_sha256,
                )
                return fallback

            raise RuntimeError(
                f"stale computer state_revision {revision}; refresh computer_observe before acting"
            )

    def execute(
        self,
        owner_session_id: str,
        expected_revision: int,
        action: ComputerAction,
    ) -> ComputerStepOutcome:
        owner = self._owner(owner_session_id)
        operation_id = self.diagnostics.operation_id()
        started = time.perf_counter()
        with self._lock:
            self.diagnostics.emit(
                "action.started",
                operation_id=operation_id,
                owner=owner,
                expected_revision=expected_revision,
                action=action.safe_dict(),
            )
            try:
                before = self.ensure_revision(owner, expected_revision)
                outcome = self._execute_locked(owner, before, action)
                if before.state_revision != int(expected_revision):
                    outcome.verification["revision_autofixed"] = True
                    outcome.verification["requested_revision"] = int(expected_revision)
                    outcome.verification["used_revision"] = before.state_revision
                self.diagnostics.emit(
                    "action.completed",
                    operation_id=operation_id,
                    duration_ms=_elapsed_ms(started),
                    outcome=outcome.to_safe_dict(),
                )
                self._log_observation(operation_id, "after", outcome.after, started)
                return outcome
            except Exception as exc:
                self._log_failure("action.failed", operation_id, started, exc)
                raise

    def _execute_locked(
        self,
        owner: str,
        before: ComputerStateSnapshot,
        action: ComputerAction,
    ) -> ComputerStepOutcome:
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
        operation_id = self.diagnostics.operation_id()
        started = time.perf_counter()
        with self._lock:
            self.diagnostics.emit(
                "step.started",
                operation_id=operation_id,
                owner=owner,
                instruction=(instruction if self.diagnostics.raw else {"length": len(instruction)}),
            )
            try:
                return self._step_locked(owner, instruction, operation_id, started)
            except Exception as exc:
                self._log_failure("step.failed", operation_id, started, exc)
                raise

    def _step_locked(
        self,
        owner: str,
        instruction: str,
        operation_id: str,
        started: float,
    ) -> ComputerStepOutcome:
        before = self._publish(owner, self.operator.observe())
        self._log_observation(operation_id, "before", before, started)
        trajectory: Sequence[ComputerTrajectoryEntry] = tuple(self._trajectory[owner])
        grounder_started = time.perf_counter()
        with self.diagnostics.bind(operation_id):
            prediction = self.grounder.predict(instruction, before.observation, trajectory)
        self.diagnostics.emit(
            "grounding.completed",
            operation_id=operation_id,
            duration_ms=_elapsed_ms(grounder_started),
            action=prediction.action.safe_dict(),
            thought=(prediction.thought if self.diagnostics.raw else {"length": len(prediction.thought)}),
        )
        action = self._promote_click_to_uia(prediction.action, before.observation)
        if action != prediction.action:
            prediction = ComputerPrediction(action=action, thought=prediction.thought)

        if action.type not in {ComputerActionType.FINISH, ComputerActionType.CALL_USER} and self._is_stuck(owner, before, action):
            outcome = self._soft_replan_outcome(owner, instruction, before, action, prediction.thought)
            self.diagnostics.emit(
                "step.replan_requested",
                operation_id=operation_id,
                duration_ms=_elapsed_ms(started),
                repeated_action=action.safe_dict(),
                outcome=outcome.to_safe_dict(),
            )
            self.diagnostics.emit(
                "step.completed",
                operation_id=operation_id,
                duration_ms=_elapsed_ms(started),
                outcome=outcome.to_safe_dict(),
            )
            return outcome

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
            outcome = ComputerStepOutcome(before, prediction, None, before, verification)
            self.diagnostics.emit(
                "step.completed",
                operation_id=operation_id,
                duration_ms=_elapsed_ms(started),
                outcome=outcome.to_safe_dict(),
            )
            return outcome

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
        outcome = ComputerStepOutcome(before, prediction, execution, after, verification)
        self._log_observation(operation_id, "after", after, started)
        self.diagnostics.emit(
            "step.completed",
            operation_id=operation_id,
            duration_ms=_elapsed_ms(started),
            outcome=outcome.to_safe_dict(),
        )
        return outcome

    def _soft_replan_outcome(
        self,
        owner: str,
        instruction: str,
        before: ComputerStateSnapshot,
        repeated_action: ComputerAction,
        thought: str,
    ) -> ComputerStepOutcome:
        action = ComputerAction(type=ComputerActionType.WAIT, duration_ms=500)
        prediction = ComputerPrediction(
            action=action,
            thought=(
                thought
                or "The same GUI action was suggested repeatedly on an unchanged screenshot; pause and let the outer agent re-plan."
            ),
        )
        verification = {
            "method": "soft-stuck-replan",
            "execution_ok": True,
            "visual_changed": False,
            "active_window_changed": False,
            "stuck_detected": True,
            "repeated_action": repeated_action.safe_dict(),
            "replan_hint": "observe the current screen and choose a different target or strategy",
            "before_image_sha256": before.observation.image_sha256,
            "after_image_sha256": before.observation.image_sha256,
        }
        self._trajectory[owner].append(
            ComputerTrajectoryEntry(
                instruction=instruction,
                observation_id=before.observation.observation_id,
                image_sha256=before.observation.image_sha256,
                action=action,
                execution_ok=False,
            )
        )
        return ComputerStepOutcome(before, prediction, None, before, verification)

    def _log_observation(self, operation_id: str, phase: str, snapshot: ComputerStateSnapshot | None, started: float) -> None:
        if snapshot is None:
            return
        observation = snapshot.observation
        screenshot_path = self.diagnostics.save_screenshot(observation, operation_id=operation_id, phase=phase)
        payload = observation.to_safe_dict(
            control_limit=(len(observation.controls) if self.diagnostics.raw else 0),
            redactor=redact_secrets,
        )
        self.diagnostics.emit(
            "observation.captured",
            operation_id=operation_id,
            phase=phase,
            duration_ms=_elapsed_ms(started),
            state_revision=snapshot.state_revision,
            screenshot_path=screenshot_path,
            observation=payload,
        )

    def _log_failure(self, event: str, operation_id: str, started: float, exc: Exception) -> None:
        self.diagnostics.emit(
            event,
            operation_id=operation_id,
            duration_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
            error=redact_secrets(str(exc)),
        )

    @staticmethod
    def _promote_click_to_uia(action: ComputerAction, observation: ComputerObservation) -> ComputerAction:
        """Promote a visual single-click to the most specific enabled UIA target.

        Visual grounding backends intentionally predict frame-local coordinates so
        they remain replaceable. When that point lands inside a UIA control from
        the same observation, Loom can safely attach the semantic control id and
        let the Windows operator try Invoke before physical input. Double/right
        clicks keep their physical semantics and are not promoted.
        """

        if action.type is not ComputerActionType.CLICK or action.control_id or action.point is None:
            return action
        screen_x, screen_y = observation.frame.to_screen(action.point)
        matches = [
            control
            for control in observation.controls
            if control.enabled
            and control.rect.left <= screen_x < control.rect.right
            and control.rect.top <= screen_y < control.rect.bottom
        ]
        if not matches:
            return action
        target = min(
            matches,
            key=lambda control: (control.rect.width * control.rect.height, control.control_id),
        )
        return replace(action, control_id=target.control_id)

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
            self._history.pop(owner, None)
            if self._latest_owner == owner:
                self._latest_owner = ""
                self._latest = None
                self._revision += 1

    def close(self) -> None:
        with self._lock:
            self._trajectory.clear()
            self._history.clear()
            self._latest_owner = ""
            self._latest = None
            self._revision += 1
            self.operator.close()

    def _publish(self, owner: str, observation: ComputerObservation) -> ComputerStateSnapshot:
        self._revision += 1
        self._latest_owner = owner
        self._latest = observation
        snapshot = ComputerStateSnapshot(self._revision, observation)
        self._history[owner].append(snapshot)
        return snapshot

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
        computer_grounder_kind: str | None = None,
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
        requested_grounder = str(
            computer_grounder_kind
            or os.environ.get("LOOM_COMPUTER_GROUNDER")
            or ""
        ).strip().casefold()
        grounder = computer_grounder
        selected_grounder = str(getattr(grounder, "name", "") or "").strip().casefold()

        if grounder is None and operator is not None:
            has_alibaba_secret = bool(
                str(os.environ.get("LOOM_COMPUTER_API_KEY") or "").strip()
                or str(os.environ.get("DASHSCOPE_API_KEY") or "").strip()
            )
            wants_alibaba = requested_grounder in GUI_PLUS_GROUNDER_ALIASES or (
                not requested_grounder and not profile and has_alibaba_secret
            )
            if wants_alibaba:
                grounder = AlibabaGUIPlusGroundingBackend.from_environment()
                if grounder is None:
                    raise RuntimeError(
                        "Alibaba GUI-Plus grounding requires LOOM_COMPUTER_API_KEY or DASHSCOPE_API_KEY"
                    )
                selected_grounder = grounder.name
            elif profile:
                if requested_grounder and requested_grounder not in {"ui-tars", "uitars"}:
                    raise ValueError(f"unsupported Computer Use grounder: {requested_grounder}")
                grounder = UITarsGroundingBackend(self.platform, profile)
                selected_grounder = grounder.name
            elif requested_grounder:
                if requested_grounder in {"ui-tars", "uitars"}:
                    raise RuntimeError("UI-TARS grounding requires LOOM_COMPUTER_MODEL_PROFILE")
                raise ValueError(f"unsupported Computer Use grounder: {requested_grounder}")

        self.computer_backend_name = backend_name
        self.computer_model_profile = profile
        self.computer_grounder_name = str(getattr(grounder, "name", "") or "disabled")
        self.computer_grounder_kind = selected_grounder or requested_grounder or "disabled"
        diagnostics = ComputerDiagnostics()
        if isinstance(grounder, AlibabaGUIPlusGroundingBackend):
            grounder.diagnostics = diagnostics
        self.computer_diagnostics = diagnostics
        self.computer_sessions = (
            ComputerSessionStore(
                operator,
                grounder,
                settle_delay=computer_settle_delay,
                diagnostics=diagnostics,
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
        grounder_config: dict[str, object] = {}
        if store is not None and store.grounder is not None:
            safe_config = getattr(store.grounder, "safe_config", None)
            if callable(safe_config):
                grounder_config = dict(safe_config())
                grounder_config.pop("base_url", None)
        return {
            "enabled": store is not None,
            "operator": self.computer_backend_name,
            "grounder": self.computer_grounder_name,
            "grounder_kind": self.computer_grounder_kind,
            "grounder_config": grounder_config,
            "model_profile": self.computer_model_profile,
            "policy_step_enabled": bool(store is not None and store.grounder is not None),
            "state_persistence": "ephemeral with short same-session revision history",
            "screenshot_persistence": "none unless computer_observe save_screenshot=true",
            "typed_text_persistence": "transient_only for model-produced tool calls",
            "credential_persistence": "runtime environment only; not stored in Loom session state",
            "observation_mode": "screenshot + UIA hybrid when Windows backend is enabled",
            "verification": "post-action re-observation; deterministic foreground-window check for switch_window",
            "diagnostics": {"mode": self.computer_diagnostics.mode, "log_dir": str(self.computer_diagnostics.root)},
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


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
