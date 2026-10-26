# Memory v3

Loom Memory v3 keeps the durable SQLite provenance model from Memory v2, but changes how long histories are routed into agent context.

## Architecture

Memory is layered instead of treating every MemoryRecord as prompt material:

1. Extraction — completed observable turns are converted into durable candidates.
2. Canonical records — exact duplicates consolidate into one record with evidence and source counts.
3. Semantic reconciliation — conservative keep, update, merge, and supersede operations reconcile related active records.
4. Knowledge index — each workspace has a compact, high-signal routing summary grouped by memory category.
5. Task routing — the current user request is matched against active memory; only a small set of relevant records is injected.
6. Deep read — search_memory and read_memory retrieve details and provenance only when needed.
7. Lifecycle — low-importance, single-source, unused project/fact records may be archived after a long inactive period. Archive is reversible and never applies automatically to decisions, constraints, or preferences.
8. Usage telemetry — automatic routing, searches, and provenance reads are recorded so relevance and staleness can be inspected.
9. Skill promotion candidates — repeatedly corroborated, high-importance project/decision memories can be surfaced as candidates for a reusable repository Skill. Loom never writes or installs a Skill automatically from background memory work.

## Authority

Memory is advisory evidence. It is never system, developer, permission, or runtime authority.

Current user instructions, the live tool harness, current runtime state, and observed tool results take precedence. Operational restrictions remembered from old conversations must be verified against current tools/runtime.

## Context budget

Every model request receives a LOOM_MEMORY_CONTEXT v3 system message containing a compact LOOM_MEMORY_INDEX v3 routing summary and, at most, a few records routed from the current user request. The full memory database is never dumped into prompt context.

If no relevant match is found, Loom falls back only to preferences among at most two high-signal records. Project facts are not injected simply because they are important. Automatic context contains index counts and categories; the full index previews remain available through memory_index and the project UI.

## Search

MemoryStore.search_hits returns records together with score and reasons. Local BM25 ranking uses corpus term frequency, Chinese bigrams and bilingual project vocabulary. Domains and paths remain exact identifiers instead of being counted repeatedly as host fragments. Relevance dominates bounded metadata tie breakers. Retrieval frequency is not treated as evidence of usefulness, and retrieval timestamps do not make an old fact fresh. Active records are searched without the previous 500-record cutoff.

Explicit memory search requires a real query match so unrelated high-importance memories do not leak into results.

## Reliable extraction and work handoffs (2026-10-26)

Completed, failed, cancelled, interrupted and limit-reached turns are eligible for extraction. Failed work is historical evidence, never proof that a feature passed tests or was deployed. The extraction prompt explicitly distinguishes observed progress from completion claims.

The worker selects a bounded prefix of eligible events and advances its cursor only to that prefix's last event. Remaining events are rescheduled immediately. Large individual observations use marked head/tail excerpts; the full source event remains in the event log. Event-range replay reuses an already persisted extraction, and writes are idempotent at the SQLite transaction boundary. This covers a crash between storing candidates and advancing the thread cursor.

Separately, each terminal turn writes a workspace-scoped handoff without a model request. Handoffs contain up to four recent user messages from that turn, observed changed paths, bounded redacted evidence, the actual terminal outcome, source session/turn/event IDs, and the observation timestamp. A failure in this auxiliary write cannot prevent committing the foreground outcome. The startup backlog retries capture for the latest terminal turn of each session before model extraction; this also imports a useful recent handoff from older hosts. It does not bulk-import every historical turn.

Related handoffs from other sessions are routed into new conversations even when no canonical facts have been extracted. search_memory returns both canonical memories and matching handoff previews; read_memory accepts the returned `handoff:<session_id>:<turn_id>` identifier for the stored evidence excerpt. Workspace access checks apply to both. Memory disablement gates capture and model access. Auto-extract controls background model work; deterministic handoff capture remains enabled while memory itself is enabled.

Handoffs are historical observations, not an authoritative current-state snapshot. The model is told to recheck current files and deployment. Existing semantic reconciliation retains its validated update/supersede history, with bilingual matching for related candidates. No new vector service, model download, graph database or credential is required. The bilingual vocabulary is finite: this release does not provide general embedding-based semantic search. Handoffs are separate from canonical memory records. The runtime forget operation accepts handoff IDs. Deleting a canonical memory also removes handoffs from its evidence turns; tombstones prevent startup backfill from resurrecting those excerpts. Raw conversation logs retain their existing lifecycle.

Regression coverage in tests/test_memory_upgrade.py includes interrupted work, cross-conversation injection without extraction, workspace isolation, secret redaction, disabled access, long-event batches, crash recovery, duplicate source prevention, and bilingual homepage recall against stale deployment and frequently retrieved noise. Existing memory/runtime/storage suites cover compatibility and semantic mutation validation.

## Lifecycle

Automatic archival is deliberately conservative. By default a record is eligible only when all of the following are true:

- category is fact or project;
- importance is 1–2;
- there is at most one source;
- it has never been used;
- it has never been semantically verified;
- it has been inactive for at least 180 days.

Archived memories do not participate in automatic context or model memory tools. They remain visible in Project Memory and can be restored. Repeated new evidence for the same fingerprint automatically reactivates the record.

Usage telemetry is pruned so the event log does not grow without bound.

## Project UI

Project Details → Project Memory exposes active/archived counts, category counts, the compact knowledge index, search, provenance, recent usage routes, manual archive/restore, permanent delete, and stable Skill-promotion candidate counts.

Global memory remains managed from Settings → Memory.

## Model tools

Memory v3 exposes memory_index, search_memory, read_memory, memory_skill_candidates, and memory_status.

memory_skill_candidates is discovery-only. Converting a candidate into .agents/skills/<name>/SKILL.md remains an explicit foreground action and still obeys Loom filesystem and permission policies.
