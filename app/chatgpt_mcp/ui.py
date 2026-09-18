from __future__ import annotations

THREAD_CONTROL_URI = "ui://loom/thread-control.html"

THREAD_CONTROL_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {
      color-scheme: light dark;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body { margin: 0; padding: 12px; background: transparent; }
    .card {
      border: 1px solid color-mix(in srgb, currentColor 18%, transparent);
      border-radius: 14px;
      padding: 14px;
      display: grid;
      gap: 10px;
    }
    .row { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
    .title { font-weight: 650; font-size: 14px; }
    .status { opacity: .72; font-size: 12px; }
    .approval {
      display: none;
      padding-top: 10px;
      border-top: 1px solid color-mix(in srgb, currentColor 14%, transparent);
      gap: 8px;
    }
    .approval.visible { display: grid; }
    .tool { font-weight: 600; font-size: 13px; }
    pre {
      margin: 0;
      max-height: 180px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace;
      background: color-mix(in srgb, currentColor 7%, transparent);
      border-radius: 9px;
      padding: 9px;
    }
    .actions { display: flex; justify-content: flex-end; gap: 8px; }
    button {
      border-radius: 9px;
      padding: 7px 12px;
      border: 1px solid color-mix(in srgb, currentColor 24%, transparent);
      background: transparent;
      color: inherit;
      cursor: pointer;
      font: inherit;
    }
    button.primary { font-weight: 650; }
    button:disabled { opacity: .45; cursor: default; }
    .message { min-height: 1.3em; opacity: .76; font-size: 12px; }
  </style>
</head>
<body>
  <section class="card" aria-live="polite">
    <div class="row">
      <div class="title">Loom task</div>
      <div id="status" class="status">Loading…</div>
    </div>
    <div id="approval" class="approval">
      <div>
        <div class="title">Loom needs your approval</div>
        <div id="tool" class="tool"></div>
      </div>
      <pre id="arguments"></pre>
      <div id="reason" class="status"></div>
      <div class="actions">
        <button id="decline" type="button">Decline</button>
        <button id="accept" class="primary" type="button">Allow</button>
      </div>
    </div>
    <div id="message" class="message"></div>
  </section>
  <script>
    (() => {
      const statusEl = document.getElementById("status");
      const approvalEl = document.getElementById("approval");
      const toolEl = document.getElementById("tool");
      const argsEl = document.getElementById("arguments");
      const reasonEl = document.getElementById("reason");
      const messageEl = document.getElementById("message");
      const acceptEl = document.getElementById("accept");
      const declineEl = document.getElementById("decline");

      let threadId = "";
      let fingerprint = "";

      function host() {
        return typeof window !== "undefined" ? window.openai : undefined;
      }

      function hiddenMeta() {
        const responseMetadata = host()?.toolResponseMetadata || {};
        const envelope =
          responseMetadata.mcp_tool_result ||
          responseMetadata.call_tool_result ||
          responseMetadata;
        return envelope?._meta || envelope?.meta || {};
      }

      function render() {
        const output = host()?.toolOutput || {};
        const thread = output.thread || {};
        const pending = output.pendingApproval || null;
        threadId = String(thread.id || output.threadId || "");
        fingerprint = String(hiddenMeta()["loom/approvalFingerprint"] || "");

        statusEl.textContent = String(thread.status || "unknown").replaceAll("_", " ");
        if (!pending) {
          approvalEl.classList.remove("visible");
          messageEl.textContent =
            thread.status === "completed" ? "Task completed." : "";
          return;
        }

        approvalEl.classList.add("visible");
        toolEl.textContent = String(pending.toolName || "tool request");
        argsEl.textContent = JSON.stringify(pending.arguments || {}, null, 2);
        reasonEl.textContent = String(pending.reason || "");
        const actionable = Boolean(threadId && fingerprint && host()?.callTool);
        acceptEl.disabled = !actionable;
        declineEl.disabled = !actionable;
        messageEl.textContent = actionable
          ? "Only your click can send this Loom approval decision."
          : "Approval controls are unavailable in this host.";
      }

      async function decide(decision) {
        if (!threadId || !fingerprint || !host()?.callTool) return;
        acceptEl.disabled = true;
        declineEl.disabled = true;
        messageEl.textContent = decision === "accept" ? "Allowing…" : "Declining…";
        try {
          await host().callTool("loom_approval_respond", {
            thread_id: threadId,
            fingerprint,
            decision,
          });
          approvalEl.classList.remove("visible");
          messageEl.textContent =
            decision === "accept" ? "Approval sent to Loom." : "Request declined.";
          if (host()?.sendFollowUpMessage) {
            await host().sendFollowUpMessage({
              prompt: "Refresh Loom thread " + threadId + " and report its latest status.",
              scrollToBottom: true,
            });
          }
        } catch (error) {
          messageEl.textContent =
            error instanceof Error ? error.message : "Could not send approval decision.";
          render();
        }
      }

      acceptEl.addEventListener("click", () => void decide("accept"));
      declineEl.addEventListener("click", () => void decide("decline"));
      window.addEventListener("openai:set_globals", render, { passive: true });
      render();
    })();
  </script>
</body>
</html>
"""

__all__ = ["THREAD_CONTROL_HTML", "THREAD_CONTROL_URI"]
