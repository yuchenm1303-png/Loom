from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(rel: str, old: str, new: str, count: int = 1) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected block in {rel}: {old[:120]!r}")
    updated = text.replace(old, new, count)
    path.write_text(updated, encoding="utf-8")


# The launch-mode module owns process lifetime only. Runtime version/protocol now
# come from the independently installed Host runtime manifest.
replace(
    "desktop-react/electron/hostMode.ts",
    '''// Host runtime version is intentionally independent from the Desktop package\n// version. The browser cares about Host protocol/runtime compatibility; the\n// Electron UI may evolve on a different cadence.\nexport const LOOM_HOST_RUNTIME_VERSION = "1.0.0";\n\n''',
    "",
)

# Never read the managed pointer before Electron is ready enough to resolve the
# per-user data directory.
replace(
    "desktop-react/electron/hostRuntime.ts",
    '''function managedHostRuntime(): HostRuntimeDescriptor | null {\n  if (!app.isPackaged || process.platform !== "win32") return null;\n''',
    '''function managedHostRuntime(): HostRuntimeDescriptor | null {\n  if (!app.isReady() || !app.isPackaged || process.platform !== "win32") return null;\n''',
)

# Model helper subprocesses must come from the active Host runtime, not whatever
# happens to be on PATH and not the Desktop package version.
replace(
    "desktop-react/electron/modelManager.ts",
    '''import { spawn, spawnSync } from "node:child_process";\nimport path from "node:path";\n''',
    '''import { spawn, spawnSync } from "node:child_process";\nimport path from "node:path";\nimport { resolveHostPythonExecutable } from "./hostRuntime.js";\n''',
)
replace(
    "desktop-react/electron/modelManager.ts",
    '''    const python = process.env.LOOM_PYTHON || (process.platform === "win32" ? "python" : "python3");\n    const script = path.join(this.repoRoot, scriptName);\n''',
    '''    const python = resolveHostPythonExecutable(this.repoRoot);\n    const script = path.join(this.repoRoot, scriptName);\n''',
    count=2,
)

# Device identity now reports the active runtime version/protocol separately from
# the long-lived bootstrap transport protocol.
replace(
    "desktop-react/electron/webRelayAuth.ts",
    '''import { LoomAccountClient } from "./accountClient.js";\nimport { LOOM_HOST_RUNTIME_VERSION, loomHostLaunchMode, type LoomHostLaunchMode } from "./hostMode.js";\n\nconst DEFAULT_ACCOUNT_URL = "https://account.smirel.com/v1";\nconst DEVICE_ID_FILE = "loom-web-device-id";\nexport const LOOM_HOST_PROTOCOL_VERSION = 1;\n''',
    '''import { LoomAccountClient } from "./accountClient.js";\nimport { loomHostLaunchMode, type LoomHostLaunchMode } from "./hostMode.js";\nimport { currentHostRuntimeProtocol, currentHostRuntimeVersion } from "./hostRuntime.js";\n\nconst DEFAULT_ACCOUNT_URL = "https://account.smirel.com/v1";\nconst DEVICE_ID_FILE = "loom-web-device-id";\n// This protocol belongs to the tiny installed bootstrap/relay. Agent/runtime\n// capability compatibility is reported separately as hostProtocol.\nexport const LOOM_BOOTSTRAP_PROTOCOL_VERSION = 1;\n''',
)
replace(
    "desktop-react/electron/webRelayAuth.ts",
    '''  hostMode: LoomHostLaunchMode;\n  hostProtocol: number;\n};\n''',
    '''  hostMode: LoomHostLaunchMode;\n  hostProtocol: number;\n  bootstrapProtocol: number;\n};\n''',
)
replace(
    "desktop-react/electron/webRelayAuth.ts",
    '''    appVersion: app.getVersion(),\n    hostVersion: LOOM_HOST_RUNTIME_VERSION,\n    hostMode: loomHostLaunchMode(),\n    hostProtocol: LOOM_HOST_PROTOCOL_VERSION,\n''',
    '''    appVersion: app.getVersion(),\n    hostVersion: currentHostRuntimeVersion(),\n    hostMode: loomHostLaunchMode(),\n    hostProtocol: currentHostRuntimeProtocol(),\n    bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION,\n''',
)

