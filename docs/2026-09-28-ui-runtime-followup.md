# UI and runtime follow-up — 2026-09-28

## Repository data

The local usage fixture entered history in `1e4ae924` and was removed from the
current tree in `29b1a9d4`. Its JSON contains aggregate usage, dates, model/tool
names and daily totals; it does not contain conversation messages. This repair
retains existing public history, following the proposed keep-history option.
It does not claim to remove historical copies or change repository visibility.

The tracked `scratch/` probes, cleanup scripts and output files introduced in
`e038e48d` are removed. `/scratch/` and `/desktop-react/design/*.local.json` are
ignored to prevent accidental reintroduction through ordinary `git add`.

## Reasoning capsule

The reasoning container had `margin-top: -5px` inside a folding container with
only 3px top padding and `overflow: hidden`. Removing the negative margin keeps
the capsule inside the clip without disabling folding. A regression contract
guards the nonnegative margin.

## Model attribution

Request, accepted-response and rejected-response events now record `model`,
`provider` and `profile_id` from the immutable Step request snapshot. No complete
profile or credential reference is copied into these event attribution fields.
Retries and steering rejection retain the same sampled identity. The profile
aggregator already prefers event model metadata over session metadata.

Old events cannot be accurately reconstructed if they never recorded a model.
Existing historical session-model attribution remains approximate; no old
events are backfilled with today's configuration. Platforms without a model
snapshot record empty model/provider values rather than inventing an identity.

## Motion contracts

The old tests pinned a pseudo-element sweep, 5.6s timing and bead-only task-row
entrances. The current design uses one decorative sweep layer, pauses it outside
execution, fades its wrapper on completion, and keeps task commands visible from
their first frame. Thinking capsules retain bead unrolling. Stickers remain
visible while making a small spring movement.

Contracts now cover these behaviors, including no animated text transforms,
live-only entrances, single-layer sheen, idle time and reduced-motion handling.
The model attribution test changes configuration during execution and verifies
both event pairs and usage aggregation retain the correct per-call models.
