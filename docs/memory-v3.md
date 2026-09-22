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

If no lexical match is found, Loom falls back to at most two high-signal records so durable preferences or key project decisions remain available without recreating the old top-N-memories-every-turn behavior.

## Search

MemoryStore.search_hits returns records together with score and reasons. Ranking considers phrase/term overlap, workspace locality, importance, corroborating source count, prior useful retrievals, confidence, and freshness.

Explicit memory search requires a real query match so unrelated high-importance memories do not leak into results.

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
