from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(rel: str, old: str, new: str, count: int = 1) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected block in {rel}: {old[:120]!r}")
    path.write_text(text.replace(old, new, count), encoding="utf-8")


# The Host and Desktop are one Loom product identity. A background-only Host
# should live in the notification area, while an explicitly opened Desktop gets
# the normal taskbar button back.
replace(
    "desktop-react/electron/remoteRelay.ts",
    '''  if (!window) return;\n  if (window.isMinimized()) window.restore();\n  window.show();\n  window.focus();\n''',
    '''  if (!window) return;\n  window.setSkipTaskbar(false);\n  if (window.isMinimized()) window.restore();\n  window.show();\n  window.focus();\n''',
)

replace(
    "desktop-react/electron/remoteRelay.ts",
    '''async function ensureHostTray(): Promise<void> {\n  if (hostTray || process.platform === "darwin") return;\n  let icon = nativeImage.createEmpty();\n  try { icon = await app.getFileIcon(process.execPath, { size: "small" }); } catch {}\n  hostTray = new Tray(icon);\n  hostTray.setToolTip("Loom Host · local Web access available in background");\n  hostTray.setContextMenu(Menu.buildFromTemplate([\n    { label: "Open Loom", click: () => showLoomWindow() },\n    { label: "Open Loom Web", click: () => void openLocalLoomWeb() },\n    { type: "separator" },\n    {\n      label: "Quit Loom Host",\n      click: () => {\n        allowHostQuit = true;\n        app.quit();\n      },\n    },\n  ]));\n  hostTray.on("double-click", () => showLoomWindow());\n}\n''',
    '''async function ensureHostTray(): Promise<void> {\n  if (hostTray || process.platform === "darwin") return;\n  // Reuse the installed Loom executable icon instead of introducing a second\n  // "Host" brand. The tray is only Loom's background presence.\n  let icon = nativeImage.createEmpty();\n  try { icon = await app.getFileIcon(process.execPath, { size: "normal" }); } catch {}\n  hostTray = new Tray(icon);\n  hostTray.setToolTip("Loom · running in the background");\n  hostTray.setContextMenu(Menu.buildFromTemplate([\n    { label: "Open Loom", click: () => showLoomWindow() },\n    { label: "Open Loom Web", click: () => void openLocalLoomWeb() },\n    { type: "separator" },\n    {\n      label: "Quit Loom",\n      click: () => {\n        allowHostQuit = true;\n        app.quit();\n      },\n    },\n  ]));\n  hostTray.on("double-click", () => showLoomWindow());\n}\n''',
)

replace(
    "desktop-react/electron/remoteRelay.ts",
    '''  window.on("close", (event) => {\n    if (allowHostQuit) return;\n    event.preventDefault();\n    window.hide();\n  });\n''',
    '''  window.on("close", (event) => {\n    if (allowHostQuit) return;\n    event.preventDefault();\n    // Closing the Desktop means "keep Loom running in the background". Remove\n    // the hidden window from Alt+Tab/taskbar and leave only the Loom tray icon.\n    window.setSkipTaskbar(true);\n    window.hide();\n  });\n''',
)

replace(
    "desktop-react/electron/remoteRelay.ts",
    '''    const keepHidden = () => {\n      if (!uiRequested && !window.isDestroyed()) setImmediate(() => {\n        if (!uiRequested && !window.isDestroyed()) window.hide();\n      });\n    };\n''',
    '''    const keepHidden = () => {\n      if (!uiRequested && !window.isDestroyed()) setImmediate(() => {\n        if (!uiRequested && !window.isDestroyed()) {\n          window.setSkipTaskbar(true);\n          window.hide();\n        }\n      });\n    };\n''',
)

replace(
    "desktop-react/electron/main.ts",
    '''    autoHideMenuBar: true,\n    show: false,\n    webPreferences: {\n''',
    '''    autoHideMenuBar: true,\n    show: false,\n    // A login-started background Host must not reserve a taskbar slot. If the\n    // user opens Loom, remoteRelay restores the normal taskbar button first.\n    skipTaskbar: isBackgroundHostLaunch(),\n    webPreferences: {\n''',
)

# Keep a lightweight source contract around this UX boundary so Host/Desktop
# identity cannot silently regress when the relay lifecycle changes again.
test = ROOT / "tests/test_host_shell_identity.py"
test.write_text('''from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_background_host_uses_single_loom_identity() -> None:\n    relay = (ROOT / "desktop-react/electron/remoteRelay.ts").read_text(encoding="utf-8")\n    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")\n\n    assert 'window.setSkipTaskbar(false);' in relay\n    assert 'window.setSkipTaskbar(true);' in relay\n    assert 'Loom · running in the background' in relay\n    assert 'label: "Quit Loom"' in relay\n    assert 'Quit Loom Host' not in relay\n    assert 'app.getFileIcon(process.execPath, { size: "normal" })' in relay\n    assert 'skipTaskbar: isBackgroundHostLaunch(),' in main\n''', encoding="utf-8")

print("host shell identity polish applied")
