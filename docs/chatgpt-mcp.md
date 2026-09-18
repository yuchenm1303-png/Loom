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

The approval response tool is declared with UI visibility only. The model must not approve authority requested by its own Loom task.

New ChatGPT-remote threads use approval permission mode. Continuing or steering an existing thread is rejected when the thread has a broader active permission mode than the ChatGPT remote policy allows. Stopping a turn remains allowed because it reduces authority rather than adding it.

## Approval integrity

thread/read projects the durable pending approval and adds a SHA-256 fingerprint over the exact thread, turn, request, call, tool, arguments, and approval stage. approval/respond rereads authoritative thread state and rejects the response when the fingerprint no longer matches.

This does not replace Loom approval. It prevents a stale remote approval card from approving a different pending action.

## Idempotency

Remote task start and steer accept an idempotency key. Duplicate concurrent calls share one execution, and a key cannot be reused for a different operation. Approval response derives an idempotency identity from the approval fingerprint so UI retries do not execute the response twice.

The first implementation keeps this replay cache in the adapter process. Moving the replay ledger to durable App Server state is a later hardening step.

## Running the development adapter

After installing Loom, the entry point is:

    loom-chatgpt-mcp --workspace C:\path\to\project

The MCP transport is stdio. The adapter currently launches a Loom App Server child process and speaks the existing versioned JSON-RPC protocol to it.

This child-process topology is deliberately transitional. It is suitable for adapter and MCP contract development, but it must not become the final desktop topology because a running Loom Desktop already owns an App Server and active in-memory turn state. The next phase adds a multi-client local App Server IPC transport so Desktop, WeChat Remote, and ChatGPT Remote all attach to the same authoritative service instance.

## Secure MCP Tunnel

The production local connection should place OpenAI Secure MCP Tunnel in front of loom-chatgpt-mcp rather than exposing the Loom App Server or a local port to the public Internet. Tunnel setup is deployment configuration and is not an authorization boundary inside Loom.

## Non-goals of this phase

This phase does not:

- expose AgentRuntime tools directly to ChatGPT;
- let the MCP model choose permissionMode;
- add a second permission or sandbox implementation;
- make approval/respond model-callable;
- claim reliable background completion push into ChatGPT;
- solve the final shared Desktop/Remote local IPC topology.

Those are intentionally kept separate from the first protocol boundary.
