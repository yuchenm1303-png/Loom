"""Verify the policy image's COPY graph in an isolated Python import path."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def test_policy_container_can_import_without_the_repository_on_sys_path(tmp_path):
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "services/loom_model_policy/Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        if not line.startswith("COPY "):
            continue
        _, *sources, target = shlex.split(line)
        destination = tmp_path / target.removeprefix("/app/")
        if target.endswith("/"):
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
        for source in sources:
            shutil.copyfile(root / source, destination / Path(source).name if target.endswith("/") else destination)
    result = subprocess.run([sys.executable, "-I", "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import services.loom_model_policy.server", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
