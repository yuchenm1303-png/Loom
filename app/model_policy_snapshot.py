"""Short-lived, account-scoped model permissions refreshed off the RPC queue."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from urllib.error import HTTPError


class ModelPolicySnapshot:
    def __init__(self, url: str, credential: str, *, fetch=None, clock=time.monotonic, start=True,
                 on_authentication_failure=None):
        self.url, self.credential = url, credential
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.allowed: frozenset[str] = frozenset()
        self.known: frozenset[str] = frozenset()
        self.dynamic: dict[str, tuple[bool, float]] = {}
        self.resolves_dynamic = False
        self.schema_version = 1
        self.denial_sources: dict[str, str] = {}
        self.generation = 0
        self.credential_revision = 0
        self.on_authentication_failure = on_authentication_failure
        self.auth_failure_revision = -1
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
            revision = self.credential_revision
        started = self.clock()
        try:
            payload = self.fetch(credential)
            access = payload.get("access") if isinstance(payload, dict) else None
            if (not isinstance(access, dict) or not isinstance(access.get("models"), list)
                    or any(not isinstance(model, str) for model in access["models"])
                    or not isinstance(access.get("enabled", True), bool)):
                raise ValueError("Invalid model permission snapshot")
            allowed = frozenset(str(model) for model in access["models"]) if access.get("enabled", True) else frozenset()
            decisions = access.get("decisions", [])
            if (not isinstance(decisions, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("model_id"), str)
                    or not isinstance(item.get("enabled"), bool) for item in decisions)):
                raise ValueError("Invalid model permission decisions")
            denial_sources = {item["model_id"]: str(item.get("source", "")) for item in decisions}
            known = frozenset(item["model_id"] for item in decisions) | allowed
            with self.lock:
                if revision != self.credential_revision:
                    return
                self.allowed, self.expires, self.error = allowed, started + 10, ""
                self.generation += 1
                self.schema_version = access.get("schema_version", 1)
                self.denial_sources = denial_sources
                self.dynamic.clear()
                self.known = known
                self.resolves_dynamic = isinstance(access.get("decisions"), list)
                if not access.get("enabled", True):
                    self.dynamic.clear()
                    self.resolves_dynamic = False
        except Exception as exc:
            with self.lock:
                if revision != self.credential_revision:
                    return
                # A failed refresh must not extend an existing permission lease.
                self.error = "Model permissions could not be refreshed. Retry when the account service is available."
                if isinstance(exc, HTTPError) and exc.code in {401, 403}:
                    self.generation += 1  # Fence older in-flight dynamic decisions too.
                    self.allowed = frozenset()
                    self.known = frozenset()
                    self.dynamic.clear()
                    self.expires = 0.0
                notify = isinstance(exc, HTTPError) and exc.code == 401 and self.auth_failure_revision != revision
                if notify:
                    self.auth_failure_revision = revision
            if notify and self.on_authentication_failure:
                try:
                    self.on_authentication_failure()
                except Exception:
                    pass  # Notification failure cannot create or revoke a grant.
        finally:
            with self.lock:
                if revision == self.credential_revision:
                    self.ready.set()

    def _loop(self) -> None:
        while not self.stopped.is_set():
            self.wake.clear()
            self.refresh()
            self.wake.wait(5)

    def check(self, selection: str, credential: str) -> None:
        # Execution checks cannot replace the identity installed by the control
        # plane. An old caller must not switch the cache back after logout.
        with self.lock:
            if credential != self.credential:
                raise RuntimeError("Model account changed. Retry shortly.")
        # Valid warm sends stay local. An expired lease must wait for the
        # background refresh instead of failing the first send after resume.
        with self.lock:
            if credential == self.credential and self.clock() >= self.expires:
                self.ready.clear()
                self.wake.set()
        self.ready.wait(5)
        self._check(selection, credential)

    def set_credential(self, credential: str) -> None:
        with self.lock:
            if credential != self.credential:
                self.credential_revision += 1
                self.credential, self.expires = credential, 0
                self.allowed = frozenset()
                self.known = frozenset()
                self.dynamic.clear()
                self.generation += 1
                self.denial_sources.clear()
                self.ready.clear()
                self.wake.set()

    def _check(self, selection: str, credential: str) -> None:
        with self.lock:
            if credential != self.credential:
                raise RuntimeError("Model account changed. Retry shortly.")
            if self.clock() >= self.expires:
                self.wake.set()
                raise RuntimeError(self.error or "Model permission snapshot expired. Retry shortly.")
            if selection in self.allowed:
                return
            if self.denial_sources.get(selection) == "catalog" or (self.schema_version == 2 and selection not in self.known):
                raise RuntimeError("Model is no longer available from this provider.")
            if selection in self.known or not self.resolves_dynamic:
                raise RuntimeError("This built-in model is disabled by Loom Admin.")
            # Legacy snapshots without decisions are explicit allow-lists.
            # Modern policy snapshots resolve newly discovered models through
            # /check rather than mistaking an incomplete catalog for a deny.
            cached = self.dynamic.get(selection)
            if cached and self.clock() < cached[1]:
                enabled = cached[0]
            else:
                enabled = None
            generation = self.generation
        if enabled is None:
            started = self.clock()
            try:
                request = urllib.request.Request(
                    self.url.rsplit("/", 1)[0] + "/check",
                    data=json.dumps({"model_id": selection}).encode("utf-8"),
                    headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                decision = payload.get("decision", {})
                if decision.get("model_id") != selection or not isinstance(decision.get("enabled"), bool):
                    raise ValueError("Invalid model policy decision")
                enabled = decision["enabled"]
            except Exception as exc:
                raise RuntimeError("Model permissions could not be refreshed. Retry when the account service is available.") from exc
            with self.lock:
                if credential != self.credential or generation != self.generation or self.clock() >= started + 10:
                    raise RuntimeError("Model permission snapshot expired. Retry shortly.")
                self.dynamic[selection] = (enabled, started + 10)
        if not enabled:
            raise RuntimeError("This built-in model is disabled by Loom Admin.")

    def close(self) -> None:
        self.stopped.set()
        self.wake.set()
