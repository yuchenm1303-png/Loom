# Window06 sealed

Window06 is sealed on the corrected three-way recovery contract in `docs/codex-recovery-session-parity-v1.md`:

- live turn: rejoin;
- safely suspended unfinished turn: recover the same logical turn through Window01;
- unclean process loss: fail closed through Window06 interruption finalization.

Final Codex audit baseline: `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.
