# Evidence and progress after context rollover

A real browser regression run exposed three different failures: old assistant
FAIL interpretations were carried into a handoff as facts, routine observations
were repeatedly narrated, and external commands were unnecessarily wrapped in
Windows shells. These must not be addressed with text keyword filters, forced
turn endings, or automatic command rewriting.

Compaction now requests separate tool facts, assistant interpretations, user
corrections, hypotheses, and unresolved contradictions. The shared compaction
wrapper explicitly identifies a lossy assistant handoff, with retained user
messages and newer corrections taking precedence. This wrapper applies to both
new summaries and recovered checkpoints, including older summaries. Important
verdicts need call IDs and target/environment identity; missing evidence remains
uncertain and can be recovered through read_durable_tool_result.

Every actor request after tool execution receives a small current-turn execution
projection derived directly from durable events: result and failure counts,
assistant text response count, and the latest four result call IDs and execution
outcomes. Counts establish neither functional pass/fail nor task completion.
The projection excludes earlier turns and does not copy narrative conclusions.
It survives history compaction because its source is the event store, not the
summary. The task plan remains a separate projection. There is no progress-message
quota, text classifier, hidden user-message filtering, or additional review model.

The exec contract explains literal argv, when a shell is actually required, and
the Windows PowerShell curl alias. The host continues executing the exact argv;
it must not guess intent and rewrite a command that may mutate external state.

These changes make evidence and communication requirements explicit; they do not
prove that a model will always obey them. Real runs still need evidence audits.
In particular, a trusted click proves a real input event, not a previously absent
style transition: wasClicked=true cannot be interpreted as false. Regression
coverage checks that progress is turn-scoped, never derives test verdicts from
assistant prose, and remains available alongside plans through context rollover.
