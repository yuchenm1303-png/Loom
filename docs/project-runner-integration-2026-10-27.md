# Independently reviewed integration

Protocol v4 adds `projectRun/prepareIntegration` and
`projectRun/applyIntegration`, both with operation ID, run ID and expected revision.
Preparation takes the original delivered run; application takes its integration
child after that child has independently passed and been delivered.

Preparation freezes the current clean source branch/ref and HEAD, checks base
ancestry and the delivered branch, and uses `git merge-tree --write-tree` to
construct the merged result without modifying source files or index. Conflicts
return a Git error with the source intact and no new runnable review. A clean
merge becomes an isolated child run carrying the original objective and plan,
fresh review sessions/candidate, and the existing independent acceptance gate.
It starts directly in whole-project review; no planner or acceptance shortcut.
Failure can reopen the original task sequence for bounded repair in isolation.
The child inherits cumulative token/episode usage and limits. Optional
`tokenBudget`/`maxEpisodes` are explicit total top-ups, not silent resets.
Each explicitly requested alternative integration is a separate run/ledger;
this is not a globally pooled account budget.

After acceptance, the ordinary `projectRun/deliver` creates the child's frozen
commit, parented at the captured source HEAD. Application persists intent before
performing a fast-forward of the source checkout. It requires the same checked
out branch, a clean source, and that captured HEAD. Source advancement requires
a new integration review from the original delivered run. A fast-forward already
completed before process loss is reconciled by the same operation ID after
restart; no reset, force push, or duplicate merge is used. Git hooks are disabled
for this final merge and the resulting checkout must match the clean delivery
checkout before state becomes `integrated`. Remote publication stays pending.

Integration operations share the existing Git panel's common-directory lock,
and RPCs also hold the project scheduler guard. Final application refuses active
source-workspace sessions and holds the Host turn-launch guard while moving the
checkout. Uncoordinated external Git/filesystem
operations cannot be locked by the Host: pre/postconditions fail closed where
detectable, but this is not an OS transaction across branch switching, file
writes and SQLite. An interrupted apply with source edits needs inspection and
the original operation retry; the Host never rolls back user files. Conflicts,
dirty sources and prepared receipts remain available for inspection. Failed
preparation may retain unreferenced Git objects/worktrees; automatic cleanup and
conflict resolution remain future work.

Tests use real Git, runtime checks and scripted model responses. They cover a
source branch advancing before preparation, new review of merged content,
rework, fast-forward, SQLite reopen after interrupted application, source edits,
branch changes, later source advancement, merge conflicts and shared Git locks.
This slice exposes backend RPCs; automatic invocation, remote push/deployment,
frontend controls, multiworker execution and multi-day real-model qualification
are not provided by this change.
