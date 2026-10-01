"""Shared loopback broker, independent of any one Loom app/runtime process."""
from __future__ import annotations

import json
import sys
import time

from .browser_extension_bridge import BrowserExtensionBridge


def main() -> None:
    # Pairing credentials travel over the private stdin pipe, never argv or logs.
    configuration = json.load(sys.stdin)
    bridge = BrowserExtensionBridge(**configuration)
    bridge.start()
    if bridge.port_conflict or bridge._remote:
        # Concurrent launch: another broker won the bind. Its clients will find
        # it via authenticated discovery, so the losing process has no work.
        bridge.stop()
        return
    idle_since = time.monotonic()
    try:
        while True:
            time.sleep(5.0)
            bridge.reap_runtimes()
            with bridge._condition:
                active = bool(bridge._runtime_clients)
            if active:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since > 300.0:
                break
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
