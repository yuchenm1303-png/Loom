# Verified local branch delivery

`projectRun/deliver` accepts `operationId`, `runId`, and `expectedRevision`.
It requires a settled, independently passed **whole-project** candidate. Manual
candidates and individual task passes cannot enter delivery. The candidate,
acceptance definition, repository identity, and base ancestry are checked again.

Delivery copies the frozen candidate into a new detached checkout, stages its
complete tracked and nonignored file view, and creates a single commit parented
at the run's original base. Worker intermediate commits are squashed into this
snapshot. `commit-tree` avoids commit hooks and uses an explicit Loom identity.
Repository worktree hooks and configured Git filters still have their normal
Git behavior. A separate checkout of the resulting commit must match the
reviewed file bytes and modes exactly; conversion/filter differences fail closed.
No model tokens are consumed by delivery.

The receipt is saved in SQLite **before** creating
`refs/heads/codex/project-<run UUID>`. Ref creation uses Git compare-and-swap;
an existing different commit is never overwritten. Retrying the identical
operation after interruption uses the saved commit rather than generating a new
one. A ref already pointing at that exact commit reconciles successfully.
Only after this does the run become `delivered`, with
`delivery=local_verified_branch`. Source HEAD, index, branch, and dirty files
are untouched. The existing project Git panel can inspect the delivered branch.

This is a local deliverable, not completion of automatic integration. The receipt
explicitly reports `integration=pending` and `publication=pending`. Merging onto
a source branch that advanced requires acceptance against the merged content;
it is not safe to carry the old review over silently. This slice does not merge,
push, deploy, provide UI controls, or run multiple workers. Branch/ref operations
are atomic at Git level, and Host requests serialize with the project scheduler;
external Git commands can still race and will fail closed on ref conflicts.

Prepared receipts must be reconciled with their original operation ID before a
new review. A conflicting externally created delivery branch requires operator
inspection; it is not deleted automatically. Unreferenced commits or extra
detached checkouts left by preparation failure remain available for inspection;
automated retention/cleanup is future work. Verification covers tracked and
nonignored regular files under the existing snapshot bounds; ignored build
outputs/dependencies and external environment changes remain outside its scope.

Tests use real Git worktrees and the real runtime with scripted model responses:
end-to-end review then export, preservation of a staged dirty source, exact
committed content/base, idempotency, interruption after ref creation and SQLite
reopen, conflicting refs, changed candidates, stale revisions, and rejection of
manual-only candidates. This is not a paid-model or week-long endurance run.