# The relay's normal Host update route now talks to the independent runtime
# updater. The full Electron updater remains only as an explicit emergency
# bootstrap update path.
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''import { LoomAccountClient } from "./accountClient.js";\nimport { LOOM_HOST_PROTOCOL_VERSION, webRelayDeviceIdentity } from "./webRelayAuth.js";\nimport { ensureWebHostUpdate, softwareUpdateState } from "./updater.js";\nimport { BACKGROUND_HOST_ARG, LOOM_HOST_RUNTIME_VERSION, isBackgroundHostLaunch, loomHostLaunchMode, type LoomHostLaunchMode } from "./hostMode.js";\n''',
    '''import { LoomAccountClient } from "./accountClient.js";\nimport { LOOM_BOOTSTRAP_PROTOCOL_VERSION, webRelayDeviceIdentity } from "./webRelayAuth.js";\nimport { ensureBootstrapUpdate, softwareUpdateState } from "./updater.js";\nimport { BACKGROUND_HOST_ARG, isBackgroundHostLaunch, loomHostLaunchMode, type LoomHostLaunchMode } from "./hostMode.js";\nimport { currentHostRuntimeProtocol, currentHostRuntimeVersion } from "./hostRuntime.js";\nimport { ensureHostRuntimeUpdate, hostRuntimeUpdateState } from "./hostRuntimeUpdater.js";\n''',
)
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''  hostMode: LoomHostLaunchMode;\n  hostProtocol: number;\n};\n''',
    '''  hostMode: LoomHostLaunchMode;\n  hostProtocol: number;\n  bootstrapProtocol: number;\n};\n''',
)
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''    writeLocalJson(response, 200, {\n      ok: true,\n      ...identity,\n      hostVersion: LOOM_HOST_RUNTIME_VERSION,\n      hostMode: loomHostLaunchMode(),\n      relayReady: ws?.readyState === WebSocket.OPEN,\n      update: softwareUpdateState(),\n    }, origin);\n''',
    '''    writeLocalJson(response, 200, {\n      ok: true,\n      ...identity,\n      relayReady: ws?.readyState === WebSocket.OPEN,\n      update: hostRuntimeUpdateState(),\n      bootstrapUpdate: softwareUpdateState(),\n    }, origin);\n''',
)
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''      const requiredProtocol = Math.max(0, Math.min(1_000_000, Number(body.required_protocol || 0) || 0));\n      const update = LOOM_HOST_PROTOCOL_VERSION < requiredProtocol\n        ? await ensureWebHostUpdate()\n        : softwareUpdateState();\n      writeLocalJson(response, 200, {\n        ok: true,\n        hostProtocol: LOOM_HOST_PROTOCOL_VERSION,\n        hostVersion: LOOM_HOST_RUNTIME_VERSION,\n        hostMode: loomHostLaunchMode(),\n        requiredProtocol,\n        compatible: LOOM_HOST_PROTOCOL_VERSION >= requiredProtocol,\n        update,\n      }, origin);\n''',
    '''      const requiredProtocol = Math.max(0, Math.min(1_000_000, Number(body.required_protocol || 0) || 0));\n      const update = await ensureHostRuntimeUpdate(requiredProtocol);\n      const hostProtocol = currentHostRuntimeProtocol();\n      writeLocalJson(response, 200, {\n        ok: true,\n        hostProtocol,\n        hostVersion: currentHostRuntimeVersion(),\n        hostMode: loomHostLaunchMode(),\n        bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION,\n        requiredProtocol,\n        compatible: hostProtocol >= requiredProtocol,\n        update,\n      }, origin);\n''',
)
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''    } else if (frame.operation === "hostUpdateStatus") {\n      result = {\n        hostProtocol: LOOM_HOST_PROTOCOL_VERSION,\n        hostVersion: LOOM_HOST_RUNTIME_VERSION,\n        hostMode: loomHostLaunchMode(),\n        update: softwareUpdateState(),\n      };\n    } else if (frame.operation === "hostUpdateEnsure") {\n      const requiredProtocol = Math.max(0, Math.min(1_000_000, Number(args[0] || 0) || 0));\n      result = {\n        hostProtocol: LOOM_HOST_PROTOCOL_VERSION,\n        hostVersion: LOOM_HOST_RUNTIME_VERSION,\n        hostMode: loomHostLaunchMode(),\n        requiredProtocol,\n        compatible: LOOM_HOST_PROTOCOL_VERSION >= requiredProtocol,\n        update: LOOM_HOST_PROTOCOL_VERSION < requiredProtocol ? await ensureWebHostUpdate() : softwareUpdateState(),\n      };\n''',
    '''    } else if (frame.operation === "hostUpdateStatus") {\n      result = {\n        hostProtocol: currentHostRuntimeProtocol(),\n        hostVersion: currentHostRuntimeVersion(),\n        hostMode: loomHostLaunchMode(),\n        bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION,\n        update: hostRuntimeUpdateState(),\n      };\n    } else if (frame.operation === "hostUpdateEnsure") {\n      const requiredProtocol = Math.max(0, Math.min(1_000_000, Number(args[0] || 0) || 0));\n      const update = await ensureHostRuntimeUpdate(requiredProtocol);\n      const hostProtocol = currentHostRuntimeProtocol();\n      result = {\n        hostProtocol,\n        hostVersion: currentHostRuntimeVersion(),\n        hostMode: loomHostLaunchMode(),\n        bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION,\n        requiredProtocol,\n        compatible: hostProtocol >= requiredProtocol,\n        update,\n      };\n    } else if (frame.operation === "bootstrapUpdateStatus") {\n      result = { bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION, appVersion: app.getVersion(), update: softwareUpdateState() };\n    } else if (frame.operation === "bootstrapUpdateEnsure") {\n      result = { bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION, appVersion: app.getVersion(), update: await ensureBootstrapUpdate() };\n''',
)
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''        hostMode: auth.hostMode,\n        hostProtocol: auth.hostProtocol,\n''',
    '''        hostMode: auth.hostMode,\n        hostProtocol: auth.hostProtocol,\n        bootstrapProtocol: auth.bootstrapProtocol,\n''',
)

