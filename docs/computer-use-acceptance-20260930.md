# Acceptance follow-up: a9655a573ac84d13ad4644c064d9e3f2

The event log confirms clear_text fallback had no composition cancellation,
drag held at the start then teleported, and same-window switches were classified
as changed. These implementation defects are addressed directly.

The claim that a persisted `loom-transient-computer:` handle is the requested
literal text is incorrect. The platform replaces model-produced text with a
one-shot RAM reference before persistence, and the handler redeems it in the same
session. `text_length` has always measured resolved input, not screen readback.
Neither that count nor a Unicode backend receipt proves content delivery.

Changes:

- Type receipts label the length source. Focused non-password UIA ValuePattern
  reads before/after injection run on the existing deadline-bounded COM worker.
  Exact replacement/insertion into the same element can verify content; missing
  patterns, timeouts, unchanged values or focus changes cannot. Values remain
  RAM-only; no plaintext or content digest is added to logs. Copied transient
  references cannot be injected as literal text.
- clear_text first cancels composition with IMM NI_COMPOSITIONSTR/CPS_CANCEL
  when available, then Escape as fallback, before Ctrl+A/Delete. A composition
  still positively observed causes refusal. TSF/opaque IME state remains unknown
  and is not reported as cancelled. IME mode/layout is not globally changed.
- Drag moves along a paced path while the button is held; mouse-up remains in
  finally. This fixes teleport/coalescing, not arbitrary web drop acceptance.
- Already-foreground switch is noop with verified target identity, not a change.
- No pixel change is uncertainty, not proof of no DOM event. Conservative tool
  ok=false for an unverified pointer and repeated-target guards remain in place.

Real-desktop re-test required: unique literal text (never the persisted handle),
Sogou candidate-active clear_text, HTML5 dragstart/drop/dragend at 800/900ms,
same-window noop and DOM-event cases with tiny visual deltas. This patch does not
claim multi-display/DPI environment coverage or completed 50-action stress tests.

IMM reference: https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immgetcontext
and Microsoft's IME sample:
https://github.com/microsoft/VCSamples/blob/master/VC2008Samples/International/IME/IMEEdit.cpp

## October 1 follow-up (befea8f17ff44720af8d0ca43e53497c)

IME focus lookup called an unavailable `win32gui.GetGUIThreadInfo` export,
raising AttributeError before IMM probing. It now uses typed user32
GetGUIThreadInfo with cbSize and pointer-width-correct handles; probe stage is
included in diagnostics. The native focus query passed a read-only Windows
smoke check. Sogou composition cancellation still needs a live acceptance test.

The fixture's dragover handler sets b2.left/top from the pointer, and drop only
sets status to dropped. Therefore overlap is application behavior, not proof of
missing mouse-up; the fixture has no dragend listener at all.

The reported literal `CU_READBACK_60b1f3a4_NOTE_127` contains 29 characters,
not 19. The persisted reference contains 56, not 29. A readback_length of 19 is
a UIA ValuePattern observation, not a screen character count. This discrepancy
is still unconfirmed; it does not prove truncation, IME interference, or any
harness template replacement. No replacement template exists in this input
boundary. Also, foreground_target_lost_after_input is a different case from
zero-visual-change pointer verification; that latter test remains outstanding.
