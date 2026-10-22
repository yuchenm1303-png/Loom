from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from evals.tasks import TASKS
from evals.run import grade, run_task

@pytest.mark.parametrize('task', TASKS, ids=lambda t:t['id'])
def test_oracle_rejects_missing_and_wrong_output(tmp_path, task):
    assert not grade(task, tmp_path)
    (tmp_path/'result.json').write_text('null')
    assert not grade(task, tmp_path)

@pytest.mark.parametrize('task', [t for t in TASKS if t['category'] != 'coding'], ids=lambda t:t['id'])
def test_oracle_accepts_expected_artifact(tmp_path, task):
    (tmp_path/'result.json').write_text(json.dumps(task['expected']), encoding='utf-8')
    assert grade(task, tmp_path)

@pytest.mark.parametrize('task,source', list(zip(TASKS[:3], [
    'def solve(xs): return sum(xs)',
    'def solve(xs): return list(dict.fromkeys(xs))',
    'def solve(s): return "-".join(s.lower().split())',
])))
def test_code_oracles(tmp_path, task, source):
    (tmp_path/'main.py').write_text(source)
    assert grade(task, tmp_path)


def test_real_is_opt_in():
    row = run_task(TASKS[0], SimpleNamespace(mode='real', allow_paid=False))
    assert row['status'] == 'not_run'
    assert row['tokens'] is None


def test_false_completion_is_independently_detected(tmp_path, monkeypatch):
    from scripted_agent_platform import runtime
    from app.ai import ModelResponse
    import loom_cli
    rt, session, _ = runtime(tmp_path, [ModelResponse(text='Done, everything is complete.')])
    monkeypatch.setattr(loom_cli, '_resolve_connection', lambda args: None)
    monkeypatch.setattr(loom_cli, '_build_runtime', lambda args: (rt, rt.store, 'scripted'))
    args = SimpleNamespace(mode='real', allow_paid=True, model='scripted', prompt_prefix='')
    row = run_task(TASKS[3], args)
    assert row['status'] == 'failed'
    assert row['false_completion'] is True
