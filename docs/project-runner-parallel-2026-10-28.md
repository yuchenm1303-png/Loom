# Bounded parallel project tasks

Protocol v6 supports `maxWorkers: 1 | 2` on create, defaulting to 1. Parallel work
requires independent review. Settings exposes the same opt-in limit, task links,
dependency/file scope declarations, and active team episode count. Ordinary
single-worker and omitted-field retry fingerprints retain their previous behavior.

The planner produces the original ordered task IDs plus `dependsOn`, `fileScopes`,
and `sharedResources`. Dependencies may name only earlier tasks, which makes the
graph acyclic. Missing dependencies default to the preceding task. Missing file
scopes claim the whole workspace; missing resources claim a shared environment.
Explicitly independent tasks with disjoint scopes and resources may run together.
Path containment conflicts are conservative and case insensitive. Invalid or
escaping scopes fail planning rather than granting broad access.

Each task becomes a durable child run, atomically adopted by the parent before
execution. Its detached worktree starts at the current aggregate commit, with
its own worker and independent task/whole-task review sessions. Dependent tasks
start only after all predecessors have been accepted and serially aggregated.
Children do not modify the source project or each other's checkout. Before a
candidate can enter review, and again before delivery/aggregation, the actual
tracked/nonignored changes are compared with the frozen baseline and declared
file scopes. Unclaimed changes fail closed; declared scopes are not just hints.
No automatic ignore rules hide generated files from this check.

Accepted task results are delivered through the existing exact-content gate,
then merged one at a time into the parent's aggregate checkout. Delivery intent
records the original operation ID and revision before execution. Merge intent
records old/new commits before checkout, allowing the same checked-out commit
to reconcile after response loss without replaying model work. Conflicts, dirty
or changed aggregate workspaces, and changed repository identity pause the parent
with a diagnostic. Nothing force-resets or overwrites user work. Once all task
results are combined, a new independent whole-project review must pass against
the aggregate. Failed aggregate acceptance uses the bounded sequential repair
path; it does not launch an unbounded new team. Existing manual delivery or
automatic local integration can then proceed normally.

The parent is the pooled project ledger: every child episode reserves one slot
in its transaction before dispatch, and every settled terminal result charges
its tokens to both child history and parent usage exactly once. Child limits
do not reset the shared allowance. Explicit parent budget increases propagate to
team children. Token caps remain soft between episodes: up to two already
admitted model episodes can overshoot a token threshold. This is not an account
spending cap or a ledger for user-initiated turns outside project episodes.
Internal project sessions skip speculative idle summarization so it cannot start
model work outside episode admission/accounting. Ordinary user conversations
retain their normal idle behavior; in-turn compaction remains available.

Only one project team runs concurrently in the project scheduler, with at most
two active episodes including review and approval waits. Other normal Host
conversations are outside this scheduler's concurrency limit. Parent control
gates every task dispatch, including after SQLite reopen. Cancellation is saved
first, propagated to known children, checked again before model activation, and
requests cooperative cancellation. Late results still settle usage. An unknown
execution outcome pauses coordination and is never blindly replayed.

This first version is for code/file/process tasks. Parallel prompts prohibit
desktop/browser automation, server ports and external/shared environment changes;
declared shared resources serialize incompatible tasks. Worktrees and declarations
are not OS/network/port isolation or a security sandbox. Tools still use the
source conversation's real permission boundary. Automatic conflict resolution,
hard per-request budget reservations, fairness across multiple project teams,
resource containers, cleanup/retention, and multi-day real-model qualification
remain future work.

Validation uses real runtime sessions and Git worktrees with routed scripted
model responses. A two-party barrier inside the actual write tool proves that
two workers overlap in distinct directories. Tests cover fresh aggregate evidence,
dependencies starting on the merged predecessor, full automatic local integration,
pooled exhaustion, scope violations, pause/cancel after SQLite reopen, aggregate
checkout interruption and recovery without duplicate model episodes, and conflict
preservation of a user commit. Frontend checks cover the parallel option payload
and owner-controlled task navigation. No paid-model or week-long run is claimed.
