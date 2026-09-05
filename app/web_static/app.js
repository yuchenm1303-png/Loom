const state = {
  bootstrap: null,
  sessionId: "",
  snapshot: null,
  lastRenderKey: "",
  polling: null,
  toastTimer: null,
};

const el = (id) => document.getElementById(id);

const refs = {
  sessionList: el("sessionList"),
  newSessionButton: el("newSessionButton"),
  emptyNewSessionButton: el("emptyNewSessionButton"),
  workspacePath: el("workspacePath"),
  modelName: el("modelName"),
  permissionSelect: el("permissionSelect"),
  emptyState: el("emptyState"),
  messageScroller: el("messageScroller"),
  messages: el("messages"),
  approvalCard: el("approvalCard"),
  approvalTool: el("approvalTool"),
  approvalEffect: el("approvalEffect"),
  approvalReason: el("approvalReason"),
  approvalArguments: el("approvalArguments"),
  approveButton: el("approveButton"),
  denyButton: el("denyButton"),
  composerStatus: el("composerStatus"),
  promptInput: el("promptInput"),
  sendButton: el("sendButton"),
  stopButton: el("stopButton"),
  statusBadge: el("statusBadge"),
  tokenCount: el("tokenCount"),
  eventCount: el("eventCount"),
  runtimeError: el("runtimeError"),
  activityList: el("activityList"),
  modalBackdrop: el("modalBackdrop"),
  closeModalButton: el("closeModalButton"),
  cancelModalButton: el("cancelModalButton"),
  createSessionButton: el("createSessionButton"),
  workspaceInput: el("workspaceInput"),
  newPermissionSelect: el("newPermissionSelect"),
  toast: el("toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `${response.status} ${response.statusText}`);
  }
  return data;
}

function showToast(message) {
  refs.toast.textContent = message;
  refs.toast.classList.remove("hidden");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => refs.toast.classList.add("hidden"), 2600);
}

function setOptions(select, values, selected) {
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === selected;
    select.append(option);
  }
}

function openModal() {
  if (!state.bootstrap) return;
  refs.workspaceInput.value = state.snapshot?.session?.workspace_dir || state.bootstrap.default_workspace || "";
  setOptions(
    refs.newPermissionSelect,
    state.bootstrap.permission_modes,
    state.snapshot?.session?.permission_mode || state.bootstrap.default_permission_mode,
  );
  refs.modalBackdrop.classList.remove("hidden");
  setTimeout(() => refs.workspaceInput.focus(), 0);
}

function closeModal() {
  refs.modalBackdrop.classList.add("hidden");
}

function compactNumber(value) {
  const n = Number(value || 0);
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}m`;
}

function sessionStatusLabel(status, active) {
  if (active) return "running";
  return status || "idle";
}

function renderSessions(sessions) {
  refs.sessionList.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "activity-empty";
    empty.textContent = "No sessions yet.";
    refs.sessionList.append(empty);
    return;
  }

  for (const session of sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-item${session.session_id === state.sessionId ? " active" : ""}`;
    button.dataset.sessionId = session.session_id;

    const title = document.createElement("div");
    title.className = "session-title";
    title.textContent = session.title || "New session";

    const meta = document.createElement("div");
    meta.className = "session-meta";
    const dot = document.createElement("span");
    dot.className = `session-dot${session.active ? " running" : ""}`;
    const text = document.createElement("span");
    text.textContent = `${session.permission_mode} · ${compactNumber(session.tokens)} tok`;
    meta.append(dot, text);

    button.append(title, meta);
    button.addEventListener("click", () => selectSession(session.session_id));
    refs.sessionList.append(button);
  }
}

