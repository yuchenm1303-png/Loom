# Host process crash qualification

`tests/test_project_process_recovery.py` starts a separate Python Host with the
production project service, runtime, Git workspace and SQLite/session stores.
The parent waits for a precise checkpoint, forcibly kills that exact process,
then creates a fresh service from disk. The child runs no shutdown hooks.
Scripted model responses make this repeatable without credentials or paid calls.

Covered interruption boundaries:

| Boundary | Required recovery |
| --- | --- |
| Planner completed; project has not imported its result | Import its durable terminal turn once, charge 5 tokens once, retain pause, then resume directly into the worker. |
| Worker intent committed; dispatch has not started | No tool ran (the turn's durable events show none): close the episode as interrupted and continue with a new episode; never send the old one again. |
| Worker model request admitted and still in flight | No tool ran: close the turn as interrupted, keep known planner usage and an unknown request reserve, continue with a new episode; the old request is not replayed. |
| Worker tool wrote a file but has not returned | Preserve the partial file and 12 known tokens; pause instead of repeating the side effect. |
| Control transaction changed run, event and command rows but has not committed | Roll back all three together, pass SQLite integrity check, then import the earlier completed planner normally. |

For an uncertain outcome (a tool was in flight), three scheduler ticks must leave
exactly two reserved episodes and zero new model requests, and resume must
explicitly refuse pending reconciliation.

Updated 2026-10-29: an episode interrupted before any tool ran has no side effect
to replay or reconcile, so it no longer stops the project. On a multi-day run a
Host update, sleep or crash most often lands while the model is thinking; pausing
every such run for a human made long autonomy impossible. The decision reads the
turn's durable events (no `tool_requested`/`tool_started`), never message text,
and a tool in flight still requires reconciliation. A killed process must also release the scheduler OS lease so the
new service can acquire it.

This is process-crash qualification with scripted responses, not a machine power
failure test, real-provider crash test or multi-day soak. A file written by a
tool may survive while its execution outcome remains uncertain; automatically
replaying it would be unsafe. Such runs intentionally require reconciliation.

Run with the repository's test dependencies available:

```powershell
python -m pytest -o addopts= -q tests/test_project_process_recovery.py
```

Validation on a clean worktree of `a567f774`: **77 passed** across process
recovery, runner, budget, workflow and integration tests (203 s). All five
process-kill cases passed; the clean worktree excluded concurrent uncommitted
runner/parallel and desktop changes in the shared checkout.

Still needed for the broader persistent-agent goal: a larger real project with
rework, real-provider interruption/restart qualification, installed-desktop
acceptance and a multi-day run. Existing small-project and two-worker real-model
results are recorded separately in the live qualification document.
