#!/usr/bin/env python3
"""Probe VM-0-2-ubuntu via SSH (no shell, no quoting pain).

Usage: python scripts/loom_target_probe.py
"""
from __future__ import annotations
import base64
import shlex
import subprocess
import sys
from pathlib import Path

KEY = r"C:\Users\邹羽宸\.ssh\termrelay_tencent_ed25519"
HOST = "ubuntu@170.106.171.50"
SCRIPT = Path("scripts/probe_loom_target.sh")


def run_remote(b64: str) -> tuple[int, str, str]:
    # remote: echo <b64> | base64 -d > /tmp/probe.sh && chmod +x /tmp/probe.sh && bash /tmp/probe.sh
    remote = f"echo {b64} | base64 -d > /tmp/probe.sh && chmod +x /tmp/probe.sh && bash /tmp/probe.sh"
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-i", KEY,
        HOST,
        remote,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=False)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def main() -> int:
    raw = SCRIPT.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    b64 = base64.b64encode(raw).decode()
    code, out, err = run_remote(b64)
    sys.stdout.write(out)
    if err.strip():
        sys.stdout.write("\n[stderr]\n" + err)
    sys.stdout.write(f"\n[exit={code}]\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())