# ChatGPT MCP Remote

Loom exposes ChatGPT as a remote-control channel over the existing App Server. ChatGPT is not an execution backend and does not receive a direct path to shell, browser, file, connector, or nested MCP handlers.

## Boundary

The intended control path is:

    ChatGPT / MCP host
        -> Loom ChatGPT MCP adapter
        -> RemoteControlClient
        -> Loom App Server RPC
        -> Agent Runtime
        -> ToolOrchestrator
        -> PermissionEngine
        -> approval / sandbox
        -> execution

The MCP adapter must not import AgentRuntime. Remote-control policy is only an exposure ceiling for a channel; Loom permission snapshots and PermissionEngine remain authoritative.

## Initial tool surface

The first MCP surface is intentionally small:

- loom_status
- loom_projects_list
- loom_threads_list
- loom_thread_read
- loom_task_start
- loom_task_steer
- loom_task_stop
- loom_approval_respond

The approval response tool is declared with UI visibility only. The model must not approve authority requested by its own Loom task. `loom_thread_read` is bound to the `ui://loom/thread-control.html` MCP Apps resource, which renders task state and explicit Allow / Decline controls in ChatGPT-compatible hosts.

New ChatGPT-remote threads use approval permission mode. Continuing or steering an existing thread is rejected when the thread has a broader active permission mode than the ChatGPT remote policy allows. Stopping a turn remains allowed because it reduces authority rather than adding it.

## Approval integrity

`thread/read` projects the durable pending approval into normal structured state. The MCP adapter separately computes a SHA-256 fingerprint over the exact thread, turn, request, call, tool, arguments, and approval stage and returns that fingerprint only in the MCP tool result metadata under `loom/approvalFingerprint`. It is deliberately absent from model-visible `structuredContent`.

The approval widget reads the fingerprint from host-provided tool-response metadata and passes it to the app-only `loom_approval_respond` tool. Before forwarding any decision, RemoteControl rereads authoritative App Server state and rejects the response when the fingerprint no longer matches.

This does not replace Loom approval. It prevents a stale remote approval card from approving a different pending action, while keeping the approval token out of the ordinary model tool result.

## Idempotency

Remote task start and steer accept an idempotency key. Duplicate concurrent calls in one adapter process still share one execution, and a key cannot be reused for a different logical request.

For `loom_task_start`, the same id is now also forwarded to App Server as `clientInputId` for both `thread/start` and `turn/start`. App Server owns a durable SQLite replay ledger under the Loom runtime home, so an MCP adapter or Secure MCP Tunnel restart cannot create a second thread / turn for the same logical task. New-thread task start therefore replays both layers: the original thread identity and the original first-turn identity.

Before Runtime adoption, App Server persists the reserved turn id plus the staged attachment manifest. If a process dies after preparation but before `TURN_STARTED`, the next call resumes that reserved turn from the original staged files. If durable `TURN_STARTED` already exists, the replay returns the original turn and does not re-run the model. A changed request under the same id fails closed.

Approval response continues to derive its retry identity from the approval fingerprint. Steering retains its existing adapter-side idempotency in this phase because it already carries a correlated turn id and has different replay semantics from starting a new turn.

## Running the development adapter

After installing Loom, the entry point is:

    loom-chatgpt-mcp --workspace C:\path\to\project

Start Loom Desktop first for the normal ChatGPT Remote path. For deliberate headless/development use only:

    loom-chatgpt-mcp --workspace C:\path\to\project --no-local-attach

The MCP transport is stdio. By default the adapter first looks for the authenticated local App Server endpoint published by Loom Desktop under the Loom runtime home. That endpoint is loopback-only, uses an ephemeral port and a per-process random token, and routes requests into the same App Server service instance that owns Desktop's active turns and pending approvals.

The default adapter mode requires the canonical shared App Server to already be running, normally because Loom Desktop is open. A missing descriptor is an error rather than an automatic hidden-runtime fallback. This prevents a tunnel process from starting a private Runtime and later racing a newly opened Desktop against the same durable state.

