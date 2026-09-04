# Durable Thread design notes

The durable thread layer follows one rule: persist intent and canonical history, never the execution stack.

Runtime boundaries:

- `CoreAgentRuntime`: model/tool execution, permissions, managed processes, patch/diff.
- `DurableAgentRuntime`: cross-turn goal, queue dispatch, interrupted-history repair.
- `DurableThreadStateStore`: SQLite persistence for goal and queue state.

This separation keeps process lifetime, permission lifetime, and thread lifetime explicit instead of conflating them in one session object.
