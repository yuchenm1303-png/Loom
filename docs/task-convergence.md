# Task convergence and readable progress

The observed browser acceptance run repeatedly announced final delivery, received
a Stop rejection, ran another tool batch, and announced delivery again. Its
assessor added recovery/cleanup requirements across those reviews. Tool batches
reset generation retry counters, so the total number of semantic continuations
was unbounded. Browser observations were transported as unlabelled user turns,
and the live transcript expanded every intermediate paragraph.

## What Codex actually establishes

The comparison uses OpenAI's public Codex source pinned at
`b741e480e203f037ca726bc2a76d99a8e8668e66`:

- [Turn loop](https://github.com/openai/codex/blob/b741e480e203f037ca726bc2a76d99a8e8668e66/codex-rs/core/src/session/turn.rs): tool follow-ups, pending inputs and Stop hooks are distinct lifecycle paths.
- [Plan handler](https://github.com/openai/codex/blob/b741e480e203f037ca726bc2a76d99a8e8668e66/codex-rs/core/src/tools/handlers/plan.rs): `update_plan` emits structured plan state and returns a short acknowledgement.
- [Agent prompt](https://github.com/openai/codex/blob/b741e480e203f037ca726bc2a76d99a8e8668e66/codex-rs/core/gpt-5.2-codex_prompt.md): plans are for substantial work; communication defaults to concise.

Public protocol parity does not establish parity with every private Codex app
feature or model's training. A general model-based completion judge is a Loom
extension, not proof that all requested work is complete.

## Runtime changes

- Transient browser/desktop text is appended to its existing tool result with
  its original call ID. It never becomes human guidance. Images use a named,
  explicitly labelled transport attachment for compatible providers that accept
  vision only on user messages; language inference and Stop intent exclude it.
  Full DOM and image data remain ephemeral. Trust boundaries stay in system
  instructions and are applied silently.
- `update_plan` persists 2–8 outcome milestones as `plan_updated` events. At most
  one may be in progress. Completed steps require an evidence reference; blocked
  steps require a blocker. Changing scope or reopening resolved steps requires
  an explanation. Current-turn state is reinjected after history compaction and
  survives approval/host resumptions; a new turn does not inherit it as authority.
  These are model-maintained claims, not independently verified success.
- The assessor gets a fresh context containing actual user inputs, authoritative
  instructions, milestone state, attributed execution excerpts and the candidate.
  Actor narration, hidden reasoning, image transport and prior Stop feedback
  cannot silently become new requirements. Tool excerpts retain both ends and
  explicitly mark omissions; omissions alone do not justify rerunning work.
  Added execution evidence is bounded to an internal 12k-token budget and the
  captured model's declared input budget when known. Call identities/outcomes
  remain; if protected intent/evidence identities cannot fit, completion fails
  explicitly instead of repeatedly sending an over-length review request.
- Semantic continuations have a durable **turn-wide** budget, default 3
  (`AgentLimits.max_stop_continuations`, configurable 1–10). Tools and approval
  resumptions cannot reset it. On exhaustion, status is `limit_reached` with an
  explicit incomplete-work message and remaining tasks, never false completion.
  Provider/schema retries remain a separate bounded policy.
- Default prompt v8 asks for milestone changes and useful findings instead of
  per-receipt acknowledgement or trust-boundary monologues. Existing Loom default
  prompts upgrade; custom prompts are preserved.

## Presentation

An active turn displays the latest successful plan with completed/total counts
and explicit blocked steps. Old completed tool entries and commentary fold into
an expandable history; current work, streaming items, user steering, approvals,
errors, failed tools, decision cards, edited files and explicit final answers stay
visible. Every original item remains available; folding changes presentation,
not history or finality.

These changes require an updated Host runtime for execution behavior. A web UI
deployment/refresh updates presentation only. Automated scripted regression tests
verify the runtime contract; model-specific end-to-end efficiency still needs
measurement after that runtime is installed.
