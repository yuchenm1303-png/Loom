"""A local Loom for the qa build: the real account service and gateway, and a stand-in Host.

    python android/qa/stack.py <workdir>

The stand-in signs up, connects as a computer and answers the phone. Tokens last 60 seconds so
expiry is reached in a test. Commands arrive as files in <workdir>: `pair` issues a code and
writes it to `code.txt`, approving the phone when it claims it. `db.json` is refreshed with the
phone sessions and `host.log` records what the phone asked for.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "http://127.0.0.1:18787/v1"
RELAY = "ws://127.0.0.1:18790/api/ws/device"
DEVICE = {"id": "qa-computer-0000000000000000", "name": "QA computer", "platform": "win32", "version": "qa",
          "hostVersion": "qa", "hostMode": "background", "hostProtocol": 99, "bootstrapProtocol": 1, "mobileProtocol": 1}


def post(path: str, body: dict, token: str = "") -> dict:
    request = urllib.request.Request(ACCOUNT + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def start_services(work: Path) -> list[subprocess.Popen]:
    env = {**os.environ, "PYTHONUTF8": "1", "LOOM_ACCOUNT_ACCESS_TTL": "60", "LOOM_ACCOUNT_API_BASE_URL": ACCOUNT,
           "LOOM_WEB_STATIC_DIR": str(work), "LOOM_ACCOUNT_QUIET": "0", "PYTHONPATH": f"{ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}
    account = subprocess.Popen([sys.executable, "-m", "services.loom_account.server", "--port", "18787", "--db", str(work / "accounts.db")],
                               cwd=ROOT, env=env, stdout=(work / "account.log").open("w"), stderr=subprocess.STDOUT)
    gateway = subprocess.Popen([sys.executable, "-m", "uvicorn", "services.loom_web_gateway.app:app", "--host", "127.0.0.1", "--port", "18790",
                                "--log-level", "info"], cwd=ROOT, env=env, stdout=(work / "gateway.log").open("w"), stderr=subprocess.STDOUT)
    return [account, gateway]


def dump_sessions(work: Path) -> None:
    db = sqlite3.connect(work / "accounts.db")
    rows = db.execute("SELECT client_name, created_at, revoked_at FROM sessions WHERE client_kind = 'mobile' ORDER BY created_at").fetchall()
    db.close()
    (work / "db.json").write_text(json.dumps([{"name": a, "created": b, "revoked": c} for a, b, c in rows]), encoding="utf-8")


class Host:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.tokens = post("/auth/register", {"email": f"qa-{int(time.time())}@example.com", "password": "correct-horse-battery-staple"})

    def log(self, line: str) -> None:
        with (self.work / "host.log").open("a", encoding="utf-8") as file:
            file.write(f"{time.strftime('%H:%M:%S')} {line}\n")

    def access(self) -> str:
        self.tokens = post("/auth/refresh", {"refresh_token": self.tokens["refresh_token"]})
        return self.tokens["access_token"]

    def pair(self) -> None:
        access = self.access()
        issued = post("/auth/mobile-pair/issue", {"device_id": DEVICE["id"], "device_name": DEVICE["name"]}, access)
        (self.work / "code.txt").write_text(f"loom://pair?v=1&t={issued['pairing_ticket']}&d={DEVICE['id']}&n=QA", encoding="utf-8")
        for _ in range(120):
            time.sleep(0.5)
            state = post("/auth/mobile-pair/status", {"pair_id": issued["pair_id"]}, access)
            if state["state"] == "claimed":
                post("/auth/mobile-pair/decide", {"pair_id": issued["pair_id"], "approve": True}, access)
                self.log("approved")
                return

    def answer(self, frame: dict):
        operation, args = frame.get("operation"), frame.get("args") or []
        self.log(f"invoke {operation} {args[0] if operation == 'call' and args else ''}".strip())
        if operation == "connect":
            return {"protocolVersion": 1, "serverInfo": {"name": "qa", "version": "0"}, "capabilities": {},
                    "runtime": {"model": "qa", "defaultWorkspace": "/qa", "defaultPermissionMode": "approval",
                                "permissionModes": ["read-only", "approval", "workspace", "full-access"]}}
        if operation == "listModels":
            profile = {"selection": "qa", "id": "qa", "kind": "builtin", "name": "QA", "adapter": "openai", "baseUrl": "", "model": "qa", "configured": True}
            return {"primary": profile, "profiles": [profile], "activeModelId": "qa", "current": {**profile, "provider": "openai"}, "recentModels": []}
        if operation == "call" and args and args[0] == "thread/list":
            return {"threads": [], "counts": {"active": 0, "archived": 0, "all": 0}}
        if operation == "call" and args and args[0] == "project/list":
            return {"projects": [], "unfiledThreadCount": 0}
        return {}

    async def relay(self) -> None:
        while True:
            try:
                async with websockets.connect(RELAY, additional_headers={"Authorization": f"Bearer {self.access()}"}, max_size=None) as socket:
                    await socket.send(json.dumps({"type": "device_hello", "device": DEVICE}))
                    self.log("computer online")
                    pinger = asyncio.create_task(self.ping(socket))
                    try:
                        async for raw in socket:
                            frame = json.loads(raw)
                            if frame.get("type") == "invoke":
                                await socket.send(json.dumps({"type": "invoke_result", "browserId": frame["browserId"], "id": frame["id"], "result": self.answer(frame)}))
                    finally:
                        pinger.cancel()
            except (OSError, websockets.WebSocketException, urllib.error.URLError) as error:
                self.log(f"computer offline {type(error).__name__}")
                await asyncio.sleep(2)

    @staticmethod
    async def ping(socket) -> None:
        while True:
            await asyncio.sleep(20)
            await socket.send(json.dumps({"type": "ping"}))

    async def commands(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            dump_sessions(self.work)
            order = self.work / "pair"
            if order.exists():
                order.unlink()
                await asyncio.to_thread(self.pair)


async def main(work: Path) -> None:
    host = Host(work)
    await asyncio.gather(host.relay(), host.commands())


if __name__ == "__main__":
    folder = Path(sys.argv[1]).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text("<html></html>", encoding="utf-8")
    processes = start_services(folder)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:18787/healthz", timeout=1)
                urllib.request.urlopen("http://127.0.0.1:18790/api/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        asyncio.run(main(folder))
    finally:
        for process in processes:
            process.terminate()
