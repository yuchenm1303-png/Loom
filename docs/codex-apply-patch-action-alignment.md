# Typed apply-patch action alignment

This branch is stacked on the typed execution-action work and extends the same approval invariant to `apply_patch`: the durable approval key should describe the requested file-edit action, not merely the generic tool definition.

## Action identity

`ApplyPatchActionIdentity` is a frozen, secret-minimized description of one model-originated `apply_patch` request.

For structured `changes` input it:

- preserves operation order;
- canonicalizes action spelling;
- resolves `path` and `move_to` against the frozen workspace and rejects escapes;
- canonicalizes JSON key ordering before computing a request digest;
- records only the canonical touched paths plus the request digest in the binding payload, not file contents.

For Codex-style text `patch` input it:

- enforces the existing maximum patch size and Begin/End envelope;
- canonicalizes line endings using the same `splitlines()` semantics as Loom's text-patch parser;
- extracts declared Add/Delete/Update/Move paths and resolves them against the workspace;
- records only canonical touched paths plus a SHA-256 request digest, not the patch body.

`call_id` remains instance metadata and is excluded from the semantic digest.

## Why live file preimages are not part of the action key

An early version of this slice derived identity from `ApplyPatchRuntime.plan()`. That was intentionally removed before the branch was finalized.

A model response can contain more than one queued tool call. An earlier legitimate call may change the workspace before a later `apply_patch` executes. Binding the later call to the file bytes that happened to exist at model-sampling time would therefore classify a valid sequential batch as approval tampering.

The action identity instead binds what the model requested: transformation payload and canonical target paths. Loom's existing `ApplyPatchRuntime.plan()/apply()` remains responsible for filesystem preconditions, expected-text checks, atomic replacement and preimage validation at execution time.

This keeps two different invariants separate:

1. **approval integrity** — the queued patch request cannot silently change after it was sampled/approved;
2. **filesystem consistency** — the patch runtime decides whether that unchanged request is still valid against the current workspace.

## Generic action binding

`action_binding_digest()` is now action-type agnostic. It combines the frozen Step/tool binding with `action.kind` and `action.digest()` for any typed execution action.

Existing exec semantics are unchanged: `ExecActionIdentity.kind` returns the same `exec_command` value that the composite binding previously hard-coded. Tools with no typed action still return the legacy generic binding exactly.

Because Core already switched all pending-binding lifecycle points to `action_binding_digest()` in the parent branch, `apply_patch` automatically receives the same creation, approval-resume and pre-execution validation without any new Core or SandboxRuntime state.

## Test contracts

The branch covers:

- patch bodies/file contents are not copied into the action binding payload;
- call id, structured-object key order and equivalent workspace path spelling do not change structured action identity;
- structured content changes do change identity;
- LF and CRLF spellings of the same text patch share one identity;
- workspace file-content drift does not mutate the approval identity by itself;
- the generic execution-action factory returns the typed patch action;
- action binding changes when the approved patch request changes;
- an unchanged approved patch executes normally;
- queued patch-argument drift is rejected before any file write;
- workspace preimage drift is handled by the patch runtime rather than misclassified as approval tampering.

## Scope

This slice does not change the atomic patch engine or the user-visible patch format. It also does not introduce session-wide approval caching. It only gives one concrete queued patch request a canonical action identity on the already-existing pending-binding lifecycle.
