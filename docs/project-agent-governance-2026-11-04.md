# Project agent admission and preparation recovery

Project worker and reviewer episodes have a single owner: the durable project
scheduler. Generic `spawn_agent` delegation remains available in ordinary chats,
but a project episode cannot spawn or wake a second, unregistered execution tree.
That tree would otherwise share the worker's checkout and evade the project's
two-worker limit, request admission, review and cancellation controls. Planning
independent tasks in the project DAG is the supported parallel execution path.

The guard is enforced by AgentControl before creating a session or enqueueing a
message, not only by a prompt or tool visibility. The model-admission boundary
also rejects historical unscheduled project descendants; activation cancels an
attempt to resume one directly. Project ancestry is resolved from the persisted
agent graph, including closed descendants. Normal chat delegation is unchanged.

At Host startup, existing project descendants are closed recursively. Reported
model/compaction usage in their durable events is imported into the owning run's
ledger, keyed by event ID. Reopening the database or reconciling again does not
charge it twice. Delayed usage events are still counted after cancellation.
Unreported historical provider usage cannot be reconstructed; this is not a
retroactive provider billing cap. Project cancellation closes historical branch
trees, cancels their queued/running work and terminates their session processes.

Parallel task creation now persists `parallelSpawn` on the parent before touching
the filesystem. It freezes the child ID and base commit. After a restart the same
intent is resumed and its pristine checkout is adopted atomically with its task
record. There is no new planning/model request at this recovery boundary. A
changed checkout or repository identity stops for inspection rather than resetting
it. Cancelled preparations are cleaned only if still pristine.

Upgrade cleanup also finds pre-intent workspaces: only canonical UUID-named,
unregistered worktrees directly inside the managed workspace directory qualify.
Their Git repository identity and original commit must match recorded project
history. Tracked changes, untracked files, ignored files, new commits, symlinks
and junctions are retained. Removal uses Git without `--force`.

Validation includes real runtime/agent-graph tests with scripted models, budget
import and cancellation races, SQLite reopen, ordinary chat delegation, and real
Host processes killed immediately before and after child worktree creation.
Existing parallel execution, independent review, program, workflow, budget and
process-recovery tests are included in the regression run. No paid models are
needed. This change does not claim week-long real-model qualification or add
arbitrary recursive project teams.