# Runtime subprocesses and extension assets are selected from the active managed
# Host bundle. The same bootstrap still provides native dialogs/clipboard and a
# rare emergency full-app update path.
replace(
    "desktop-react/electron/main.ts",
    '''import { isBackgroundHostLaunch, setLoomHostLaunchMode } from "./hostMode.js";\nimport { consumeHeadlessUpdateRestart, registerHeadlessUpdateGuard } from "./updater.js";\n''',
    '''import { isBackgroundHostLaunch, setLoomHostLaunchMode } from "./hostMode.js";\nimport { consumeHeadlessUpdateRestart, registerHeadlessUpdateGuard } from "./updater.js";\nimport {\n  currentHostRuntimeVersion,\n  resolveHostBrowserExtensionRoot,\n  resolveHostPythonExecutable,\n  resolveHostSandboxExecutable,\n} from "./hostRuntime.js";\nimport { registerHostRuntimeUpdateHooks } from "./hostRuntimeUpdater.js";\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''const REPO_VENV_PYTHON = process.platform === "win32"\n  ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")\n  : path.join(REPO_ROOT, ".venv", "bin", "python");\n''',
    "",
)
replace(
    "desktop-react/electron/main.ts",
    '''function resolvePythonExecutable(): string {\n  const configured = process.env.LOOM_PYTHON?.trim();\n  if (configured) return configured;\n  if (fsSync.existsSync(REPO_VENV_PYTHON)) return REPO_VENV_PYTHON;\n  return process.platform === "win32" ? "python" : "python3";\n}\n''',
    '''function resolvePythonExecutable(): string {\n  return resolveHostPythonExecutable(REPO_ROOT);\n}\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''function browserExtensionSource(): string {\n  return app.isPackaged\n    ? path.join(process.resourcesPath, "browser-current-tab")\n    : path.join(REPO_ROOT, "extensions", "browser-current-tab");\n}\n''',
    '''function browserExtensionSource(): string {\n  return resolveHostBrowserExtensionRoot(REPO_ROOT);\n}\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''registerHeadlessUpdateGuard(async () => {\n  try {\n    await rpc.assertRestartSafe();\n    return true;\n  } catch {\n    return false;\n  }\n});\n''',
    '''registerHeadlessUpdateGuard(async () => {\n  try {\n    await rpc.assertRestartSafe();\n    return true;\n  } catch {\n    return false;\n  }\n});\nregisterHostRuntimeUpdateHooks({\n  canActivate: async () => {\n    try {\n      await rpc.assertRestartSafe();\n      return true;\n    } catch {\n      return false;\n    }\n  },\n  reload: async () => {\n    const wasReady = rpc.ready;\n    rpc.stop();\n    if (wasReady) await rpc.connect();\n  },\n});\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''    const python = resolvePythonExecutable();\n    const script = path.join(REPO_ROOT, "loom_app_server.py");\n''',
    '''    const python = resolvePythonExecutable();\n    const sandboxExecutable = resolveHostSandboxExecutable(REPO_ROOT);\n    const script = path.join(REPO_ROOT, "loom_app_server.py");\n''',
)
replace(
    "desktop-react/electron/main.ts",
    '''        LOOM_DESKTOP_PYTHON: python,\n        // Computer Use observes the foreground window, which is sometimes Loom\n''',
    '''        LOOM_DESKTOP_PYTHON: python,\n        LOOM_HOST_RUNTIME_VERSION: currentHostRuntimeVersion(REPO_ROOT),\n        LOOM_WINDOWS_SANDBOX_EXECUTABLE: sandboxExecutable || process.env.LOOM_WINDOWS_SANDBOX_EXECUTABLE,\n        // Computer Use observes the foreground window, which is sometimes Loom\n''',
)

