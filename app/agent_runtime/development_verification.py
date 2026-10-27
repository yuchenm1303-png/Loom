"""Automatic development obligations. Facts drive continuation, never prose keywords.

This service observes source changes, not user intent or functional correctness.
An explicit blocked/not-applicable disposition is auditable and is not a pass.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import time
import threading
from contextlib import contextmanager
from pathlib import Path

from app.ai import AIMessage, MessageRole
from .contracts import AgentEventKind as E, ToolEffect
from .tools import AgentTool, ToolContext, ToolResult
from .verification import subject_snapshot, check_freshness


KINDS = (E.VERIFICATION_UPDATED, E.VERIFICATION_FINISH_REQUESTED, E.CHECK_RECORDED)
_IGNORED = {".git", ".loom", ".cache", ".cursor-build", "node_modules", "__pycache__", ".venv", "venv", "dist", "dist-electron"}
_SOURCE = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss",
           ".html", ".rs", ".go", ".java", ".kt", ".swift", ".qml", ".xaml", ".svg"}
_VISUAL = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".qml", ".xaml", ".svg"}


def source_kind(path):
    parts = Path(path.replace("\\", "/")).parts
    if not parts or any(p in _IGNORED for p in parts) or any(p in {"tests", "__tests__", "docs"} for p in parts):
        return None
    name = parts[-1].lower()
    if any(mark in name for mark in (".test.", ".spec.")) or name.startswith("test_"):
        return None
    suffix = Path(name).suffix
    if suffix in {".json", ".yaml", ".yml"} and any(p.lower() in {"themes", "styles", "tokens", "locales", "i18n"} for p in parts):
        return "visual"
    if suffix not in _SOURCE:
        return None
    return "visual" if suffix in _VISUAL or (suffix in {".ts", ".js"} and any(
        part.lower() in {"components", "pages", "screens", "views"} for part in parts)) else "functional"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


_GIT_TIMEOUT = 3.0
_GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
_prefixes = {}
_prefix_guard = threading.Lock()


def _git(workspace, *args, timeout=_GIT_TIMEOUT):
    """One bounded git process, or None.

    Output goes to a temporary file, not a pipe: on Windows a pipe stays open while any
    process that inherited its handle lives, and ``subprocess.run(timeout=...)`` then
    waits for that process too. A file returns when git does, and the kill is final.
    Optional locks are off, so a scan never takes or waits on index.lock.
    """
    import tempfile
    with tempfile.TemporaryFile() as out:
        try:
            process = subprocess.Popen(["git", "-c", "core.fsmonitor=false", *args], cwd=workspace,
                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL, env=_GIT_ENV,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            return None
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return None
        if code:
            return None
        out.seek(0)
        return out.read(512_001)


def _git_prefix(workspace):
    """The workspace's path inside its repository; constant for a folder, so asked once."""
    key = os.path.normcase(os.path.abspath(str(workspace)))
    with _prefix_guard:
        if key in _prefixes:
            return _prefixes[key]
    output = _git(workspace, "rev-parse", "--show-prefix")
    if output is None:
        return None
    prefix = output.decode("utf-8", errors="replace").strip()
    with _prefix_guard:
        _prefixes[key] = prefix
    return prefix


def git_state(workspace):
    """(dirty source fingerprints, discovery, HEAD) from one ``git status`` run.

    Porcelain v2 with ``--branch`` carries the HEAD oid, so attribution needs one
    process per scan instead of separate status, prefix and rev-parse runs.
    """
    output = _git(workspace, "status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all",
                  "--ignore-submodules=all", "--", ".", ":(exclude).cache", ":(exclude)node_modules",
                  ":(exclude).loom")
    if output is None:
        files, discovery = plain_sources(workspace)
        return files, discovery, ""
    if len(output) > 512_000:
        return {}, "scope_too_large", ""
    prefix = _git_prefix(workspace)
    if prefix is None:
        files, discovery = plain_sources(workspace)
        return files, discovery, ""
    head, names = "", []
    records = output.decode("utf-8", errors="replace").split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if record.startswith("# branch.oid "):
            value = record[len("# branch.oid "):].strip()
            head = value if len(value) == 40 else ""
        elif record.startswith("1 "):
            names.append(record.split(" ", 8)[-1])
        elif record.startswith("2 "):
            names.append(record.split(" ", 9)[-1])
            if index < len(records):
                names.append(records[index])  # the rename's original path
                index += 1
        elif record.startswith("u "):
            names.append(record.split(" ", 10)[-1])
        elif record.startswith("? "):
            names.append(record[2:])
    names = [p[len(prefix):] for p in names if p.startswith(prefix)]
    paths = sorted({p for p in names if source_kind(p)})
    if len(paths) > 128:
        return {}, "scope_too_large", head
    context = ToolContext("workspace", "observation", Path(workspace))
    files = {}
    for path in paths:
        try:
            files.update(subject_snapshot(context, [path])["files"])
        except (ValueError, OSError):
            files[path] = "unavailable"
    return files, "available", head


