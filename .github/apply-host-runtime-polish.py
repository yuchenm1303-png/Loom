from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(rel: str, old: str, new: str, count: int = 1) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected block in {rel}: {old[:100]!r}")
    path.write_text(text.replace(old, new, count), encoding="utf-8")


# Keep any already-installed unpacked browser extension synchronized when the
# independently distributed Host runtime switches versions.
replace(
    "desktop-react/electron/main.ts",
    '''async function setupBrowserExtension(browser: "edge" | "chrome" = "edge", extensionConnected = false): Promise<Record<string, unknown>> {\n  const source = browserExtensionSource();\n  const target = browserExtensionTarget();\n  if (!fsSync.existsSync(path.join(source, "manifest.json"))) {\n    throw new Error(`Packaged browser extension is missing: ${source}`);\n  }\n  const manifest = JSON.parse(await fs.readFile(path.join(source, "manifest.json"), "utf8")) as { version?: string };\n  const installTargets = [target, ...legacyBrowserExtensionTargets()];\n  const bridgeConfig = JSON.stringify({ bridgeUrl: "http://127.0.0.1:39222", token: ensureBrowserBridgeToken() });\n  const updateSignal = JSON.stringify({ token: crypto.randomUUID(), version: String(manifest.version || "") });\n  for (const installTarget of installTargets) {\n    await fs.mkdir(installTarget, { recursive: true });\n    await fs.cp(source, installTarget, { recursive: true, force: true });\n    await fs.writeFile(path.join(installTarget, "bridge-config.json"), bridgeConfig, { encoding: "utf8", mode: 0o600 });\n    // Write the signal last. A legacy extension that already has the watcher\n    // will now reload only after all code and pairing files are in place.\n    await fs.writeFile(path.join(installTarget, "extension-update.json"), updateSignal, { encoding: "utf8", mode: 0o600 });\n  }\n''',
    '''async function syncBrowserExtensionAssets(): Promise<{ target: string; version: string; installTargets: string[] }> {\n  const source = browserExtensionSource();\n  const target = browserExtensionTarget();\n  if (!fsSync.existsSync(path.join(source, "manifest.json"))) {\n    throw new Error(`Packaged browser extension is missing: ${source}`);\n  }\n  const manifest = JSON.parse(await fs.readFile(path.join(source, "manifest.json"), "utf8")) as { version?: string };\n  const version = String(manifest.version || "");\n  const installTargets = [target, ...legacyBrowserExtensionTargets()];\n  const bridgeConfig = JSON.stringify({ bridgeUrl: "http://127.0.0.1:39222", token: ensureBrowserBridgeToken() });\n  const updateSignal = JSON.stringify({ token: crypto.randomUUID(), version });\n  for (const installTarget of installTargets) {\n    await fs.mkdir(installTarget, { recursive: true });\n    await fs.cp(source, installTarget, { recursive: true, force: true });\n    await fs.writeFile(path.join(installTarget, "bridge-config.json"), bridgeConfig, { encoding: "utf8", mode: 0o600 });\n    // Write the signal last. A legacy extension that already has the watcher\n    // reloads only after all code and pairing files are in place.\n    await fs.writeFile(path.join(installTarget, "extension-update.json"), updateSignal, { encoding: "utf8", mode: 0o600 });\n  }\n  return { target, version, installTargets };\n}\n\nasync function setupBrowserExtension(browser: "edge" | "chrome" = "edge", extensionConnected = false): Promise<Record<string, unknown>> {\n  const { target, version, installTargets } = await syncBrowserExtensionAssets();\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''    desiredVersion: String(manifest.version || ""),\n''',
    '''    desiredVersion: version,\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''  reload: async () => {\n    const wasReady = rpc.ready;\n    rpc.stop();\n    if (wasReady) await rpc.connect();\n  },\n});\n''',
    '''  reload: async () => {\n    // Browser Use assets are part of the independently versioned Host runtime.\n    // Synchronize installed unpacked-extension folders before the App Server\n    // resumes so Browser Use and Agent code cross the version boundary together.\n    await syncBrowserExtensionAssets();\n    const wasReady = rpc.ready;\n    rpc.stop();\n    if (wasReady) await rpc.connect();\n  },\n});\n''',
)

# Explain the new atomic Host updater phases instead of falling back to a generic
# message while the pointer is ready/activating or the bootstrap is too old.
replace(
    "desktop-react/src/components/WebAppGate.tsx",
    '''  if (phase === "downloaded") return "Loom Host update is ready. Restart Loom on the host computer to finish updating.";\n  if (phase === "available") return version\n''',
    '''  if (phase === "ready") return "Loom Host update is verified and waiting for the current task to become idle.";\n  if (phase === "activating") return "Loom Host is switching to the verified runtime. It will reconnect automatically.";\n  if (phase === "incompatible") return "This Loom bootstrap is too old for the newest Host runtime. Install the latest Loom once to upgrade the bootstrap.";\n  if (phase === "downloaded") return "Loom bootstrap update is ready. Restart Loom on the host computer to finish updating.";\n  if (phase === "available") return version\n''',
)

# Protect cross-version Browser Use synchronization.
test = ROOT / "tests/test_host_runtime_distribution.py"
text = test.read_text(encoding="utf-8")
text += '''\n\ndef test_runtime_activation_synchronizes_browser_assets() -> None:\n    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")\n    assert "async function syncBrowserExtensionAssets" in main\n    assert "await syncBrowserExtensionAssets();" in main\n'''
test.write_text(text, encoding="utf-8")

print("host runtime polish applied")