# The Desktop updater is now the bootstrap updater. Background Host sessions do
# not download 140MB Desktop releases during normal operation; only an explicit
# bootstrap compatibility request can trigger that rare path.
replace(
    "desktop-react/electron/updater.ts",
    '''let headlessInstallGuard: (() => boolean | Promise<boolean>) | null = null;\nlet headlessInstallRetryTimer: NodeJS.Timeout | null = null;\nlet headlessInstallAttempt: Promise<boolean> | null = null;\n''',
    '''let headlessInstallGuard: (() => boolean | Promise<boolean>) | null = null;\nlet headlessInstallRetryTimer: NodeJS.Timeout | null = null;\nlet headlessInstallAttempt: Promise<boolean> | null = null;\nlet bootstrapAutoInstallRequested = false;\n''',
)
replace(
    "desktop-react/electron/updater.ts",
    '''function headlessInstallWanted(): boolean {\n  return state.phase === "downloaded" && !hasVisibleWindow();\n}\n''',
    '''function headlessInstallWanted(): boolean {\n  return bootstrapAutoInstallRequested && state.phase === "downloaded" && !hasVisibleWindow();\n}\n''',
)
replace(
    "desktop-react/electron/updater.ts",
    '''export async function ensureWebHostUpdate(): Promise<SoftwareUpdateState> {\n  if (!updateEnabled) return softwareUpdateState();\n''',
    '''export async function ensureBootstrapUpdate(): Promise<SoftwareUpdateState> {\n  if (!updateEnabled) return softwareUpdateState();\n  bootstrapAutoInstallRequested = true;\n''',
)
replace(
    "desktop-react/electron/updater.ts",
    '''  return next;\n}\n\nfunction clearErrorRetry(): void {\n''',
    '''  return next;\n}\n\n// Backwards-compatible export for older internal callers. New Web Host update\n// paths use hostRuntimeUpdater instead of downloading the Desktop package.\nexport const ensureWebHostUpdate = ensureBootstrapUpdate;\n\nfunction clearErrorRetry(): void {\n''',
)
replace(
    "desktop-react/electron/updater.ts",
    '''  autoUpdater.on("update-not-available", (info) => {\n    clearErrorRetry();\n''',
    '''  autoUpdater.on("update-not-available", (info) => {\n    clearErrorRetry();\n    bootstrapAutoInstallRequested = false;\n''',
)
replace(
    "desktop-react/electron/updater.ts",
    '''app.on("browser-window-created", (_event, window) => {\n  window.webContents.once("did-finish-load", () => {\n''',
    '''app.on("browser-window-created", (_event, window) => {\n  startAutomaticChecks();\n  window.webContents.once("did-finish-load", () => {\n''',
)
replace(
    "desktop-react/electron/updater.ts",
    '''app.whenReady().then(() => {\n  startAutomaticChecks();\n});\n''',
    '''app.whenReady().then(() => {\n  // A pure background Host updates its independent runtime instead. Desktop\n  // update checks start lazily when a Desktop window actually exists.\n  if (hasVisibleWindow()) startAutomaticChecks();\n});\n''',
)

