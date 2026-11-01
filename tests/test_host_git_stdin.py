"""Git run from the Host must not inherit its stdin.

The Host's stdin is the desktop's JSON-RPC pipe and its main thread always has a read pending
on it. On Windows a git that inherits that handle blocks at startup until the next request
arrives, so projectRun/overview timed out and persistent-project mode showed "Could not read
this project from the Host".
"""
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent("""
    import sys, threading
    sys.path.insert(0, sys.argv[1])
    from app.project_runner.review import git
    def run():
        print(git(sys.argv[2], "rev-parse", "--show-toplevel") and "answered", flush=True)
    threading.Thread(target=run, daemon=True).start()
    sys.stdin.readline()  # the Host's main thread: a read pending on the request pipe
""")


def test_project_git_answers_while_the_host_reads_its_stdin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, stdin=subprocess.DEVNULL)
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        answer = []
        reader = threading.Thread(target=lambda: answer.append(child.stdout.readline().strip()), daemon=True)
        reader.start()
        reader.join(10)
        assert answer == ["answered"]
    finally:
        child.stdin.close()
        child.wait(10)
