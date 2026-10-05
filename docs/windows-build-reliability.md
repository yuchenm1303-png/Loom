# Windows candidate build and startup verification

The failed Windows Release run 37267739025 spent 6m47s building, including
roughly 2m33s generating NSIS, then failed after the Host client's 120s connect
deadline. Discovery and Host IPC had already succeeded. The log did not identify
the Python startup stage; it cannot by itself prove a Python import failure.

Two concrete startup defects were found: automatic runtime updates perform
blocking archive extraction/self-test in the Host event loop, and restart safety
treated an uninitialized child as idle even with a pending connect request.

Changes:

- Archive extraction, runtime self-test and large filesystem operations yield
  to the event loop. The common restart-safety boundary rejects activation while
  connect is pending, covering both runtime and bootstrap updates.
- Candidate smoke sets LOOM_DISABLE_AUTO_UPDATES=1 for both update mechanisms.
  It uses isolated data and a private discovery port, asserts the bootstrap
  updater is disabled, connects without credentials twice, and verifies both
  clients attach to the same surviving Host. Candidate validation must not
  download, activate or accidentally test the online stable runtime.
- package:win produces win-unpacked, runs that smoke, then compresses that same
  directory with electron-builder --prepackaged. Startup errors therefore stop
  before NSIS compression. Workflows no longer duplicate the post-NSIS smoke.
- Shared Windows setup caches npm/pip downloads, Electron tooling and Playwright
  browsers. Frozen app output, generated OAuth configuration and credentials
  are never cached. Dependency installs use npm ci; pip keeps its explicit
  private prefix but no longer reinstalls every satisfied dependency.
- Browser downloads survive runtime-build cleanup. All frozen launches use the
  explicit full Chrome executable, including headless mode, so --no-shell avoids
  downloading and shipping an unused second Chromium implementation.
- Packaged commands import only their selected entry point. Startup stages go
  to stderr, preserving JSON-RPC stdout; build steps report elapsed seconds.
- Superseded Windows package checks are canceled; publishing workflows retain
  their protected release sequencing.

Cold builds still download dependencies and analyze the full frozen runtime.
Cache benefit must be measured on later CI runs, and reducing compression or
skipping startup tests is not part of this change. A first-run test still launches
real bundled Chromium; installer, OAuth and compatibility checks remain enabled.

Local validation: 59 Electron tests, 4 real-process watchdog tests and 13 Python
dispatch/stdio/App Server tests passed. The frozen runtime passed credential-free
first-run (real Chromium), import and connector checks. The new win-unpacked Host
passed two client connections and survived both exits; NSIS and blockmap were
then generated successfully from that verified directory. Compatibility commands
for model bridge, model admin and MCP still launch after lazy dispatch. Local
browser cache was seeded from an existing full Chromium bundle and matching
auxiliary downloads; this is a warm-cache check, not a measured cold CI build.