# Frozen fallback runtime gets its own manifest, so a brand-new install has a
# valid Host identity before it ever downloads the independent stable bundle.
replace(
    "desktop-react/scripts/build-windows-runtime.mjs",
    '''run(runtimeExe, [\n  "-c",\n  "import tempfile; from pathlib import Path; import keyring.backends.Windows; from app.connector_web_oauth import WebOAuthConnectorManager; m=WebOAuthConnectorManager(Path(tempfile.mkdtemp()), environment={}); s=m.github_status(); assert s.get('id') == 'github'; assert 'connected' in s; assert 'webOAuthAvailable' in s; print('loom-connector-status-ok')",\n], { cwd: path.dirname(runtimeExe) });\nconsole.log(`[build-runtime] Ready: ${runtimeExe}`);\n''',
    '''run(runtimeExe, [\n  "-c",\n  "import tempfile; from pathlib import Path; import keyring.backends.Windows; from app.connector_web_oauth import WebOAuthConnectorManager; m=WebOAuthConnectorManager(Path(tempfile.mkdtemp()), environment={}); s=m.github_status(); assert s.get('id') == 'github'; assert 'connected' in s; assert 'webOAuthAvailable' in s; print('loom-connector-status-ok')",\n], { cwd: path.dirname(runtimeExe) });\n\nconst hostRuntimeVersion = String(process.env.LOOM_HOST_RUNTIME_VERSION || "1.0.0").trim();\nconst hostRuntimeProtocol = Number(process.env.LOOM_HOST_RUNTIME_PROTOCOL || "1");\nconst hostMinBootstrapVersion = String(process.env.LOOM_HOST_MIN_BOOTSTRAP_VERSION || "0.1.10").trim();\nif (!/^\\d+\\.\\d+\\.\\d+$/.test(hostRuntimeVersion)) throw new Error(`Invalid Host runtime version: ${hostRuntimeVersion}`);\nif (!Number.isInteger(hostRuntimeProtocol) || hostRuntimeProtocol < 0) throw new Error(`Invalid Host runtime protocol: ${hostRuntimeProtocol}`);\nconst hostManifest = {\n  schema: 1,\n  version: hostRuntimeVersion,\n  protocol: hostRuntimeProtocol,\n  platform: "win32",\n  arch: "x64",\n  minBootstrapVersion: hostMinBootstrapVersion,\n  sourceSha: String(process.env.GITHUB_SHA || "").trim() || undefined,\n  publishedAt: new Date().toISOString(),\n};\nfs.writeFileSync(path.join(DIST_ROOT, "python", "manifest.json"), `${JSON.stringify(hostManifest, null, 2)}\\n`, "utf8");\nconsole.log(`[build-runtime] Host manifest: ${hostRuntimeVersion} protocol=${hostRuntimeProtocol}`);\nconsole.log(`[build-runtime] Ready: ${runtimeExe}`);\n''',
)

