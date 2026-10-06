# Turn completion

This document is superseded by [Turn completion lifecycle](turn-completion-lifecycle.md).
The former independent `loom_turn_stop_decision` reviewer, its retry loop and
continuation projection have been removed. Historical `turn_stop_requested` and
`turn_stop_checked` event names remain readable for old logs; production does not
emit new assessments. Delivery is governed by native provider/tool/pending-input
state. Task acceptance is recorded separately by plans and evidence-backed checks.