Use `--no-local-attach` only when an explicit standalone/development Runtime is intended. Standalone mode launches its App Server with `--local-ipc`, so it acquires the same OS-level ownership lock and becomes the one canonical App Server for that Loom home. A later Desktop instance therefore cannot silently start a competing local control plane.

Invalid metadata, non-loopback addresses, authentication failures, and connection failures also fail closed. At all times the design permits at most one local control endpoint owner per Loom runtime home.

A long-lived ChatGPT / Secure MCP Tunnel adapter also survives a normal Loom Desktop or canonical App Server restart. Before each local App Server request, the adapter rereads the published descriptor and compares its endpoint identity (transport, loopback address, ephemeral port, token, process id, and instance id) with the connection it currently holds. When the canonical endpoint changes, the adapter closes the stale socket, authenticates to the replacement endpoint, and repeats the App Server initialize handshake before sending the new request.

Reconnect does **not** mean blind write replay. If a transport dies after a request may have been sent:

- read-only App Server methods are reconnected and retried once;
- `thread/start` and `turn/start` are retried only when a non-empty `clientInputId` is present, relying on the durable App Server ledger above;
- steering, approval responses, interrupts/stops, and other writes are not automatically replayed because their outcome may already have been committed;
- a missing descriptor reports Loom offline instead of launching a private Runtime;
- a malformed, unreadable, non-loopback, unreachable, or authentication-failing replacement descriptor fails closed.

This retry policy is deliberately below MCP and above Runtime: ChatGPT tools keep their normal surface, while Loom's canonical App Server remains the only place that decides whether a write is safe to admit or replay.

The shared local endpoint carries request/response control traffic only in this phase. ChatGPT reconstructs authoritative progress with `thread/read`; the existing Desktop stdio client continues receiving App Server notifications directly. The ChatGPT widget may request a follow-up refresh after a user approval, but this is a foreground UX enhancement rather than a background task-completion push channel.

## Secure MCP Tunnel

The production local connection should place OpenAI Secure MCP Tunnel in front of `loom-chatgpt-mcp` rather than exposing the Loom App Server or a local port to the public Internet. Tunnel setup is deployment configuration and is not an authorization boundary inside Loom.

The official tunnel client supports a local stdio MCP child directly. The operator needs:

- `CONTROL_PLANE_TUNNEL_ID` for the provisioned tunnel;
- `CONTROL_PLANE_API_KEY` from an OpenAI Runtime API key principal with Tunnels Read + Use;
- one `main` MCP binding, which Loom supplies as the `loom-chatgpt-mcp` command.

Do not put an OpenAI admin key into the long-lived runner. Admin credentials are only for tunnel management. Also run exactly one active `tunnel-client` instance per tunnel id when the binding is stdio; multiple runners would create separate MCP children without request affinity.

For a source/development install on Windows:

    $env:CONTROL_PLANE_TUNNEL_ID = "tunnel_<32 lowercase hex>"
    $env:CONTROL_PLANE_API_KEY = "<runtime API key>"
    .\scripts\init_chatgpt_tunnel.ps1 -Workspace C:\path\to\workspace
    tunnel-client run --profile loom-chatgpt

The helper only validates the environment, creates the official `sample_mcp_stdio_local` profile, runs `tunnel-client doctor --explain`, and optionally starts the foreground runner with `-Run`. It never writes the runtime API key into Loom configuration.

For a packaged Loom build, pass `-McpCommand` that invokes the frozen private runtime's `loom_chatgpt_mcp.py` dispatch entrypoint. This keeps Secure MCP Tunnel outside the Loom trust boundary while still letting it attach to the canonical desktop App Server through the authenticated loopback endpoint.

While `tunnel-client run` is healthy, select or paste the matching tunnel in the ChatGPT connector/app settings. Tunnel discovery and every later MCP call require the local runner to remain active.

## Non-goals of this phase

This phase does not:

- expose AgentRuntime tools directly to ChatGPT;
- let the MCP model choose permissionMode;
- add a second permission or sandbox implementation;
- make approval/respond model-callable;
- claim reliable background completion push into ChatGPT;
- expose the local App Server endpoint beyond loopback or bypass its token handshake.

Those are intentionally kept separate from the first protocol boundary.