replace(
    "desktop-react/package.json",
    '''    "build:runtime:win": "node scripts/build-windows-runtime.mjs",\n''',
    '''    "build:runtime:win": "node scripts/build-windows-runtime.mjs",\n    "package:host-runtime": "node scripts/package-host-runtime.mjs",\n''',
)

# Update existing contracts to the runtime/bootstrap split.
replace(
    "tests/test_loom_host_background_runtime.py",
    '''    assert "LOOM_HOST_RUNTIME_VERSION" in source\n    auth = (ELECTRON / "webRelayAuth.ts").read_text(encoding="utf-8")\n    assert "hostVersion: LOOM_HOST_RUNTIME_VERSION" in auth\n    assert "hostMode: loomHostLaunchMode()" in auth\n''',
    '''    assert "LOOM_HOST_RUNTIME_VERSION" not in source\n    runtime = (ELECTRON / "hostRuntime.ts").read_text(encoding="utf-8")\n    updater = (ELECTRON / "hostRuntimeUpdater.ts").read_text(encoding="utf-8")\n    auth = (ELECTRON / "webRelayAuth.ts").read_text(encoding="utf-8")\n    assert "currentHostRuntimeVersion()" in auth\n    assert "currentHostRuntimeProtocol()" in auth\n    assert "LOOM_BOOTSTRAP_PROTOCOL_VERSION" in auth\n    assert "host-runtime" in runtime\n    assert "activateHostRuntime" in runtime\n    assert "ensureHostRuntimeUpdate" in updater\n    assert "hostMode: loomHostLaunchMode()" in auth\n''',
)
replace(
    "tests/test_loom_host_background_runtime.py",
    '''def test_headless_updates_wait_for_idle_runtime_and_resume_headless() -> None:\n    source = UPDATER.read_text(encoding="utf-8")\n    main = MAIN.read_text(encoding="utf-8")\n    assert "registerHeadlessUpdateGuard" in source\n    assert "HEADLESS_INSTALL_RETRY_MS" in source\n    assert "markHeadlessUpdateRestart();" in source\n    assert "consumeHeadlessUpdateRestart" in source\n    assert 'window.on("hide", () => { void maybeInstallHeadless(); });' in source\n    assert "await rpc.assertRestartSafe();" in main\n    assert "consumeHeadlessUpdateRestart()" in main\n''',
    '''def test_host_runtime_updates_independently_and_bootstrap_update_is_rare() -> None:\n    source = UPDATER.read_text(encoding="utf-8")\n    runtime_updater = (ELECTRON / "hostRuntimeUpdater.ts").read_text(encoding="utf-8")\n    main = MAIN.read_text(encoding="utf-8")\n    assert "bootstrapAutoInstallRequested" in source\n    assert "ensureBootstrapUpdate" in source\n    assert "registerHostRuntimeUpdateHooks" in runtime_updater\n    assert "checksum verification failed" in runtime_updater\n    assert "Runtime activation rolled back" in runtime_updater\n    assert "await rpc.assertRestartSafe();" in main\n    assert "resolveHostPythonExecutable" in main\n    assert "resolveHostBrowserExtensionRoot" in main\n''',
)

