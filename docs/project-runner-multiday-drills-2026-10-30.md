# Project runner: multi-day drills (2026-10-30)

What a program meets when it runs for days, and what each meeting showed. A literal multi-day
run is still to come; this compresses each multi-day hazard into a drill that takes minutes to
hours, and runs a real program alongside to watch for what only accumulates.

## Drills and findings

| Hazard | Drill | Found | Fix |
|---|---|---|---|
| Sign-in expires / key revoked overnight | soak `--auth-expired FROM:TO --resume-after-auth` (401 on every request for a window) | One 401 stopped a team task for inspection at once; the hub offered "re-review", which was refused (no candidate). The project was stuck for good. | `credential` outcome: retry after 5, 10, 20, 40, 60, 60 min, then `credential_blocked` (never `needs_review`); the hub says to sign in or fix the key and offers Continue (9c2d876f). Soak: a 1 h refusal heals itself; a 5 h one waits, then completes after Continue. |
| Long Loom home path | soak under a deep folder | `git worktree add` fails at ~210 characters on Windows ("'$GIT_DIR' too big"; `core.longpaths` does not lift it). `delivery-checks/<run>/<40-char commit>` hit it; the one-level review folders had added 38 characters. | Leaf names cut to 12 characters (9c2d876f). |
| Machine sleeps | live Host and its children suspended 15 min (NtSuspendProcess), then once more with a model request in flight (10 min) | The project continued both times; the in-flight request returned the moment the Host woke. The hub, however, waited on a read the frozen Host never answered: blank, or stuck on "reading the project" forever. | Reads time out after 20 s; the hub says it cannot reach the Host and offers Try again (9c2d876f). Limit: a process freeze keeps the network up; a real sleep drops connections, which the transient-retry path covers (seen live on 10-29). Not yet drilled with the lid closed. |
| Days of state | soak: 6 milestones x 12 tasks, 10% outages, 10% malformed replies, 30% rejected reviews, a Host restart every 7 ticks, a 7 h credential window; 2.7 simulated days | Passed: 53 restarts, every invariant (integrated files, tokens charged once, flat episode starts, nothing left running, no worktrees left). The scheduler re-read every run once per run: a tick parsed run JSON ~n^2 times (7,500 parses at 86 runs; tens of milestones would make a tick take a minute). | The loop walks only runs that can still be scheduled and counts episodes out with one query: 890 ms -> 20 ms on the final state (b48a542d). |
| Memory over hours | live Host RSS every 10 min; tracemalloc on a 90-tick in-process soak | The live Host grew ~30 MB/h. Most of it: a new system message per request, so identity-keyed memos never hit and filled with copies until their 24M-character budget pushed them out. Bounded, but wasted. | An unchanged system message is reused per session (57d215f7); probe growth 7.2 -> 5.5 MB per 60 ticks; the rest is bounded memo tables. |
| Restart Windows | not drilled (needs the person present) | The sandbox prep's NUL-device fix is reset at boot and reapplied by a boot task; unverified after a real reboot. | - |

## Live program (weave-live2)

Started from the Weave hub (16M budget, 2 workers, guidance typed after 20 minutes), Host from
a clean worktree, a new root whose folders the sandbox prep had not opened (so git in the
sandbox failed and agents worked without it, as unattended sessions now do). Resource samples
every 10 minutes: `scratchpad/live2/resources.csv`.

What it met, in order (times UTC):

- 15:29 planned milestone 1 as 15 parallel tasks (45 acceptance criteria). 19:30 all 15 integrated
  after 2 Host restarts on new code, one budget raise (16M -> program 36M) and two Host freezes
  (the sleep drills above); every interrupted step resumed on its own.
- 19:31-22:06 the whole-project review: 7 episodes (6 ended at the per-episode tool limit and
  continued), ~10M tokens, 2.5 h; found one real defect (README's "Running the tests" still said
  the suite was absent, against the person's guidance that every example must run as written).
- 22:06-22:14 the fix: one worker episode, 0.86M.
- 22:15-23:54 the re-review: 5 more episodes, ~7M, not finished when the program budget ran out.

Why the reviews cost so much: the kept-checks registry cut its list to 24 in string order
(`task-1`, `task-10`, ..., `task-2`) before the runner picked the review's criteria, so the
project review was handed none of task-3..task-9's earlier accepted checks (21 of 45) and
rebuilt their probes in every review. A review now gets the check of every criterion it judges,
in plan order; only a worker's regression list stays capped (see the commit after 57d215f7).

With all 45 checks in hand (00:04-01:27, 4 more episodes) the re-review still did not finish:
the runner stopped it, correctly, after 8 consecutive episodes at the tool limit. Each continued
episode starts again from the top: it re-ran all 45 accepted checks (45 distinct criteria
recorded in one episode, ~60 exec calls), probed further, and hit the limit before replying.
The runner already resolves the latest check per criterion across the whole review session, but
a continued episode is not told which criteria already have passing evidence there, so it never
gets to the verdict. Open: hand a continued review the criteria already recorded as passed for
this candidate in its session, and ask it only for the rest.

Host resources (sampled every 10 min): the Host on 57d215f7 went 149 -> 199 MB working set over
4.7 h of real work (~11 MB/h, flattening in the last hour; the Host before that fix grew ~30 MB/h);
handles steady at ~850; the project database and event log grew 3.3 MB and 21 MB.
