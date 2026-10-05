# Browser execution interface audit

This change fixes missing interface evidence; it does not add a completion classifier,
keyword matching, a retry threshold, or automatic functional PASS inference.

## Observed run

Session 13d05daf-00a4-4b14-a54d-9a21891e5e7d attempted file and data navigation
before using HTTP. Status described policy enforcement but did not enumerate schemes
or report the configured private-network flag. The navigation contract now exposes
both, with schemes shared with the validator. Network restrictions remain enforced.

Browser state promised visual surface rectangles, but the request projection only
preserved viewport size and discarded page_info.visual_surfaces. The projection now
carries bounded, typed rectangles and redacted labels in the latest ephemeral tool
observation. Arbitrary page metadata is excluded, and durable history still keeps
compact receipts. Effect comparison uses the same projected geometry as the actor.

Verification capabilities identify browser_state, browser_screenshot, and deferred
browser_eval via tool_search. Execution success and functional acceptance remain
separate. No absence of an error can be promoted into functional PASS by the host.

## Context findings and limits

This run already persisted compact browser receipts and attached only the latest
full DOM. History also contained a user image, recalled old reports, data URL text,
and verbose model commentary. Deleting those by keywords or a message-count rule
would lose user requirements and evidence. This patch does not invent such a rule.
No claim is made that these interface fixes guarantee concise model behavior or
complete the ongoing acceptance run. Validate with the next run's setup time,
call correctness, evidence-linked verdicts, and commentary volume.

## Resource recovery and structured evidence

Browser manager retains bounded owner-scoped resource tombstones. Turn cleanup records
its actual terminal event kind; missing resources return not_executed plus lifecycle
metadata. New turns receive the durable prior-turn release receipt. A Host restart
without a local tombstone is reported as unknown, never guessed as idle timeout.
Provider transport errors retain typed HTTP status when available. Failed turns retain
history and record a new-turn recovery boundary; these changes preserve the existing provider retry contract and do not reinterpret
rate limits as task completion or continue a single-session test across a failed turn.

append_workspace_jsonl accepts object records, validates existing JSONL, serializes
Unicode/newlines, writes via a verified same-directory temporary file, and replaces
atomically. Same-Host calls are serialized. External shell writers must not concurrently
modify the same file. Invalid existing evidence is preserved and diagnosed, not repaired
or reinterpreted automatically. The receipt confirms file integrity, not a verdict's truth.

Plan status describes workflow execution; optional outcome separately represents passed,
failed, interrupted, not_covered, and not_assessed. UI displays an explicit adverse outcome
rather than a green test-pass implication. Models remain responsible for supplying accurate
outcomes grounded in evidence; no prose matching or extra completion reviewer was added.