def git_sources(workspace):
    """Dirty source fingerprints, bounded and read-only. Never opens unrelated clean files."""
    files, discovery, _ = git_state(workspace)
    return files, discovery


def plain_sources(workspace):
    """Non-Git projects retain bounded source observation too."""
    names, visited = [], 0
    for root, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = [name for name in dirs if name not in _IGNORED]
        visited += len(dirs) + len(files)
        if visited > 4096:
            return {}, "scope_too_large"
        for name in files:
            relative = Path(root, name).relative_to(workspace).as_posix()
            if source_kind(relative):
                names.append(relative)
                if len(names) > 128:
                    return {}, "scope_too_large"
    try:
        return (subject_snapshot(ToolContext("workspace", "observation", Path(workspace)), names)["files"], "available")
    except (ValueError, OSError):
        return {}, "unavailable"


def git_head(workspace):
    output = _git(workspace, "rev-parse", "HEAD")
    value = output.decode("ascii", errors="ignore").strip() if output is not None else ""
    return value if len(value) == 40 else ""


def committed_paths(workspace, before, after):
    if not before or not after or before == after:
        return []
    output = _git(workspace, "diff", "--relative", "--name-only", "-z", "--no-renames", before, after, "--", ".")
    if output is None or len(output) > 512_000:
        return []
    return [p for p in output.decode("utf-8", errors="replace").split("\0") if source_kind(p)][:128]


def feature_recipes(context, paths):
    """Optional project advice, not authorization and never auto-executed."""
    try:
        target = context.resolve_workspace_path(".loom/verification.json")
        if not target.exists():
            target = context.resolve_workspace_path("loom.verification.json")
            if not target.exists():
                return [], "not_configured"
        if target.stat().st_size > 64_000:
            raise ValueError("feature map exceeds 64 KiB")
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("features"), list) or len(data["features"]) > 64:
            raise ValueError("expected version=1 and up to 64 features")
        recipes = []
        for feature in data["features"]:
            if not isinstance(feature, dict) or not isinstance(feature.get("sources"), list):
                raise ValueError("feature requires sources")
            if any(not isinstance(p, str) for p in feature["sources"]):
                raise ValueError("source patterns must be strings")
            if any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in feature["sources"]):
                recipes.append({key: str(feature.get(key, ""))[:2000] for key in
                                ("id", "entry", "setup", "exercise", "expectation")})
        return recipes[:16], "configured"
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        return [], f"invalid: {str(exc)[:200]}"


_GIT_READS = {"status", "log", "show", "diff", "grep", "rev-parse", "ls-files", "ls-tree", "blame",
              "check-ignore", "merge-base", "cat-file", "describe", "shortlog", "rev-list", "for-each-ref",
              "show-ref", "help", "version", "whatchanged", "name-rev", "count-objects"}
_SHELL_READS = {"dir", "type", "findstr", "find", "where", "echo", "more", "tree", "ver", "cd", "rg", "ls", "cat",
                "head", "tail", "wc", "grep", "pwd", "which", "stat", "file", "du", "df"}


def _read_only_words(words):
    if not words:
        return False
    program = os.path.basename(words[0]).lower()
    if program.endswith(".exe"):
        program = program[:-4]
    if program == "git":
        rest = list(words[1:])
        while rest and rest[0] in {"-C", "-c", "--git-dir", "--work-tree"}:
            rest = rest[2:]
        while rest and rest[0].startswith("-") and rest[0] not in {"--version", "--help"}:
            rest = rest[1:]
        return bool(rest) and rest[0].lower() in _GIT_READS or (bool(rest) and rest[0] in {"--version", "--help"})
    return program in _SHELL_READS


