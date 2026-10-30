# Testing multi-day project runs without waiting days (2026-10-29)

A multi-day run fails on what days bring, not on what the model does in an hour:
Host restarts (updates, sleep, crashes), provider outages, malformed replies,
rejected reviews, a clock that moves through backoffs, and state that grows with
every episode. Waiting for those in real runs means finding each one days late
and unable to reproduce it. Instead they are compressed, in layers:

| Layer | Tool | Model | What it covers |
|---|---|---|---|
| 1. Soak | `scripts/soak_project_runner.py` | simulated, free | hundreds of episodes, days of fake clock, outages, malformed replies, rejected reviews, Host restarts between ticks; checks invariants |
| 2. Chaos | `scripts/chaos_project_runner.py` | real, ~5M tokens | a real qualification with its Host process killed at random moments and resumed (`smoke_project_runner.py --resume`) |
| 3. Larger projects | qualification scenarios | real | more tasks, hours instead of minutes, with chaos on |
| 4. Real multi-day run | the app | real | final acceptance, with monitoring and replay from `events.jsonl` |

The single crash points (killed before dispatch, model in flight, tool in flight,
mid-transaction) are covered by `tests/test_project_process_recovery.py`.

## Layer 1: the soak

The soak drives the real runtime (the context-managing class production uses), the
project runner, independent review, delivery and integration. A simulated agent plays
planner, workers and reviewers, and executes real checks.

It checks invariants, not a happy path:

- **integrated**: every task's file is integrated.
- **chargedOnce**: known tokens equal exactly what the simulated provider reported.
- **episodeStartBounded / episodeStartFlat**: what an episode starts with stays within the carried-history bound and does not drift upward over 20+ episodes.
- **nothingLeftRunning, noWorktreesLeft**: nothing is left running and no checkout is left behind.

Example: 16 tasks, two workers, `--continues 6`, `--minutes-per-tick 30`. It covered
3.3 simulated days in 12 minutes: 187 episodes, 22 Host restarts, 40 provider
failures, 14 malformed replies and 7 rejected reviews. The run ended integrated,
with 3,233,479 known tokens equal to what was reported, and no worktree left.

### What the soak found

1. **Episode prompts carried forever.** Chat compaction keeps up to 20k tokens of
   user messages. In a project session every episode's runner prompt is a user
   message, so after about 65 episodes each request carried about 20k tokens of
   stale prompts. That is about 1.2k characters more per episode in the soak
   (42 prompts carried in one session).
   - Fix: `compact_context(..., user_message_tokens=0)` for project sessions. The episode summary already holds the task, handoff and feedback.
   - Test: `test_continued_episode_starts_from_a_compacted_handoff_without_a_model_call`.
2. **A Host crash during a tool stopped the project for a person.** On a long run
   that is the most likely crash point.
   - Now an episode continues if everything still in flight was confined to its own checkout: read-only tools, the built-in workspace file tools, and `exec` whose processes ran in the enforced workspace sandbox with no network. The next episode is told which tools may have partly run. The independent review judges the result anyway.
   - Anything else in flight (an unsandboxed or networked process, any other tool) still waits for reconciliation. Nothing is ever replayed.
   - Tests: `test_killed_host_continues_when_the_tool_in_flight_was_confined_to_the_checkout`, `test_only_tools_confined_to_the_checkout_let_an_interrupted_episode_continue`.
3. Not defects, measured:
   - The project database grows by about 43 KB per episode (about 130 MB for 3,000 episodes).
   - A tick takes 1.7–5.5 s with parallel git work, without upward drift.
   - Worker prompts grow with the project's kept checks and notes, which are capped (24 and 30).

### Programs: milestones beyond 16 tasks

A run plans at most 16 tasks and ends when they are integrated. An objective meant to
run for a week is larger, and nobody knows its whole plan up front.

`app/project_runner/program.py` pursues one objective through successive milestones,
each an ordinary project run (same review, integration, budget and recovery):

- **Milestone 1:** the planner is told to plan only a first coherent batch.
- **After each integration:** the next milestone starts from the new commit. Its read-only planner sees the integrated repository and the earlier milestones' summaries, and plans the next batch, or returns `{"complete": true, "summary": ...}`. Only a later milestone may say complete.
- **Budget:** tokens and episodes are one budget across all milestones.
- **Idempotency:** milestone runs are created under deterministic operation IDs, so a Host that dies between creating a run and recording it finds the same run again.
- **Limit:** `maxMilestones` (≤ 50) bounds a program that never reports completion.

API: `projectProgram/create|get|list`. Tests: `tests/test_project_program.py`.

Soak `--milestones N` runs a program; the simulated planner reports completion after N milestones.

## Layer 2: chaos

```
python scripts/chaos_project_runner.py --root <new dir> --stored-model <id> --provider opencode-go \
    --scenario ledger --kills 6 --min-seconds 60 --max-seconds 420
```

Only the Host's own Python process is killed. Sandboxed children it started finish on
their own: killing a sandbox helper mid-run leaves access-control entries behind on
this machine. The pass criterion is the qualification's external oracle. The report
in `<root>-chaos/chaos-report.json` lists every kill and each Host life.
