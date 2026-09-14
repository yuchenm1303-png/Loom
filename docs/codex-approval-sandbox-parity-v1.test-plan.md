# Approval / sandbox parity validation plan

Focused tests for this slice:

```text
python -m pytest -q \
  tests/test_approval_preset_adapter.py \
  tests/test_command_approval.py \
  tests/test_approval_action_cache.py \
  tests/test_runtime_approval_cache.py \
  tests/test_orchestrator_approval_parity.py \
  tests/test_sandbox_denial_classifier.py \
  tests/test_sandbox_additional_permissions.py \
  tests/test_sandbox_escalation_runtime.py \
  tests/test_action_bound_sandbox_escalation.py \
  tests/test_approval_cancellation.py \
  tests/test_approval_review_boundary.py \
  tests/test_execution_action.py \
  tests/test_apply_patch_action_identity.py \
  tests/test_apply_patch_action_runtime.py \
  tests/test_sandbox_runtime.py
```

The repository `CI` workflow additionally runs the full Python test suite, React
desktop typecheck/build, Windows context/PTY/MXC/computer smoke jobs, MCP adapter
smoke, and browser adapter smoke on pull requests.

## Current validation status

The latest workflow for this branch did not reach a runner: every reported job has
an empty `steps` array and `runner_id = 0`. Under this project's acceptance rules,
that is not evidence that pytest, builds, or smoke tests failed. The files above
are committed as executable contract tests, but this slice does not claim a green
dynamic run until a runner actually starts and executes them.
