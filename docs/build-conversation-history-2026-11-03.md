# Build conversation history

The hub's history is a navigation surface for user conversations. Execution episodes remain in the project's execution details.

- `projectWeave/history` reads lightweight session overviews and a snapshot of the persistent project store. It does not resume sessions, read transcripts or invoke a model.
- Every recorded episode session is excluded, including completed workers and reviewers with no conversation-parent metadata. Archived and child conversations are excluded too.
- Records include durable Weave conversations, project source conversations and renderer-provided draft-mode IDs. This query is independent of the sidebar's loaded page and has no 30-record cutoff.
- Project identity comes from the shared project registry. Titles preserve manual names, otherwise prefer the build objective. Empty titles use the renderer's translated fallback.
- A completed milestone is not a completed program. Status comes from program/run controls and approvals; chat activity is a separate “Replying” indicator. Task counts identify their milestone when applicable.
- The card defaults to the current project, supports all-project search over titles/objectives/project names/folders, and shows five recent entries with the current conversation pinned first. The scrollable native dialog searches and exposes the entire matching collection.
- New conversation explicitly inherits the current conversation's project, even while viewing all-project results. Opening a history entry selects Build mode and handles failure without an unhandled rejection. Repeated row activation is guarded.
- The dialog uses native focus containment, Escape, backdrop dismissal and responsive bounds. Reads refresh every 12 seconds while visible, and on window focus. Failed reads retain existing results with a retry notice; stale effect responses are ignored.

Validation: backend history/ownership and production metadata tests, real browser interaction in both themes (including 85 records, a target beyond the old cutoff, open failure/retry, current pinning and a narrow viewport), project navigation and layout regressions, TypeScript and production renderer build. Scripted runtime tests use no paid provider requests. Installed releases need to include this commit before showing these changes.
