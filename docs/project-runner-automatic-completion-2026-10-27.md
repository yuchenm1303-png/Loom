# Opt-in automatic local completion

Protocol v5 advertises `automaticLocalCompletion`. `projectRun/create` accepts
`completionPolicy: "local_integrate"` with independent review. The default is
`manual`; existing runs and omitted-field operation fingerprints keep their
previous behavior. Creation freezes the source branch ref and remains idle
until `projectRun/start`.

The Host serial scheduler then advances implementation, task/project review,
local delivery, isolated integration preparation, fresh whole-project review,
child delivery, and local fast-forward application. These transitions use the
existing version-bound acceptance gates, Git identity/content checks and
integration protection. Remote push and deployment remain pending. Git stage
transitions and idle scheduling do not sample a model.

Each stage first saves an operation ID, target run and expected revision in the
root run's `workflowStep`. The subsequent tick issues the existing idempotent
command. A lost response after child creation or Git application can therefore
be reconciled without blindly repeating work. Integration children carry an
owner ID from their creation transaction and cannot dispatch until that owner
has durably adopted them and is active. Paused, cancelled or blocked owners
cannot silently leave a child doing model work. Root pause/cancel changes the
known child control in the same SQLite transaction; cancellation additionally
requests cooperative runtime cancellation. Pause lets an active episode settle.

The root exposes `workflowControl`, `workflowStatus`, `workflowError`, and
`integrationRunId`; completion becomes `workflowStatus=completed`, `state=integrated`.
The integration child preserves its own evidence and receipt. Root counters are
updated from cumulative child usage when completion settles; while a child is
running its counters are the current ledger. Original delivery evidence remains
bound to the original candidate, not relabelled as evidence for the merged result.

Errors block further automatic stages with a durable diagnostic, rather than
hot-looping or sampling a model. After correcting an environmental issue the
user may explicitly resume. Recorded commands retain their original retry key;
commands with no durable receipt can be prepared again at the new revision.
Unknown model outcomes and failed/repeated reviews still require inspection;
resume does not waive acceptance. Token/episode limits are inherited without
reset or implicit top-up. Budget exhaustion before integration blocks the flow.
Workflow start/resume optionally accepts explicit total `tokenBudget` and
`maxEpisodes` above cumulative usage, updating the root and its owned child
atomically; omitted limits are preserved.
Changing the source branch, dirty source, Git conflicts, active source sessions,
or a source advance after integration review also block safely. Source advance
does not automatically create an unbounded sequence of new reviews.

Tests use scripted responses with real runtime tools and Git worktrees: one
start through local integration; no extra model calls on completion/idle;
pause/cancel propagation; durable child command reconciliation after SQLite
reopen; no orphan dispatch while blocked; dirty source and explicit retry;
and no implicit budget expansion. This is a backend feature, not frontend UI,
multiworker coordination, remote publication, or a multi-day real-model test.
