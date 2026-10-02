# Loom Host Runtime

Loom Host Runtime is versioned and distributed independently from Loom Desktop.

The installed Desktop package is the long-lived bootstrap and optional local UI. The active Host runtime lives under the user's Loom data directory and contains the frozen Agent/App Server runtime, Windows sandbox helper, and Browser Use extension assets. Loom Web and Desktop both use that active runtime.

`runtime.json` defines the compatibility series and Host protocol. `stable.json` is machine-written by the Host runtime release workflow after a verified Windows bundle is published as a GitHub prerelease. Host prereleases are deliberately separate from normal `v*` Desktop releases so Electron's Desktop updater and GitHub's `/releases/latest` remain unaffected.

A Host update is downloaded to staging, SHA-256 verified against `stable.json`, extracted, self-tested, and only then atomically activated. If reloading the Agent runtime fails, the bootstrap restores the previous runtime pointer. Updates wait until the Agent has no active turn.
