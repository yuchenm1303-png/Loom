#!/usr/bin/env python3
"""Upload and run a remote shell script via ssh with stdin pipe."""
from __future__ import annotations
import base64
import subprocess
import sys
from pathlib import Path

KEY = r"C:\Users\邹羽宸\.ssh\termrelay_tencent_ed25519"
HOST = "ubuntu@170.106.171.50"


def run(script_path: str) -> int:
    raw = Path(script_path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    b64 = base64.b64encode(raw).decode()
    remote = (
        "set +e; "
        "mkdir -p /tmp/_loom; "
        f"echo {b64} | base64 -d > /tmp/_loom/script.sh; "
        "chmod +x /tmp/_loom/script.sh; "
        "bash /tmp/_loom/script.sh"
    )
    proc = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            "-i", KEY,
            HOST,
            remote,
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    sys.stdout.write(proc.stdout or "")
    if proc.stderr:
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.stdout.write(f"\n[exit={proc.returncode}]\n")
    return proc.returncode


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "scripts/loom_target_deploy.sh"
    raise SystemExit(run(target))