def read_only_command(argv):
    """True only for commands that provably cannot write: a git read or a listing/search, no redirection.

    Anything else, including every interpreter, is treated as a possible writer and scanned.
    """
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        return False
    program = os.path.basename(argv[0]).lower()
    if program.endswith(".exe"):
        program = program[:-4]
    if program == "cmd" and len(argv) >= 3 and argv[1].lower() in {"/c", "/s", "/d"}:
        line = " ".join(a for a in argv[1:] if a.lower() not in {"/c", "/s", "/d"})
        if any(mark in line for mark in (">", "%", "^", "`", "$(")):
            return False
        import re
        segments = [segment.strip() for segment in re.split(r"&&|\|\||&|\|", line)]
        return all(_read_only_words(segment.split()) for segment in segments if segment) and any(segments)
    if any(mark in a for a in argv[1:] for mark in (">",)) and program in _SHELL_READS:
        return False
    return _read_only_words(list(argv))


class DevelopmentVerification:
    _workspace_guard = threading.Lock()
    _workspace_locks = {}

    def __init__(self, runtime):
        self.runtime = runtime
        self._pending_deliveries = {}
        self._timing = {}
        self._timing_guard = threading.Lock()

    def _spent(self, session_id, key, started):
        elapsed = (time.perf_counter() - started) * 1000
        with self._timing_guard:
            bucket = self._timing.setdefault(session_id, {})
            bucket[key] = round(bucket.get(key, 0) + elapsed, 1)

    def take_timing(self, session_id):
        """What verification cost since the last model request, for that request's event."""
        with self._timing_guard:
            return self._timing.pop(session_id, {})

    @contextmanager
    def execution(self, context, tool_name, arguments=None):
        """Attribute mutations inside a tool boundary, not at an arbitrary later scan.

        Local mutating tools share a workspace lease. Foreign processes are not
        inferred to belong to this task merely because HEAD/status changed.
        A command that cannot write (a search, a listing, a git read) is not scanned.
        """
        if tool_name not in {"exec", "apply_patch", "write_workspace_text"}:
            yield
            return
        if tool_name == "exec" and read_only_command((arguments or {}).get("argv")):
            yield
            return
        key = os.path.normcase(str(context.workspace.resolve()))
        with self._workspace_guard:
            lock = self._workspace_locks.setdefault(key, threading.RLock())
        with lock:
            started = time.perf_counter()
            if tool_name == "exec":
                before, available, head = git_state(context.workspace)
            else:
                before, available, head = {}, "unavailable", ""
            self._spent(context.session_id, "attribution_scan_ms", started)
            try:
                yield
            finally:
                if available == "available":
                    started = time.perf_counter()
                    try:
                        self._capture_execution(context, before, head)
                    except (OSError, ValueError, RuntimeError):
                        # Auxiliary attribution must not convert an executed
                        # operation into a reported tool failure and invite replay.
                        import logging
                        logging.getLogger(__name__).warning("Could not capture source attribution for %s", context.turn_id)
                    self._spent(context.session_id, "attribution_scan_ms", started)

    def _capture_execution(self, context, before, head):
        after, status, new_head = git_state(context.workspace)
        if status != "available":
            return
        candidates = set(before) | set(after) | set(committed_paths(context.workspace, head, new_head))
        missing = sorted(candidates - set(after))
        current = {**(subject_snapshot(context, missing)["files"] if missing else {}),
                   **{p: v for p, v in after.items() if p in candidates}}
        changed = {p for p in candidates if before.get(p) != current.get(p)}
        if not changed:
            return
        session = self.runtime.store.load(context.session_id)
        state = self.latest(session)
        if state:
            owned = sorted(set(state.get("owned_paths", [])) | changed)
            self.runtime._emit_event(session, E.VERIFICATION_UPDATED, data={**state, "owned_paths": owned})

    def events(self, session):
        # context_select is indexed and preserves only the small verification payloads.
        return self.runtime._lifecycle_events(session.session_id, kinds=KINDS)

    def latest(self, session):
        # Read several times per step; only the newest state matters, so on a long session do not build
        # (and copy) every earlier 20 KB state just to throw them away.
        newest = getattr(self.runtime.store, "last_event", None)
        if callable(newest):
            event = newest(session.session_id, (E.VERIFICATION_UPDATED,))
            return event.data if event is not None else None
        select = getattr(self.runtime.store, "context_select", None)
        if callable(select):
            newest = select(session.session_id, kinds=(E.VERIFICATION_UPDATED,), last=1)
            return newest[-1].data if newest else None
        return next((e.data for e in reversed(self.events(session)) if e.kind is E.VERIFICATION_UPDATED), None)

    @staticmethod
    def generation(state):
        return _digest({p: item.get("snapshot") for p, item in state.get("subjects", {}).items()})

    def observed(self, session, call, result):
        if not result.ok:
            return
        name = call.name
        action = name in {"browser_click", "browser_click_at", "browser_type", "browser_press", "browser_hover",
                          "browser_drag", "browser_scroll", "browser_select", "browser_send_text", "browser_run_check", "exec"}
        action = action or (name == "computer_action" and call.arguments.get("action", {}).get("type") not in
                            {"screenshot", "done", "call_user"})
        visual = name in {"browser_screenshot", "browser_capture_sequence", "view_images", "browser_run_check", "computer_action"}
        if not action and not visual:
            return
        state = self.latest(session)
        if not state or not state.get("subjects"):
            return
        if name == "view_images":
            # Reading an old attachment after editing source is not a fresh UI
            # observation. Project-generated captures can qualify by file time;
            # Loom-owned browser/desktop captures qualify directly.
            source_changed = max((s.get("changed_at_ns", 0) for s in state["subjects"].values()), default=0)
            images = result.data.get("images", [])
            visual = bool(images) and all(i.get("source_modified_ns", 0) >= source_changed for i in images)
        receipts = list(state.get("observations", []))[-63:]
        receipts.append({"call_id": call.call_id, "generation": self.generation(state), "action": action,
                         "visual_candidate": visual, "delivered": False, "turn_id": session.current_turn_id})
        self.runtime._emit_event(session, E.VERIFICATION_UPDATED, data={**state, "observations": receipts})

    def delivered(self, session, extra):
        ids = list(extra.get("workspace_visual_observation", {}).get("source_call_ids", []))
        browser = extra.get("browser_observation", {})
        if browser.get("has_screenshot"):
            ids.append(browser.get("source_call_id"))
        computer = extra.get("computer_observation", {})
        if computer:
            ids.append(computer.get("source_call_id"))
        if not ids:
            self._pending_deliveries.pop(session.session_id, None)
            return
        self._pending_deliveries[session.session_id] = (session.current_turn_id, ids)

    def confirm_delivery(self, session):
        pending = self._pending_deliveries.pop(session.session_id, None)
        if pending is None or pending[0] != session.current_turn_id:
            return
        ids = pending[1]
        state = self.latest(session)
        if not state:
            return
        receipts = [{**r, "delivered": True} if r["call_id"] in ids else r for r in state.get("observations", [])]
        if receipts != state.get("observations", []):
            self.runtime._emit_event(session, E.VERIFICATION_UPDATED, data={**state, "observations": receipts})

    def visual_evidence(self, session, refs, *, delivered_ids=()):
        state = self.latest(session) or {}
        generation = self.generation(state)
        ids = {r.get("call_id") for r in refs}
        current = [r for r in state.get("observations", []) if r["generation"] == generation and r["call_id"] in ids]
        return (any(r.get("visual_candidate") and (r.get("delivered") or r["call_id"] in delivered_ids) for r in current)
                and any(r.get("action") for r in current))

    def execution_evidence(self, session, refs):
        state = self.latest(session) or {}
        ids = {r.get("call_id") for r in refs}
        return any(r.get("action") and r["call_id"] in ids and r["generation"] == self.generation(state)
                   for r in state.get("observations", []))

    def begin(self, session):
        previous = self.latest(session)
        if previous and previous.get("turn_id") == session.current_turn_id:
            return  # approval resume / safe handoff retains the original baseline
        # Attribution belongs to the mutating tool's workspace lease, where
        # execution() already captures the before/after state. Scanning here
        # neither attributes edits nor protects tools, but delays every reply.
        # Carry evidence unchanged; summary() checks freshness when it is used.
        carried = dict((previous or {}).get("subjects", {}))
        self.runtime._emit_event(session, E.VERIFICATION_UPDATED,
            data={"turn_id": session.current_turn_id, "baseline": {}, "discovery": "deferred",
                  "subjects": carried, "last_git": {}, "head": "", "observations": []})

    def observe(self, session, *, scan_git=False):
        started = time.perf_counter()
        try:
            self._observe(session, scan_git=scan_git)
        finally:
            self._spent(session.session_id, "observe_ms", started)

    def _observe(self, session, *, scan_git=False):
        state = self.latest(session)
        if state is None:
            return
        subjects = dict(state.get("subjects", {}))
        paths = set(self.runtime.diff_trackers.snapshot(session.session_id, session.current_turn_id).paths)
        paths.update(state.get("owned_paths", []))
        last_git, discovery = state.get("last_git", {}), state.get("discovery", "unavailable")
        head = state.get("head", "")
        if scan_git:
            current, discovery, head = git_state(session.workspace_dir)
            if discovery == "available":
                last_git = current
        context = ToolContext(session.session_id, session.current_turn_id, Path(session.workspace_dir))
        for path in sorted(paths | subjects.keys()):
            kind = source_kind(path)
            if kind is None:
                continue
            old = subjects.get(path)
            if old is not None and path not in paths:
                # Prior-turn obligations need no per-tool filesystem reads;
                # summary() checks evidence freshness when it is consumed.
                continue
            try:
                snapshot = subject_snapshot(context, [path])
            except (ValueError, OSError):
                snapshot = None
            if old is None or old.get("snapshot") != snapshot:
                try:
                    changed_at = context.resolve_workspace_path(path).stat().st_mtime_ns
                except OSError:
                    changed_at = time.time_ns()
                subjects[path] = {"kind": kind, "status": "pending", "snapshot": snapshot,
                                  "origin_turn_id": session.current_turn_id, "changed_at_ns": changed_at}
        payload = {**state, "subjects": subjects, "last_git": last_git, "head": head, "discovery": discovery}
        if payload != state:
            self.runtime._emit_event(session, E.VERIFICATION_UPDATED, data=payload)

    def summary(self, session, *, delivered_ids=()):
        state = self.latest(session) or {}
        context = ToolContext(session.session_id, session.current_turn_id, Path(session.workspace_dir))
        # An ordinary conversation has no development obligations. Do not read
        # its entire check history (or touch source files) just to report that.
        select = getattr(self.runtime.store, "context_select", None)
        events = (() if not state.get("subjects") else
                  select(session.session_id, kinds=(E.CHECK_RECORDED,)) if callable(select) else self.events(session))
        subjects = []
        snapshots = {}
        latest = {}
        for event in events:
            if event.kind is E.CHECK_RECORDED:
                latest[event.data.get("case_id")] = event
        for path, item in sorted(state.get("subjects", {}).items()):
            status, reason = "pending", "No current executed acceptance check covers this source."
            applicable = []
            # Most recent assessment per case, including prior turns for a pending follow-up.
            for event in latest.values():
                data = event.data
                scope = data.get("subject_snapshot", {}).get("files", {})
                if path not in scope or check_freshness(data, context, snapshots=snapshots) != "current":
                    continue
                if item["kind"] == "visual" and data.get("check_kind") != "visual":
                    # Static/compiler success cannot discharge visible behavior.
                    continue
                if item["kind"] == "functional" and data.get("check_kind") == "static":
                    continue
                if data.get("assessment_source") == "runtime_browser_assertions" and not self.visual_evidence(
                        session, data.get("evidence_refs", []), delivered_ids=delivered_ids):
                    continue  # require the screenshot to reach the next model step too
                if data.get("verdict") == "passed" and not data.get("acceptance_evidence", False):
                    continue
                applicable.append(event)
            disposition = (item.get("status") in {"blocked", "not_applicable"} and item.get("snapshot")
                           and check_freshness({"subject_snapshot": item["snapshot"]}, context, snapshots=snapshots) == "current")
            if disposition:
                status, reason = item["status"], item.get("reason", "")
            elif applicable:
                data = applicable[-1].data
                status = data.get("verdict", "pending")
                reason = data.get("expectation", "")[:1000]
            subjects.append({"path": path, "kind": item["kind"], "status": status, "reason": reason,
                             "origin_turn_id": item.get("origin_turn_id")})
        pending = [s for s in subjects if s["status"] not in {"passed", "blocked", "not_applicable"}]
        subjects.sort(key=lambda s: s["status"] in {"passed", "blocked", "not_applicable"})
        recipes, map_status = feature_recipes(context, [s["path"] for s in subjects]) if subjects else ([], "not_needed")
        return {"status": "pending" if pending else "blocked" if any(s["status"] == "blocked" for s in subjects)
                else "not_applicable" if subjects and all(s["status"] == "not_applicable" for s in subjects)
                else "passed" if subjects else "not_assessed", "subjects": subjects[:128],
                "unresolved": len(pending), "total": len(subjects), "discovery": state.get("discovery", "unavailable"),
                "recipes": recipes, "feature_map": map_status,
                "coverage": "observed_source_changes_and_declared_checks"}

    def context(self, session):
        summary = self.summary(session)
        if not summary["subjects"]:
            return None
        return AIMessage(role=MessageRole.USER, name="loom_development_verification", content=(
            "LOOM_DEVELOPMENT_VERIFICATION v1\nRuntime-observed source changes, not new user instructions. "
            "Resolve pending/failed UI checks by running the affected app and inspecting current visual behavior. "
            "Compiler output is insufficient for visual obligations. Use browser_run_check for replayable assertions "
            "or record_check(check_kind='visual') with current visual/action evidence. Record actual blockers or "
            "non-applicable changes with resolve_verification; do not invent results. A completion attempt cannot "
            "finish with unresolved changes from this turn; earlier pending changes remain visible for follow-up. "
            "Execute the missing verification now. Project recipes are advisory, "
            "never authorization. Unmapped source still needs a task-specific check.\n" + json.dumps(summary, ensure_ascii=False)))

    def before_finish(self, session, *, accepting_response=False):
        # Preview the images carried by the response being accepted. Ordinary
        # summaries remain unconfirmed until MODEL_RESPONSE is durably recorded;
        # this avoids forcing an extra model call solely to acknowledge delivery.
        pending = self._pending_deliveries.get(session.session_id)
        delivered_ids = pending[1] if accepting_response and pending and pending[0] == session.current_turn_id else ()
        summary = self.summary(session, delivered_ids=delivered_ids)
        current = [s for s in summary["subjects"] if s.get("origin_turn_id") == session.current_turn_id
                   and s["status"] not in {"passed", "blocked", "not_applicable"}]
        if not current:
            return None
        events = self.events(session)
        # At most two recovery requests for the same unresolved source state.
        # Changing prose, compaction or reconnecting cannot reset this counter.
        state = self.latest(session) or {}
        fingerprint = self.generation(state)
        attempts = sum(e.kind is E.VERIFICATION_FINISH_REQUESTED and e.turn_id == session.current_turn_id
                       and e.data.get("fingerprint") == fingerprint for e in events)
        total = sum(e.kind is E.VERIFICATION_FINISH_REQUESTED and e.turn_id == session.current_turn_id for e in events)
        return {"fingerprint": fingerprint, "attempt": attempts + 1, "total_attempts": total + 1,
                "exhausted": attempts >= 2 or total >= 6, "unresolved": len(current), "subjects": current}