replace(
    "tests/test_loom_web_local_host_contract.py",
    '''UPDATER = (ROOT / "desktop-react/electron/updater.ts").read_text(encoding="utf-8")\n''',
    '''UPDATER = (ROOT / "desktop-react/electron/updater.ts").read_text(encoding="utf-8")\nHOST_UPDATER = (ROOT / "desktop-react/electron/hostRuntimeUpdater.ts").read_text(encoding="utf-8")\n''',
)
replace(
    "tests/test_loom_web_local_host_contract.py",
    '''    assert "LOOM_HOST_PROTOCOL_VERSION = 1" in WEB_RELAY_AUTH\n    assert "hostProtocol: auth.hostProtocol" in REMOTE_RELAY\n    assert 'const LOCAL_UPDATE_PATH = "/loom/update"' in REMOTE_RELAY\n    assert 'frame.operation === "hostUpdateEnsure"' in REMOTE_RELAY\n    assert "ensureWebHostUpdate()" in REMOTE_RELAY\n    assert "registerHeadlessUpdateGuard" in UPDATER\n    assert "HEADLESS_INSTALL_RETRY_MS" in UPDATER\n    assert "markHeadlessUpdateRestart()" in UPDATER\n    assert "autoUpdater.quitAndInstall(false, true)" in UPDATER\n''',
    '''    assert "LOOM_BOOTSTRAP_PROTOCOL_VERSION = 1" in WEB_RELAY_AUTH\n    assert "hostProtocol: auth.hostProtocol" in REMOTE_RELAY\n    assert "bootstrapProtocol: auth.bootstrapProtocol" in REMOTE_RELAY\n    assert 'const LOCAL_UPDATE_PATH = "/loom/update"' in REMOTE_RELAY\n    assert 'frame.operation === "hostUpdateEnsure"' in REMOTE_RELAY\n    assert "ensureHostRuntimeUpdate(requiredProtocol)" in REMOTE_RELAY\n    assert "hostRuntimeUpdateState()" in REMOTE_RELAY\n    assert 'frame.operation === "bootstrapUpdateEnsure"' in REMOTE_RELAY\n    assert "ensureBootstrapUpdate()" in REMOTE_RELAY\n    assert "bootstrapAutoInstallRequested" in UPDATER\n    assert "activateHostRuntime" in HOST_UPDATER\n    assert "sha256(bytes) !== channel.sha256" in HOST_UPDATER\n    assert "autoUpdater.quitAndInstall(false, true)" in UPDATER\n''',
)

# Dedicated distribution contracts protect the part that prevents Host updates
# from silently collapsing back into Desktop releases.
(ROOT / "tests/test_host_runtime_distribution.py").write_text('''from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_host_runtime_has_its_own_release_channel() -> None:\n    workflow = (ROOT / ".github/workflows/host-runtime-release.yml").read_text(encoding="utf-8")\n    package = (ROOT / "desktop-react/scripts/package-host-runtime.mjs").read_text(encoding="utf-8")\n    config = (ROOT / "host-runtime/runtime.json").read_text(encoding="utf-8")\n    assert "--prerelease" in workflow\n    assert "host-v$version" in workflow\n    assert "host-runtime/stable.json" in workflow\n    assert "Loom-Host-Runtime-$version-win-x64.zip" in workflow\n    assert "Compress-Archive" in package\n    assert '"minBootstrapVersion"' in config\n\n\ndef test_host_runtime_is_selected_independently_from_desktop() -> None:\n    runtime = (ROOT / "desktop-react/electron/hostRuntime.ts").read_text(encoding="utf-8")\n    updater = (ROOT / "desktop-react/electron/hostRuntimeUpdater.ts").read_text(encoding="utf-8")\n    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")\n    models = (ROOT / "desktop-react/electron/modelManager.ts").read_text(encoding="utf-8")\n    assert 'path.join(app.getPath("userData"), "host-runtime")' in runtime\n    assert "current.json" in runtime\n    assert "resolveHostPythonExecutable" in main\n    assert "resolveHostBrowserExtensionRoot" in main\n    assert "resolveHostPythonExecutable(this.repoRoot)" in models\n    assert "Runtime activation rolled back" in updater\n    assert "minBootstrapVersion" in updater\n\n\ndef test_background_host_does_not_poll_full_desktop_updates() -> None:\n    updater = (ROOT / "desktop-react/electron/updater.ts").read_text(encoding="utf-8")\n    assert "bootstrapAutoInstallRequested" in updater\n    assert "if (hasVisibleWindow()) startAutomaticChecks();" in updater\n    assert "ensureBootstrapUpdate" in updater\n''', encoding="utf-8")

print("host runtime channel transform applied")
