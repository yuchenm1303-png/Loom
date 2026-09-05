# Web UI live feedback

Loom's local Web UI shows immediate execution feedback after a prompt is submitted so a slow model request does not look like a frozen interface.

This is **live runtime status**, not token-level model streaming.

## What the UI shows

As soon as a turn is accepted, the conversation displays a temporary Loom working row and starts an elapsed timer. The text is then updated from durable runtime events already produced by Loom, for example:

- waiting for the configured model
- running a Loom tool
- running a nested Code Mode tool
- a tool finished and Loom is continuing
- a workspace process is running or finished
- the workspace changed and Loom is checking the result
- waiting for user approval

The right-side Runtime Activity panel continues to show the underlying event history.

## Polling

The browser polls the localhost service adaptively:

- about 450 ms while a turn or approval is active
- about 1.1 seconds while idle

The polling loop is non-overlapping: the next poll is scheduled only after the current refresh finishes.

## Reasoning privacy

Live feedback is derived from runtime state and tool events. Loom does not expose private model chain-of-thought. It reports useful execution state such as model waits, tool calls, process activity, approvals, and completion.

## Streaming scope

The current provider/runtime contract still returns each model response as a completed response. Adding genuine token streaming would require a separate provider/runtime streaming contract and event path. This UI feature deliberately does not pretend otherwise.