def resolve_verification_tool(service):
    def resolve(context, arguments):
        runtime = service.runtime
        session = runtime.store.load(context.session_id)
        state = service.latest(session)
        if not state:
            raise ValueError("no observed changes require a disposition")
        subjects = dict(state["subjects"])
        from .evidence import resolve_evidence
        facts, invalid = resolve_evidence(runtime.store, context, arguments["evidence_refs"])
        if invalid:
            raise ValueError("disposition requires an executed observation or existing workspace evidence")
        for path in arguments["paths"]:
            if path not in subjects:
                raise ValueError(f"unknown verification subject: {path}")
        for path in arguments["paths"]:
            subjects[path] = {**subjects[path], "status": arguments["disposition"], "reason": arguments["reason"],
                              "snapshot": subject_snapshot(context, [path]), "evidence_facts": facts}
        context.emit(E.VERIFICATION_UPDATED, {**state, "subjects": subjects})
        return ToolResult(True, "Disposition recorded. Blocked and not-applicable are not acceptance passes.",
                          {"paths": arguments["paths"], "disposition": arguments["disposition"], "reason": arguments["reason"]})
    from .evidence import EVIDENCE_REFS_SCHEMA
    return AgentTool("resolve_verification", "Record why an observed source change cannot be validated or has no "
        "runtime behavior to validate (for example a type-only change). This does not mark a check passed. "
        "Supply concrete reason and evidence_refs; do not use this to avoid an available UI check.",
        {"type": "object", "additionalProperties": False, "properties": {
            "paths": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "string"}},
            "disposition": {"enum": ["blocked", "not_applicable"]},
            "reason": {"type": "string", "minLength": 20, "maxLength": 2000},
            "evidence_refs": EVIDENCE_REFS_SCHEMA}, "required": ["paths", "disposition", "reason", "evidence_refs"]},
        resolve, effect=ToolEffect.READ_ONLY, supports_parallel_tool_calls=False)