function createMessageNode(message) {
  if (!message || !["user", "assistant"].includes(message.role)) return null;
  const hasText = String(message.content || "").trim().length > 0;
  const calls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
  if (!hasText && !calls.length) return null;

  const root = document.createElement("article");
  root.className = `message ${message.role}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = message.role === "assistant" ? "L" : "You";

  const body = document.createElement("div");
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = message.role === "assistant" ? "Loom" : "You";
  body.append(label);

  if (hasText) {
    const text = document.createElement("div");
    text.className = "message-body";
    text.textContent = message.content;
    body.append(text);
  }

  if (calls.length) {
    const chips = document.createElement("div");
    chips.className = "tool-chip-row";
    for (const call of calls) {
      const chip = document.createElement("span");
      chip.className = "tool-chip";
      chip.textContent = call.name || "tool";
      chips.append(chip);
    }
    body.append(chips);
  }

  root.append(avatar, body);
  return root;
}

const interestingEventKinds = new Set([
  "model_requested",
  "tool_requested",
  "tool_approval_required",
  "tool_approved",
  "tool_denied",
  "tool_started",
  "tool_completed",
  "tool_failed",
  "process_started",
  "process_output",
  "process_exited",
  "turn_diff_updated",
  "history_repaired",
  "context_checkpointed",
  "memory_extracted",
  "memory_consolidated",
  "turn_completed",
  "turn_failed",
  "turn_cancelled",
  "limit_reached",
]);

function eventPresentation(event) {
  const data = event.data || {};
  const kind = event.kind || "event";
  if (kind === "model_requested") return ["◇", "Model step", `Step ${data.step ?? ""}`.trim()];
  if (kind === "tool_requested") return ["→", data.tool || "Tool requested", summarizeArguments(data.arguments)];
  if (kind === "tool_started") return ["·", data.tool || "Tool running", "Running"];
  if (kind === "tool_completed") return ["✓", data.tool || "Tool completed", String(data.content || "").slice(0, 170)];
  if (kind === "tool_failed") return ["!", data.tool || "Tool failed", String(data.content || "").slice(0, 170)];
  if (kind === "tool_approval_required") return ["?", data.tool || "Approval", data.reason || "Waiting for approval"];
  if (kind === "tool_approved") return ["✓", data.tool || "Approved", "Approved by user"];
  if (kind === "tool_denied") return ["×", data.tool || "Denied", data.source || "Denied"];
  if (kind === "process_started") return ["▶", "Process started", data.command || data.process_id || ""];
  if (kind === "process_output") return ["…", "Process output", String(data.output || data.content || "").slice(0, 170)];
  if (kind === "process_exited") return ["■", "Process exited", `Exit ${data.returncode ?? data.exit_code ?? ""}`.trim()];
  if (kind === "turn_diff_updated") return ["±", "Workspace changed", data.path || data.summary || "Diff updated"];
  if (kind === "history_repaired") return ["↻", "History repaired", "Recovered interrupted state"];
  if (kind === "context_checkpointed") return ["▣", "Context checkpoint", "Conversation context compacted"];
  if (kind === "memory_extracted") return ["◇", "Memory extracted", `${data.candidate_count || 0} candidate(s)`];
  if (kind === "memory_consolidated") return ["◇", "Memory consolidated", `${data.count || 0} record(s)`];
  if (kind === "turn_completed") return ["✓", "Turn completed", "Ready"];
  if (kind === "turn_failed") return ["!", "Turn failed", data.error || "Runtime reported a failure"];
  if (kind === "turn_cancelled") return ["■", "Turn stopped", "Cancelled"];
  if (kind === "limit_reached") return ["!", "Runtime limit", data.reason || "Limit reached"];
  return ["·", kind.replaceAll("_", " "), ""];
}

function summarizeArguments(argumentsValue) {
  if (!argumentsValue || typeof argumentsValue !== "object") return "";
  const keys = Object.keys(argumentsValue);
  if (!keys.length) return "";
  const pairs = keys.slice(0, 3).map((key) => {
    const raw = argumentsValue[key];
    let value = typeof raw === "string" ? raw : JSON.stringify(raw);
    if (value.length > 56) value = `${value.slice(0, 53)}…`;
    return `${key}: ${value}`;
  });
  return pairs.join(" · ");
}

function renderActivity(events) {
  const filtered = events.filter((event) => interestingEventKinds.has(event.kind)).slice(-80).reverse();
  refs.activityList.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "activity-empty";
    empty.textContent = "Tool calls and runtime events will appear here.";
    refs.activityList.append(empty);
    return;
  }

  for (const event of filtered) {
    const [iconText, titleText, detailText] = eventPresentation(event);
    const item = document.createElement("div");
    item.className = "activity-item";

    const icon = document.createElement("div");
    icon.className = "activity-icon";
    icon.textContent = iconText;

    const content = document.createElement("div");
    const title = document.createElement("div");
    title.className = "activity-title";
    title.textContent = titleText;
    content.append(title);
    if (detailText) {
      const detail = document.createElement("div");
      detail.className = "activity-detail";
      detail.textContent = detailText;
      content.append(detail);
    }
    item.append(icon, content);
    refs.activityList.append(item);
  }
}

function renderApproval(pending, active) {
  const visible = Boolean(pending);
  refs.approvalCard.classList.toggle("hidden", !visible);
  if (!visible) return;
  refs.approvalTool.textContent = pending.tool_name || "Tool call";
  refs.approvalEffect.textContent = pending.effect || "sensitive";
  refs.approvalReason.textContent = pending.reason || "Loom needs your approval before continuing.";
  refs.approvalArguments.textContent = JSON.stringify(pending.arguments || {}, null, 2);
  refs.approveButton.disabled = active;
  refs.denyButton.disabled = active;
}

function renderSnapshot(snapshot, { preserveScroll = false } = {}) {
  state.snapshot = snapshot;
  const session = snapshot.session;
  const active = Boolean(snapshot.active || session.active);
  const status = sessionStatusLabel(session.status, active);
  const scroller = refs.messageScroller;
  const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;

  refs.emptyState.classList.add("hidden");
  refs.messageScroller.classList.remove("hidden");
  refs.workspacePath.textContent = session.workspace_dir;
  refs.permissionSelect.disabled = active;
  refs.permissionSelect.value = session.permission_mode;

  refs.statusBadge.textContent = status.replaceAll("_", " ");
  refs.statusBadge.className = `status-badge ${status}`;
  refs.tokenCount.textContent = compactNumber(snapshot.usage?.total_tokens || 0);
  refs.eventCount.textContent = compactNumber(snapshot.events?.length || 0);

  refs.runtimeError.textContent = snapshot.error || "";
  refs.runtimeError.classList.toggle("hidden", !snapshot.error);

  refs.messages.replaceChildren();
  for (const message of snapshot.messages || []) {
    const node = createMessageNode(message);
    if (node) refs.messages.append(node);
  }

  renderActivity(snapshot.events || []);
  renderApproval(snapshot.pending_approval, active);

  const waiting = Boolean(snapshot.pending_approval);
  refs.promptInput.disabled = active || waiting;
  refs.sendButton.disabled = active || waiting || !refs.promptInput.value.trim();
  refs.stopButton.classList.toggle("hidden", !active);

  if (active) {
    refs.composerStatus.textContent = "Loom is working… tool activity updates on the right.";
  } else if (waiting) {
    refs.composerStatus.textContent = "Review the approval request before continuing.";
  } else if (session.status === "failed") {
    refs.composerStatus.textContent = "The last turn failed. You can send another message.";
  } else {
    refs.composerStatus.textContent = "Ready.";
  }

  if (!preserveScroll || nearBottom) {
    requestAnimationFrame(() => {
      scroller.scrollTop = scroller.scrollHeight;
    });
  }
}

async function refreshSession({ preserveScroll = true, refreshSessions = false } = {}) {
  if (!state.sessionId) return;
  try {
    const snapshot = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}`);
    const renderKey = JSON.stringify([
      snapshot.session.updated_at,
      snapshot.session.status,
      snapshot.active,
      snapshot.messages.length,
      snapshot.events.length,
      snapshot.pending_approval?.call_id || "",
      snapshot.error || "",
    ]);
    if (renderKey !== state.lastRenderKey) {
      state.lastRenderKey = renderKey;
      renderSnapshot(snapshot, { preserveScroll });
    } else {
      state.snapshot = snapshot;
    }
    if (refreshSessions) await refreshSessionList();
  } catch (error) {
    showToast(error.message);
  }
}

