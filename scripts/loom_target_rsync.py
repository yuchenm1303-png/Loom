#!/usr/bin/env python3
"""Sync only the Loom Web Gateway source + the desktop-react frontend to the server.

We deliberately do NOT touch /opt/loom-account/services/loom_account (live account
service) or /opt/termrelay (TermRelay production). Only files under services/loom_web_gateway
and desktop-react are uploaded.

Usage: python scripts/loom_target_rsync.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

KEY = r"C:\Users\邹羽宸\.ssh\termrelay_tencent_ed25519"
HOST = "ubuntu@170.106.171.50"
REMOTE_DEST = "/opt/loom-account/services/loom_web_gateway"
WORKSPACE = Path(".")

EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", "dist", "build",
    "artifacts", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", "out", "target", "release",
    ".turbo", ".parcel-cache",
}
EXCLUDE_FILES = {".DS_Store", ".env.local", "package-lock.json"}
EXCLUDE_SUFFIXES = (".pyc", ".pyd", ".so", ".dll", ".class", ".log", ".bak")


def select_files() -> list[Path]:
    out: list[Path] = []
    services_root = WORKSPACE / "services" / "loom_web_gateway"
    desktop_root = WORKSPACE / "desktop-react"

    for root_path in (services_root, desktop_root):
        if not root_path.exists():
            print(f"[skip-missing] {root_path}")
            continue
        for root, dirs, files in os.walk(root_path):
            rp = Path(root)
            # mutate to prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for d in dirs:
                out.append(rp / d)
            for f in files:
                fp = rp / f
                if fp.name in EXCLUDE_FILES:
                    continue
                if fp.name.endswith(EXCLUDE_SUFFIXES):
                    continue
                if fp.stat().st_size > 200 * 1024 * 1024:
                    print(f"  skip large: {fp}")
                    continue
                out.append(fp)

    return out


def stream_tar(paths: list[Path]) -> bytes:
    buf = tempfile.SpooledTemporaryFile(max_size=512 * 1024 * 1024)
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in paths:
            # Keep full relative path so the tarball has
            # services/loom_web_gateway/... and desktop-react/...
            rel = p.relative_to(WORKSPACE).as_posix()
            tf.add(str(p), arcname=rel, recursive=False)
    buf.seek(0)
    return buf.read()


def run_remote_tar(tar_bytes: bytes) -> tuple[int, str, str]:
    remote = (
        "set -e; "
        "cd /tmp; "
        "mkdir -p _loom_sync; "
        "cd _loom_sync; "
        "rm -rf services desktop-react; "
        "tar -xzf -; "
        # Move into the canonical destination for the gateway.
        "rm -rf /opt/loom-account/services/loom_web_gateway; "
        "mkdir -p /opt/loom-account/services/loom_web_gateway; "
        "cp -a services/loom_web_gateway/. /opt/loom-account/services/loom_web_gateway/; "
        "rm -rf /opt/loom-account/desktop-react; "
        "cp -a desktop-react /opt/loom-account/desktop-react; "
        "echo SYNC_OK; "
        "ls /opt/loom-account/services/loom_web_gateway | head; "
        "ls /opt/loom-account/desktop-react | head; "
        "wc -c /opt/loom-account/services/loom_web_gateway/Dockerfile /opt/loom-account/services/loom_web_gateway/app.py"
    )
    ssh = subprocess.Popen(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            "-i", KEY,
            HOST,
            remote,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = ssh.communicate(input=tar_bytes, timeout=1200)
    return ssh.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def main() -> int:
    paths = select_files()
    print(f"[select] {len(paths)} entries")
    tar_bytes = stream_tar(paths)
    print(f"[tar] {len(tar_bytes)} bytes")
    code, out, err = run_remote_tar(tar_bytes)
    sys.stdout.write(out)
    if err.strip():
        sys.stdout.write("\n[stderr]\n" + err)
    sys.stdout.write(f"\n[exit={code}]\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())