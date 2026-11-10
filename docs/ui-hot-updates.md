# Desktop UI hot updates

Windows x64 packaged Desktop can download a renderer-only release and refresh each safe window without restarting its Host or Agent tasks. Development continues to use Vite. Electron, preload, dependencies and Host changes still require a complete installer update.

## User experience

- Check after 10 seconds, every 30 minutes, on returning after five minutes, and through the existing Check for updates action.
- Update automatically after 15 seconds without input. Unsent text (including drafts in other conversations), attachments, quoted replies, IME composition, editable content and open dialogs defer the refresh. Deferred windows retry every 15 seconds.
- Show “界面更新已就绪” while deferred and “界面已更新” after successful activation. Notices remain until dismissed. Preserve conversation routing and Chromium storage.
- Refresh one window independently; other windows wait until their own inputs are safe. The shared Host keeps running.
- Honor automatic-update preferences and freshly verify account update eligibility before download and each window activation. Manual checks can update even if automatic updates are disabled.

## Compatibility and recovery

`build:electron` writes `dist-electron/ui-contract.json`. Its SHA-256 covers tracked Electron, Host, dependency, packaging and build-script sources, with normalized text line endings. Renderer-only commits preserve this contract. Both the contract and exact bootstrap version must match. Schema 1 historical manifests are ignored.

The client reads schema 2 `ui-runtime/stable.json` directly from the repository over HTTPS, then downloads immutable, content-addressed GitHub release assets. The repository/channel has the same trust boundary as the existing installer feed; hashes provide transfer integrity, not an independent publisher signature. No remote HTML is loaded into a privileged window. Downloads have bounded time, size, file count and concurrency; Windows path aliases, traversal and non-repository asset URLs are rejected.

Files stage in a separate directory and become available atomically after all hashes match. CSP and exact-document IPC trust are installed before navigation. Existing App/preload health signals must arrive within 30 seconds before persisting the active pointer. Load failures, preload errors, renderer crashes and boot timeouts restore the bundled UI; rejected builds cannot replay. Active cached assets are reverified on startup. The installer remains the fallback for incompatible releases.

## Publishing

1. Ship one complete Windows installer containing this implementation. Existing installed versions cannot gain a new loader through a UI-only update.
2. Commit renderer-only changes to `main`, leaving the package version and contract sources unchanged.
3. Run **Publish UI hot update** (`.github/workflows/ui-release.yml`). It builds and validates the renderer, publishes assets under an immutable `ui-<timestamp>` prerelease, then commits the channel manifest. Installer clients ignore this prerelease.

If contract sources changed, publish a complete installer first. Hot updates are compatible only with installers built from the matching contract. A rejected build must be repaired and published with a higher build number. The channel push uses a fast-forward commit: if `main` advances during publication it fails instead of overwriting concurrent work; rerun the workflow.

Local verification:

```powershell
cd desktop-react
npm run build:electron
node --experimental-test-module-mocks --test electron/tests/uiUpdate*.test.mjs
npm run build:renderer
node scripts/build-ui-update.mjs
node scripts/tests/ui-hot-update-electron.mjs
```

The publishing workflow uses the repository's Actions token; no signing keys or upload credentials are embedded in Git or the application.