async function refreshSessionList() {
  const bootstrap = await api("/api/bootstrap");
  state.bootstrap = bootstrap;
  refs.modelName.textContent = bootstrap.model || "Loom";
  renderSessions(bootstrap.sessions || []);
}

async function selectSession(sessionId) {
  state.sessionId = sessionId;
  state.lastRenderKey = "";
  renderSessions(state.bootstrap?.sessions || []);
  await refreshSession({ preserveScroll: false });
  await refreshSessionList();
}

async function sendPrompt() {
  const text = refs.promptInput.value.trim();
  if (!state.sessionId || !text || refs.sendButton.disabled) return;
  refs.promptInput.value = "";
  resizeTextarea();
  refs.sendButton.disabled = true;
  try {
    await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/turn`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    state.lastRenderKey = "";
    await refreshSession({ preserveScroll: false, refreshSessions: true });
  } catch (error) {
    refs.promptInput.value = text;
    resizeTextarea();
    showToast(error.message);
  }
}

async function resolveApproval(approved) {
  const pending = state.snapshot?.pending_approval;
  if (!state.sessionId || !pending) return;
  refs.approveButton.disabled = true;
  refs.denyButton.disabled = true;
  try {
    await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/approval`, {
      method: "POST",
      body: JSON.stringify({ call_id: pending.call_id, approved }),
    });
    state.lastRenderKey = "";
    await refreshSession({ preserveScroll: true, refreshSessions: true });
  } catch (error) {
    showToast(error.message);
    refs.approveButton.disabled = false;
    refs.denyButton.disabled = false;
  }
}

