# Update verification and release audit — 2026-10-16

The complete-installer architecture is the baseline for this audit. Installed
main-process modules, preloads, UI and runtime come from one installation;
legacy Application/UI cache pointers cannot override it. See
`installer-update-chain-2026-10-16.md` for the migration and NSIS checks.

## Repairs

- Update eligibility returns a reason: service configuration, service failure,
  signed-out state, inactive account, explicit update revocation, deadline, or
  verification exception. The original boolean IPC endpoint remains available.
- Concurrent checks share only the running request, never a completed grant.
  A service outage gets one further attempt. Explicit denials do not. An outer
  35-second deadline bounds verification; activation still fails closed.
- Broken JSON, missing account fields and aborts while reading the response body
  are failures, rather than successful empty responses that overwrite the cached
  identity. They preserve the session and keep distinct error codes.
- Account requests and eligibility failures write bounded local diagnostics to
  `update-access-diagnostics.jsonl` under the Host profile. Records contain only
  fixed endpoints, reasons, timestamps, durations and machine error codes/status.
  They exclude credentials, account details, bodies and server error messages.
- Discovery retries after 60 seconds when automatic updates are enabled, even
  when a verified installer is already downloaded. Recovery removes stale
  verification errors and retains the download. Automatic installation also
  retries verification outages and rechecks preferences before restarting.
- The settings page shows the specific failure explanation in both languages.
- Windows release workflows no longer dispatch retired component publishers.
  Packaging runs Electron and release/status tests. The generated ASAR is checked
  against both compiled renderer and main-process files; retired loaders are
  rejected. Installer metadata must match package version, actual size and
  SHA512, including the legacy metadata fields, and include a nonempty blockmap.

## Validation

- Full renderer/Electron build and TypeScript checks passed.
- All 184 Electron tests passed, including response-body failure/session recovery,
  permission revocation, concurrent verification, deadline handling, timed retry
  of a downloaded installer, preference changes during automatic installation,
  checksums, fallback downloads, and active-task/repeated-install guards.
- Eight release/status/packaging-configuration tests passed, including tampered
  installer, version mismatch and missing blockmap rejection.
- Twelve account-delivery/runtime/release-routing Python regressions passed.
- An isolated Windows directory package was built. Its packaged modules and UI
  matched the compiled files. Bundled executable provider adapters initialized
  and listed local threads.
- The real packaged Host and Desktop started with legacy active/ready pointers
  present. The Desktop loaded the installed UI, the credential-free runtime
  initialized, and a second client connected after the first exited.
- The public v0.1.93 release contains the installer, blockmap and latest.yml.
  Metadata version, filename and size match the release asset listing. This is
  not a fresh hash verification of the 326 MB public installer.

The ordinary local development smoke initially failed because the machine's
Python 3.12 installation is unavailable. The packaged smoke uses the bundled
runtime and passed; no system Python installation was modified. A local
electron-builder archive-extraction rename failed with EPERM; the isolated
package was built using the existing unpacked Electron distribution instead.

## Release boundary

This audit does not publish or install a new version. Build the next complete
installer from the final main commit, using `npm run package:win` or the Windows
release workflow. Both enforce the added gates. Publication and an upgrade from
the existing installation remain checks on that new installer, after it exists.

Historical failures cannot be attributed to a particular transport error where
the old client discarded the evidence. The new diagnostics make future failures
inspectable. Network and account-service outages can still occur; they cannot
authorize installation, erase a valid download, or silently lose the failure
reason on the repaired paths.
