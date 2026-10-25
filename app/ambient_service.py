"""Adapter to the existing Host service: no model loop, tools or credentials here."""
from pathlib import Path
from typing import Any

from .ambient import AdmissionBusy, AmbientScheduler


class ServiceExecutor:
    def __init__(self, service: Any):
        self.service = service

    def validate(self, task: dict) -> None:
        session = self.service._load(task["threadId"])
        if task["kind"] == "file":
            root = Path(session.workspace_dir).resolve()
            path = Path(str(task.get("path") or "")).expanduser()
            path = (path if path.is_absolute() else root / path).resolve()
            if not path.is_relative_to(root) or path == root:
                raise ValueError("watched file must be inside the thread workspace")
            if path.is_dir():
                raise ValueError("watch a file, not a directory")
            task["path"] = str(path)

    def ready(self, thread_id: str) -> bool:
        with self.service._guard:
            if self.service._is_active(thread_id):
                return False
            session = self.service._load(thread_id)
            return session.status.value not in {"running", "waiting_approval"}

    def start(self, task: dict, run: dict) -> dict:
        with self.service._guard:
            if not self.ready(task["threadId"]):
                raise AdmissionBusy("thread became busy during admission")
            before = self.service._load(task["threadId"]).usage.total_tokens
            response = self.service.turn_start({"threadId": task["threadId"], "input":
                f"[Automatic task: {task['name']}; source={run['source']}; run={run['id']}]\n"
                + task["prompt"] + "\nOnly report meaningful changes, completion, failure, or required user action. "
                "When monitoring finds no meaningful change, reply with exactly AMBIENT_NO_CHANGE."})
            return {"turnId": response["turn"]["id"], "usageStart": before}

    def poll(self, run: dict) -> dict | None:
        with self.service._guard:
            service = self.service
            session = service._load(run["threadId"])
            if service._is_active(run["threadId"]):
                return None
            if session.current_turn_id != run["turnId"]:
                return {"status": "needs_review", "error": service._task_errors.get(run["threadId"])
                        or "Thread changed before result reconciliation; inspect thread"}
            status = session.status.value
            if status == "running":
                return None
            return {"status": status, "tokens": max(0, session.usage.total_tokens - run["usageStart"]),
                    "result": session.final_text, "error": session.error}

    def cancel(self, run: dict) -> None:
        self.service._ambient_interrupt({"threadId": run["threadId"], "turnId": run["turnId"]})


class AmbientServiceMixin:
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.ambient = AmbientScheduler(self.store.root.parents[1], ServiceExecutor(self))
        self.ambient.start()
        original_close = self.runtime.close

        def close_runtime():
            self.ambient.close()
            original_close()

        self.runtime.close = close_runtime

    def turn_start(self, params: dict) -> dict:
        # Serialize check-and-admit for ALL clients, including timer and manual turns.
        with self._guard:
            return super().turn_start(params)

    def turn_interrupt(self, params: dict) -> dict:
        for run in self.ambient.snapshot()["runs"]:
            if (run["threadId"] == params.get("threadId") and run.get("turnId") == params.get("turnId")
                    and run["status"] in {"running", "waiting_approval"}):
                self.ambient.cancel(run["task"])
                return {"requested": True, "threadId": run["threadId"], "turnId": run["turnId"]}
        return super().turn_interrupt(params)

    def _ambient_interrupt(self, params: dict) -> dict:
        return super().turn_interrupt(params)


class AmbientControllerMixin:
    def _initialize(self, params: dict) -> dict:
        result = super()._initialize(params)
        result.setdefault("capabilities", {})["ambient"] = {
            "version": 1, "triggers": ["once", "interval", "file", "event"],
            "externalDelivery": False, "tokenLimit": "between_turns"}
        return result

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "ambient/list":
            return self.service.ambient.snapshot()
        if method == "ambient/history":
            return self.service.ambient.history(before=int(params["before"]) if params.get("before") else None,
                limit=int(params.get("limit", 100)))
        if method == "ambient/put":
            return {"task": self.service.ambient.put(params)}
        if method == "ambient/pause":
            if not isinstance(params.get("paused"), bool):
                raise ValueError("paused must be a boolean")
            return self.service.ambient.pause(params["paused"])
        if method in {"ambient/cancel", "ambient/delete"}:
            return self.service.ambient.cancel(str(params.get("id") or ""), delete=method.endswith("delete"))
        if method == "ambient/event":
            with self.service.ambient.guard:
                task = self.service.ambient._task(str(params.get("id") or ""))
                if not task or task["kind"] != "event":
                    raise ValueError("adapter events require an event task")
                return {"accepted": self.service.ambient.event(task["id"], str(params.get("key") or ""),
                            source=str(params.get("source") or "adapter"))}
        return super()._dispatch(method, params)
