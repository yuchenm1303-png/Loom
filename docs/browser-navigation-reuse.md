# Current-browser navigation and existing-tab adoption

Tab reuse does not cancel navigation intent. The extension resolves an available
tab separately from the URL to apply to it. Tabs owned by other controllers are
excluded throughout selection and checked again when claimed.

Selection order:
1. An exact URL match, including query and fragment, across browser windows.
2. The current target if its origin/path matches.
3. A single available same-origin/path candidate.
4. Multiple unrelated same-path candidates are ambiguous: create a new work tab
   rather than guess an instance by query length or overwrite one arbitrarily.

For a reused same-path page:
- Explicit query parameters replace the old query (region/instance changes work).
- An explicit fragment is applied, including an empty `#` to clear an anchor.
- When query is omitted, preserve the existing query for live-session adoption.
- A bare same-path URL preserves existing query and fragment. Use explicit `?`
  to clear the query, or `new_tab=true` for a clean navigation without adoption.
- An explicit query without a fragment drops the previous fragment.
- `new_tab=true` always creates a tab, even if an exact destination exists.

The returned URL is the observed page URL, not evidence that a requested business
operation succeeded. Server redirects remain possible. These rules are generic;
there are no Tencent/OrcaTerm host-specific exceptions.

Regression: `tests/browser_extension_concurrency.cjs` exercises the real resolver
and navigation dispatcher with deterministic Chrome API doubles, including query,
fragment, multiple-window selection, controller ownership and forced new tabs.
`tests/browser_native_acceptance.cjs` separately verifies input on disposable Edge.
Neither test operates on the user's logged-in terminals.
