# Build mode conversation access

The build hub's old access switch was local draft state, forwarded only to
`projectWeave/start`. Messages from the same hub still used the conversation's
persisted permission. This could show “Full access” while ordinary messages ran
under `approval` / `unless-trusted` and repeatedly asked for command approval.

The production hub now reads the conversation's actual permission and changes it
through the same `thread/set_permission_mode` path as ordinary chat. It displays
Full access only after the Host confirms the change, reports failure without
changing the label, and blocks sending/starting a proposal while the RPC is pending.
Keyboard submission uses the same pending guard. An approval/read-only conversation
shows its actual mode rather than being presented as sandbox/full-access draft state.

Active turns, including approval waits, retain their captured permission boundary.
The access button asks the person to stop the current turn before changing it.
No approval is automatically granted. Once idle, the person can select Full access
and continue. The backend Full access preset already uses approval policy `never`;
its policy is unchanged by this fix.

New builds use the confirmed conversation's full-access/sandbox choice. An already
started build retains its immutable task permissions. In a live build the control
is labelled Chat; a separate Tasks indicator appears when the task and conversation
access differ. Ordinary chat and the build hub share the same persisted setting.

Validation uses the real App with an isolated Host fixture: initially approval,
delayed and failed permission changes, keyboard/send races, active approval locking,
ordinary-chat round trip, separate live task access and a new proposal's permission
payload. Both themes, TypeScript, the renderer build and existing permission-policy
and navigation checks are covered. No paid model or actual cleanup command is run.
