# Persistent project controls

Settings → Persistent projects exposes the existing project-run backend. Select
a conversation to retain its workspace/model/permissions, describe the objective
and acceptance requirements, and set total token/episode budgets. Creation stays
idle until Start. Automatic local integration is an unchecked opt-in: it updates
the local source branch after acceptance, without remote push or deployment.

The panel polls the lightweight run list only while mounted, without overlapping
polls or model calls. It shows implementation steps, whole-project acceptance,
current phase, actual usage/limits, durable errors, and local delivery refs and
commits. A linked integration child supplies live usage and evidence instead of
the root's older counters. Implemented steps are not presented as proof that the
merged result passed review. Detailed acceptance freshness is read explicitly;
it becomes visibly outdated when the run revision changes, rather than hashing
the whole source tree on every background poll.

Controls use the selected run's revision and one operation ID per command.
After a lost response, Retry reuses the exact original parameters/revision/ID.
Concurrent duplicate submissions are rejected. Other mutations stay disabled
until the retry is resolved or the user explicitly refreshes state and ends it.
That pending request is local component state: leaving this page does not retry
it automatically. Returning users inspect the durable task list; no task or
start is automatically recreated by the renderer.

Automatic roots own pause/resume/cancel. Their integration children expose only
explicit rereview when the backend can accept it, with primary control on the
root. Unknown active execution cannot be bypassed by rereview. Manual runs expose
the existing delivery/merge-review/apply stages as applicable. Explicit total
budget increases are submitted with Resume/Review/Prepare; unchanged automatic
resume limits are omitted. The displayed token cap is explicitly between episodes,
not a hard per-request cap. Source and active execution conversations can be
opened using the ordinary thread navigation, including existing approval controls.

Validation: renderer and Electron TypeScript checks, production renderer build,
13 logic/navigation tests, and browser interaction tests against a test-only
bridge fixture. The browser checks lost-response create retry without duplication,
creation without automatic start, start/pause, live child counters, acceptance
records, execution-thread navigation, English, dark/light/narrow layouts, and
offline errors. The fixture cannot reach Host or modify a real repository.
This validates the frontend interactions and RPC payloads; it is not an installed
desktop acceptance run or a multi-day real-model qualification. Multiworker
execution and remote publishing remain outside this slice.
