from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from app.app_server_client import LoomAppServerClient

from .base import RemoteChannel, RemoteMessage, RemoteSessionState, sanitize_remote_text


LogListener = Callable[[str], None]


_HELP_TEXT = """Loom 远程控制命令：
/status  查看当前任务
/new     新建远程会话
/stop    停止当前任务
/allow   允许当前待审批操作
/deny    拒绝当前待审批操作
/help    显示帮助

直接发送文字即可给 Loom 布置任务；任务执行中继续发文字会作为实时补充指令。"""


class LoomRemoteService:
    """Channel-neutral remote-control adapter for the Loom App Server.

    All task lifecycle and approval actions stay on Loom's public App Server
    protocol. Transport channels only provide authenticated text ingress/egress
    and never reach into AgentRuntime directly.
    """

    def __init__(
        self,
        *,
        app_client: LoomAppServerClient,
        channel: RemoteChannel,
        state: RemoteSessionState,
        workspace: str | Path,
        permission_mode: str | None = None,
        log: LogListener | None = None,
    ) -> None:
        self.app_client = app_client
        self.channel = channel
        self.state = state
        self.workspace = Path(workspace).expanduser().resolve()
        self.permission_mode = str(permission_mode or "").strip() or None
        self.log = log or (lambda _message: None)
        self._guard = threading.RLock()
        self._pending_approval: dict[str, Any] | None = None
        self.app_client.subscribe_notifications(self.on_notification)

    def _send(self, text: str) -> None:
        clean = sanitize_remote_text(text)
        if not clean:
            return
        try:
            self.channel.send_text(clean)
        except Exception as exc:
            self.log(f"[remote] failed to send reply: {type(exc).__name__}")

    def _thread_id(self) -> str:
        return str(self.state.thread_id or "").strip()

    def _create_thread(self) -> str:
        payload = self.app_client.thread_start(
            workspace=self.workspace,
            permission_mode=self.permission_mode,
        )
        thread = payload.get("thread") or {}
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            raise RuntimeError("Loom App Server did not return a thread id")
        self.state.set_thread_id(thread_id)
        with self._guard:
            self._pending_approval = None
        return thread_id

    def _ensure_thread(self) -> tuple[str, dict[str, Any] | None]:
        thread_id = self._thread_id()
        if thread_id:
            try:
                return thread_id, self.app_client.thread_read(thread_id)
            except Exception as exc:
                self.log(
                    f"[remote] saved thread could not be resumed: {type(exc).__name__}"
                )
        return self._create_thread(), None

    def _status_text(self, snapshot: dict[str, Any] | None = None) -> str:
        thread_id = self._thread_id()
        if not thread_id:
            return "目前还没有远程会话。直接发一条任务，或发送 /new 创建会话。"
        if snapshot is None:
            snapshot = self.app_client.thread_read(thread_id)
        thread = snapshot.get("thread") or {}
        title = str(thread.get("title") or "远程会话")
        status = str(thread.get("status") or "unknown")
        current_turn = str(thread.get("currentTurnId") or "")
        pending = snapshot.get("pendingApproval")
        lines = [
            f"Loom：{title}",
            f"状态：{status}",
            f"会话：{thread_id[:8]}",
        ]
        if current_turn:
            lines.append(f"任务：{current_turn[:8]}")
        if pending:
            lines.append("等待审批：是（回复 /allow 或 /deny）")
        return "\n".join(lines)

    def handle_message(self, message: RemoteMessage) -> None:
        text = message.text.strip()
        if not text:
            return
        command = text.casefold()

        try:
            if command == "/help":
                self._send(_HELP_TEXT)
                return

            if command == "/new":
                current_thread_id = self._thread_id()
                if current_thread_id:
                    current = self.app_client.thread_read(current_thread_id)
                    current_status = str((current.get("thread") or {}).get("status") or "")
                    if current_status in {"running", "waiting_approval"}:
                        self._send(
                            "当前 Loom 任务还没有结束，不能直接切换会话。"
                            "请先等待完成，或发送 /stop 停止当前任务。"
                        )
                        return
                thread_id = self._create_thread()
                self._send(f"✅ 已创建新的 Loom 远程会话：{thread_id[:8]}")
                return

            if command == "/status":
                self._send(self._status_text())
                return

            if command == "/stop":
                thread_id = self._thread_id()
                if not thread_id:
                    self._send("当前没有正在执行的远程任务。")
                    return
                snapshot = self.app_client.thread_read(thread_id)
                thread = snapshot.get("thread") or {}
                turn_id = str(thread.get("currentTurnId") or "").strip()
                status = str(thread.get("status") or "")
                if not turn_id or status not in {"running", "waiting_approval"}:
                    self._send(f"当前没有可停止的任务（状态：{status or 'idle'}）。")
                    return
                self.app_client.turn_interrupt(thread_id, turn_id)
                self._send("⏹ 已向 Loom 发送停止请求。")
                return

            if command in {"/allow", "/deny"}:
                with self._guard:
                    approval = dict(self._pending_approval or {})
                if not approval:
                    thread_id = self._thread_id()
                    snapshot = self.app_client.thread_read(thread_id) if thread_id else {}
                    recovered = snapshot.get("pendingApproval")
                    approval = dict(recovered) if isinstance(recovered, dict) else {}
                if not approval:
                    self._send("当前没有等待审批的操作。")
                    return
                params = {
                    "threadId": str(approval.get("threadId") or self._thread_id()),
                    "turnId": str(approval.get("turnId") or ""),
                    "requestId": str(approval.get("requestId") or ""),
                    "callId": str(approval.get("callId") or ""),
                    "decision": "accept" if command == "/allow" else "decline",
                }
                if not all(params.values()):
                    raise RuntimeError("pending approval is missing correlation fields")
                self.app_client.request("approval/respond", params)
                with self._guard:
                    self._pending_approval = None
                self._send(
                    "✅ 已允许，Loom 继续执行。"
                    if command == "/allow"
                    else "🚫 已拒绝该操作。"
                )
                return

            thread_id, snapshot = self._ensure_thread()
            if snapshot is None:
                snapshot = self.app_client.thread_read(thread_id)
            thread = snapshot.get("thread") or {}
            status = str(thread.get("status") or "")
            turn_id = str(thread.get("currentTurnId") or "").strip()

            if status == "waiting_approval":
                self._send("Loom 正在等待你的审批。先回复 /allow 或 /deny，再发送新任务。")
                return

            if status == "running" and turn_id:
                self.app_client.request(
                    "turn/steer",
                    {"threadId": thread_id, "turnId": turn_id, "input": text},
                )
                self._send("↪️ 已把这条消息补充到当前任务。")
                return

            self.app_client.turn_start(thread_id, text)
            self._send("🟢 收到，Loom 已开始执行。")
        except Exception as exc:
            self.log(f"[remote] command failed: {type(exc).__name__}")
            self._send(f"⚠️ Loom 处理这条指令时出错：{type(exc).__name__}")

    def on_notification(self, method: str, params: dict[str, Any]) -> None:
        thread_id = self._thread_id()
        if not thread_id:
            return
        notified_thread_id = str(
            params.get("threadId")
            or (
                (params.get("thread") or {}).get("id")
                if isinstance(params.get("thread"), dict)
                else ""
            )
            or (
                (params.get("item") or {}).get("threadId")
                if isinstance(params.get("item"), dict)
                else ""
            )
        )
        if notified_thread_id != thread_id:
            return

        if method == "approval/requested":
            approval = params.get("approval")
            if not isinstance(approval, dict):
                return
            with self._guard:
                self._pending_approval = dict(approval)
            tool_name = str(approval.get("toolName") or "工具")
            effect = str(approval.get("effect") or "")
            reason = str(approval.get("reason") or "")
            arguments = approval.get("arguments")
            args_text = str(arguments) if arguments else "{}"
            if len(args_text) > 500:
                args_text = args_text[:497] + "..."
            reply = ["⚠️ Loom 请求操作权限", f"工具：{tool_name}"]
            if effect:
                reply.append(f"级别：{effect}")
            if reason:
                reply.append(f"原因：{reason}")
            reply.append(f"参数：{args_text}")
            reply.append("\n回复 /allow 允许，或 /deny 拒绝。")
            self._send("\n".join(reply))
            return

        if method == "turn/completed":
            turn = params.get("turn")
            if not isinstance(turn, dict):
                return
            with self._guard:
                self._pending_approval = None
            status = str(turn.get("status") or "")
            final_text = str(turn.get("finalText") or "").strip()
            error = str(turn.get("error") or "").strip()
            if status == "completed" and final_text:
                self._send(final_text)
            elif error:
                self._send(f"⚠️ Loom 任务结束（{status or 'error'}）。")
            else:
                self._send(f"ℹ️ Loom 任务已结束，状态：{status or 'unknown'}")


__all__ = ["LoomRemoteService"]
