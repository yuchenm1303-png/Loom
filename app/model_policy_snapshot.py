"""Short-lived, account-scoped model permissions refreshed off the RPC queue."""
from __future__ import annotations

import json
import threading
import time
import urllib.request


class ModelPolicySnapshot:
    def __init__(self, url: str, credential: str, *, fetch=None, clock=time.monotonic, start=True):
        self.url, self.credential = url, credential
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.allowed: frozenset[str] = frozenset()
        self.expires = 0.0
        self.error = "Model permissions are synchronizing. Retry shortly."
        if start:
            threading.Thread(target=self._loop, daemon=True, name="loom-model-permissions").start()

    def _fetch(self, credential: str) -> dict:
        request = urllib.request.Request(self.url, headers={
            "Authorization": "Bearer " + credential, "Accept": "application/json",
            "User-Agent": "LoomAppServer/1",
        })
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def refresh(self) -> None:
        with self.lock:
            credential = self.credential
        started = self.clock()
        try:
            payload = self.fetch(credential)
            access = payload.get("access") if isinstance(payload, dict) else None
            if not isinstance(access, dict) or not isinstance(access.get("models"), list):
                raise ValueError("Invalid model permission snapshot")
            allowed = frozenset(str(model) for model in access["models"]) if access.get("enabled", True) else frozenset()
            with self.lock:
                if credential != self.credential:
                    return
                self.allowed, self.expires, self.error = allowed, started + 10, ""
        except Exception:
            with self.lock:
                if credential != self.credential:
                    return
                # A failed refresh must not extend an existing permission lease.
                self.error = "Model permissions could not be refreshed. Retry when the account service is available."
        finally:
            with self.lock:
                if credential == self.credential:
                    self.ready.set()

    def _loop(self) -> None:
        while not self.stopped.is_set():
            self.wake.clear()
            self.refresh()
            self.wake.wait(5)

    def check(self, selection: str, credential: str) -> None:
        with self.lock:
            if credential != self.credential:
                self.credential, self.expires = credential, 0
                self.allowed = frozenset()
                self.ready.clear()
                self.wake.set()
        # Only cold/account-switch initialization waits; warm sends are local.
        self.ready.wait(5)
        with self.lock:
            if self.clock() >= self.expires:
                self.wake.set()
                raise RuntimeError(self.error or "Model permission snapshot expired. Retry shortly.")
            if selection not in self.allowed:
                raise RuntimeError("This built-in model is disabled by Loom Admin.")

    def close(self) -> None:
        self.stopped.set()
        self.wake.set()
