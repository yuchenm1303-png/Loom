# Web UI live feedback

Loom's local Web UI shows immediate execution feedback after a prompt is submitted so a slow model/tool request does not look frozen.

Phase 2.2 adds genuine public assistant-text provider streaming to the earlier runtime-status feedback.

## What the UI shows

As soon as a turn is accepted, the conversation starts an elapsed working state. Depending on the active step it can show:

- partial assistant text from the real provider stream;
- waiting for the configured model;
- running a Loom tool or nested Code Mode tool;
- tool/process completion and continuation;
- workspace diff activity;
- waiting for user approval.

The right-side Runtime Activity panel continues to show durable execution events.

## Browser delivery

The Web UI remains a debug/fallback client and uses adaptive localhost polling:

- about 450 ms while a turn or approval is active;
- about 1.1 seconds while idle.

The next poll is scheduled only after the previous refresh completes. During a streamed model step the server snapshot contains the public assistant text accumulated from provider chunks so far, so several small provider chunks can be visually coalesced into one browser refresh.

Rich clients using `loom-app-server` receive provider-backed `item/delta` notifications directly and do not depend on this Web polling mechanism.

## Durable boundary

Partial assistant text is transient presentation state. It is not added to `session.json`, canonical history, or `events.jsonl`.

Only the reconstructed final ModelResponse becomes the durable assistant message and `MODEL_RESPONSE`. If the stream fails or a UI disconnects, Loom can fail/recover the Turn without a half-written canonical assistant message.

## Reasoning privacy

Loom streams public assistant content and observable tool/process state only. Provider hidden reasoning fields and private model chain-of-thought are deliberately not exposed as stream events.
