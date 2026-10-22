"""Run with python -m evals.run; no new agent execution loop."""
import argparse
from dataclasses import replace
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from .tasks import TASKS


def grade(task, root):
    try:
        if task['category'] == 'coding':
            code = 'import main as m,json; print(json.dumps(' + task['expression'] + '))'
            p = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0,sys.argv[1]); '+code, str(root)], capture_output=True, text=True, timeout=10)
            actual = json.loads(p.stdout) if p.returncode == 0 else None
        else:
            path = root / 'result.json'
            if path.is_symlink():
                return False
            actual = json.loads(path.read_text(encoding='utf-8-sig'))
        return actual == task['expected']
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False


def run_task(task, args):
    row = dict(id=task['id'], category=task['category'], status='not_run', reason=None,
               seconds=None, tokens=None, interventions=None, false_completion=None)
    if args.mode == 'real' and not args.allow_paid:
        row['reason'] = 'paid model execution requires --allow-paid'
        return row
    with tempfile.TemporaryDirectory(prefix='loom-eval-') as directory:
        root = Path(directory)
        workspace = root / 'workspace'
        workspace.mkdir()
        for name, content in task['files'].items():
            (workspace / name).write_text(content, encoding='utf-8')
        if args.mode == 'oracle':
            # Fixture/oracle checks are deliberately not agent successes.
            row.update(status='oracle_checked', reason='negative oracle rejected untouched fixture' if not grade(task, workspace) else 'invalid fixture')
            return row
        from loom_cli import build_parser, _build_runtime, _resolve_connection
        cli = build_parser().parse_args(['--home', str(root/'state'), '--model', args.model, '--timeout', '60'])
        cli.context_limits = getattr(args, 'context_limits', None)
        try:
            _resolve_connection(cli)
        except SystemExit:
            row['reason'] = 'missing credentials or provider/model configuration'
            return row
        rt = None
        server = None
        start = time.monotonic()
        try:
            rt, store, _ = _build_runtime(cli)
            rt.limits = replace(rt.limits, max_model_steps=20, max_tool_calls=60)
            session = rt.create_session('agent.fast', workspace_dir=workspace, permission_mode='workspace')
            if args.prompt_prefix:
                session.system_prompt += '\n' + args.prompt_prefix
                store.save(session)
            prompt = task['prompt']
            if task['category'] == 'browser':
                handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(workspace))
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                prompt = prompt.format(url=f'http://127.0.0.1:{server.server_port}/index.html')
            result = rt.start_turn(session.session_id, prompt)
            checkpoint_ok = True
            if 'followup' in task:
                checkpoint_ok = json.loads((workspace/'checkpoint.json').read_text(encoding='utf-8-sig')) == task['checkpoint']
                rt.close()
                rt, store, _ = _build_runtime(cli)
                rt.limits = replace(rt.limits, max_model_steps=20, max_tool_calls=60)
                result = rt.start_turn(session.session_id, task['followup'])
            events = list(store.events(session.session_id))
            browser_ok = task['category'] != 'browser' or any(e.kind.value == 'tool_completed' and 'browser' in str(e.data.get('tool','')).lower() for e in events)
            passed = grade(task, workspace) and checkpoint_ok and browser_ok
            completed = result.status.value == 'completed'
            row.update(status='passed' if passed and completed else 'failed',
                       reason=None if passed and completed else 'independent oracle, required execution evidence, or runtime completion failed',
                       interventions=sum(e.kind.value == 'tool_approval_required' for e in events),
                       false_completion=completed and not passed,
                       event_counts={kind:sum(e.kind.value == kind for e in events) for kind in sorted({e.kind.value for e in events})})
            # Zero can mean unavailable provider accounting, never infer usage.
            usage = store.load(session.session_id).usage
            row['tokens'] = usage.total_tokens or None
        except Exception as exc:
            row.update(status='failed', reason=type(exc).__name__)
        finally:
            if rt:
                rt.close()
            if server:
                server.shutdown()
                server.server_close()
            row['seconds'] = round(time.monotonic()-start, 3)
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['oracle','real'], default='oracle')
    p.add_argument('--allow-paid', action='store_true')
    p.add_argument('--model', default='unknown')
    p.add_argument('--prompt-prefix', default='')
    p.add_argument('--context-label', default='runtime-default')
    p.add_argument('--context-limits', type=json.loads)
    p.add_argument('--task', action='append')
    p.add_argument('--output', default='eval-results')
    args = p.parse_args()
    selected = [t for t in TASKS if not args.task or t['id'] in args.task]
    if not selected or (args.task and set(args.task)-{t['id'] for t in TASKS}):
        p.error('unknown task')
    rows = [run_task(t,args) for t in selected]
    attempted = [r for r in rows if r['status'] in ('passed','failed')]
    report = dict(schema_version=1, suite_sha256=hashlib.sha256(Path(__file__).with_name('tasks.py').read_bytes()).hexdigest(),
        revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True)),
        mode=args.mode, model=args.model, prompt_sha256=hashlib.sha256(args.prompt_prefix.encode()).hexdigest(),
        context_strategy=args.context_label, context_limits=args.context_limits,
        success_rate=sum(r['status']=='passed' for r in attempted)/len(attempted) if attempted else None, attempted=len(attempted), tasks=rows)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines = ['# Loom task evaluation', '', f'Mode: {args.mode}; model: {args.model}; success rate: {report["success_rate"] if attempted else "unknown (no real runs)"}', '', 'Null fields mean unknown. Oracle checks are not model results.', '', '| Task | Status | Seconds | Reason |','|---|---|---|---|']
    lines += [f'| {r["id"]} | {r["status"]} | {r["seconds"]} | {r["reason"]} |' for r in rows]
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return int(any(r['status']=='failed' or r['reason']=='invalid fixture' for r in rows))

if __name__ == '__main__':
    raise SystemExit(main())