async function stopTurn() {
  if (!state.sessionId) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/cancel`, {
      method: "POST",
      body: "{}",
    });
    state.lastRenderKey = "";
    await refreshSession({ preserveScroll: true, refreshSessions: true });
  } catch (error) {
    showToast(error.message);
  }
}

async function createSession() {
  const workspace = refs.workspaceInput.value.trim();
  const permissionMode = refs.newPermissionSelect.value;
  refs.createSessionButton.disabled = true;
  try {
    const snapshot = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ workspace, permission_mode: permissionMode }),
    });
    closeModal();
    state.sessionId = snapshot.session.session_id;
    state.lastRenderKey = "";
    await refreshSessionList();
    renderSnapshot(snapshot, { preserveScroll: false });
  } catch (error) {
    showToast(error.message);
  } finally {
    refs.createSessionButton.disabled = false;
  }
}

async function changePermission() {
  if (!state.sessionId) return;
  const target = refs.permissionSelect.value;
  try {
    const snapshot = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/permission`, {
      method: "POST",
      body: JSON.stringify({ permission_mode: target }),
    });
    state.lastRenderKey = "";
    renderSnapshot(snapshot, { preserveScroll: true });
    await refreshSessionList();
  } catch (error) {
    showToast(error.message);
    if (state.snapshot) refs.permissionSelect.value = state.snapshot.session.permission_mode;
  }
}

function resizeTextarea() {
  refs.promptInput.style.height = "auto";
  refs.promptInput.style.height = `${Math.min(refs.promptInput.scrollHeight, 180)}px`;
  const blocked = !state.sessionId || state.snapshot?.active || state.snapshot?.pending_approval;
  refs.sendButton.disabled = blocked || !refs.promptInput.value.trim();
}

async function boot() {
  try {
    const bootstrap = await api("/api/bootstrap");
    state.bootstrap = bootstrap;
    refs.modelName.textContent = bootstrap.model || "Loom";
    setOptions(refs.permissionSelect, bootstrap.permission_modes || [], bootstrap.default_permission_mode);
    setOptions(refs.newPermissionSelect, bootstrap.permission_modes || [], bootstrap.default_permission_mode);
    renderSessions(bootstrap.sessions || []);

    let initial = bootstrap.preferred_session_id;
    if (!initial && bootstrap.sessions?.length) initial = bootstrap.sessions[0].session_id;
    if (initial) {
      await selectSession(initial);
    } else {
      refs.emptyState.classList.remove("hidden");
      refs.messageScroller.classList.add("hidden");
      openModal();
    }

    state.polling = setInterval(async () => {
      if (!state.sessionId) return;
      await refreshSession({ preserveScroll: true });
      if (state.snapshot?.active || state.snapshot?.pending_approval) {
        await refreshSessionList();
      }
    }, 850);
  } catch (error) {
    showToast(`Cannot start UI: ${error.message}`);
  }
}

refs.newSessionButton.addEventListener("click", openModal);
refs.emptyNewSessionButton.addEventListener("click", openModal);
refs.closeModalButton.addEventListener("click", closeModal);
refs.cancelModalButton.addEventListener("click", closeModal);
refs.createSessionButton.addEventListener("click", createSession);
refs.modalBackdrop.addEventListener("click", (event) => {
  if (event.target === refs.modalBackdrop) closeModal();
});
refs.sendButton.addEventListener("click", sendPrompt);
refs.stopButton.addEventListener("click", stopTurn);
refs.approveButton.addEventListener("click", () => resolveApproval(true));
refs.denyButton.addEventListener("click", () => resolveApproval(false));
refs.permissionSelect.addEventListener("change", changePermission);
refs.promptInput.addEventListener("input", resizeTextarea);
refs.promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendPrompt();
  }
});
refs.workspaceInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") createSession();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !refs.modalBackdrop.classList.contains("hidden")) closeModal();
});

boot();
