# One complete installation per version

Loom now uses one Windows installer update channel for the main process,
preloads, UI, Python runtime, sandbox helper and bundled browser extension.

The existing differential downloader still reuses unchanged installer blocks.
It reconstructs the complete new installer and verifies its SHA-512 before
electron-updater promotes the download. Unsupported ranges or missing old
blocks fall back to a complete installer download. Installation is the same
in either case: wait for Agent work to be idle, verify account access again,
close Desktop clients, exit the Host, and let NSIS replace the application.

Startup imports applicationEntry from the installed package. The UI is loaded
from that package's dist directory, and the runtime comes from its resources
directory. Packaged applications ignore runtime overrides and old component
cache pointers. Incomplete runtime installations report an error instead of
substituting another cached runtime. Development runtime overrides remain
available for local development.

The Application and UI loaders, their independent update state machines and
their resource stores have been removed. Standalone Host updating is replaced
by a compatibility adapter that directs existing Web update requests to the
installer. The three component publishing workflows are retired. Historical
channel manifests and downloaded user caches are left untouched for old-client
compatibility; the new application does not read them. Chat history, credentials,
settings and project files stay outside the installation directory.

The release router now routes UI, Electron and runtime source changes through
successful main CI to the Windows installer release workflow. The download
mirror publishes only verified installer assets and latest.yml metadata.

Validation performed locally:

- Complete renderer/Electron build and type checking.
- Electron tests covering ignored legacy pointers, installer discovery,
  concurrent checks, account restrictions, task guards and installer integrity.
- Runtime-selection and release-routing Python regressions.
- Real packaged Host and Desktop startup with legacy active/ready pointers: UI
  loads the installed app.asar and the bundled runtime initializes normally.
- Real isolated NSIS first install, upgrade, removal of stale application files,
  closure of application/runtime processes and preservation of user data.

Migration requires a newly built complete installer: changing source code or
publishing a legacy component manifest cannot replace an old installed entry.
