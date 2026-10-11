# Independent small updates

Loom has independent application and Host runtime release paths. Source changes
on main enter tests and publishers automatically. Install the new bootstrap once
to gain the application loader; ordinary UI, preload and Electron business-code
edits then bypass the Windows installer build entirely.

| Change | Publication | Activation |
| --- | --- | --- |
| React, CSS, public resources, Electron business code and preloads | Application Update; immutable `app-*` releases and `application-runtime/stable.json` | Background prepare; coordinated Host/Desktop restart |
| Agent/runtime code and builtin model definitions | Host Runtime Release; same-source pytest gate | Existing idle-task guard, self-test, rollback |
| Stable entry/loader/store, installed dependencies, native/build infrastructure | Successful CI routes to Windows Release | Incremental installer download and restart |

An application release contains `dist/`, compiled business modules in
`dist-electron/`, and a minimal ESM package descriptor. It excludes Electron,
node_modules, Python and the installer. Identical files reuse immutable URLs and
the client object cache; the first measured complete bundle is about 8 MB.
The stable loader resolves bare module imports through the installed package,
including dependencies inside app.asar. The sandboxed preloads remain paired
with their UI and IPC implementation. The legacy UI publisher remains available
for older clients; the new bootstrap uses the unified application bundle.

The Host alone downloads and writes activation pointers. Desktop processes
follow the live Host's build, so opening a window does not independently upgrade
its IPC implementation. Restart uses the existing active-task safety boundary,
closes connected desktops and reopens them with the new Host. Headless Hosts
apply prepared updates when idle. Application checks start after five seconds
and repeat every thirty seconds. The update panel shows download/restart status.

Every payload is checked for exact bootstrap compatibility, safe paths, trusted
GitHub origin, size and SHA256 before staging and again before execution. A
candidate becomes active only after Host transport starts. Import/startup failure
or an unacknowledged sixty-second boot triggers recovery; a crashed boot is
quarantined on the next launch. The previous verified build or installed code
remains the fallback. Bootstrap changes require one new installer to replace the
stable loader; routine model defaults, account fixes and UI changes do not.

Application publication runs typechecks, Electron contracts, renderer build and
a real relocated-code/sandboxed-preload Electron probe. All assets upload before
the channel advances; concurrent channel writes retry and newer source prevents
stale publication. The manual Windows release dispatches application publication
for the new exact dependency baseline.

UI bundles must match the exact bootstrap version. They cannot assume new IPC
methods until that bootstrap is installed. UI source timestamps and bundled
source identity prevent an older channel from replacing a newer embedded UI.
Build IDs distinguish resource revisions without incrementing the desktop
version for every theme or layout edit. Manifests carry the source SHA.

UI publication uploads immutable content-addressed assets. Unchanged objects
reuse their prior release URLs. Clients likewise reuse cached objects and
matching packaged resources, verify each file's size/SHA256, and stage the
complete bundle before activation. Four workers bound network concurrency.
The initial measured UI bundle is roughly 7.5 MB; a typical edit replaces the
changed JS/CSS chunks rather than the 324 MB desktop installer.

Activation never reloads a window containing a draft or ongoing task. A new
window selects the staged candidate, verifies it again, and starts a 30-second
health deadline. The sandboxed preload forwards an App mount acknowledgement;
only the matching window and entrypoint can commit the candidate. Failed load,
renderer crash or missing acknowledgement restores the previous verified UI or
the embedded renderer. An unfinished boot marker is quarantined at next launch.
The previous successful bundle is retained; unused bundles/objects are pruned
after successful activation when no download is in progress.

Publisher jobs upload all assets before advancing the channel. Changed source
supersedes older jobs; generated channel commits do not trigger another release.
The UI job typechecks, tests and builds its own source snapshot. The bootstrap
router waits for successful CI and ignores already-versioned release commits.
The manual Windows release also dispatches UI publication for its new bootstrap
version (bot-generated pushes otherwise do not trigger GitHub workflows).

Validation: manifest/path/size/origin rejection, changed-object reuse, corrupt
cache repair, failed staging cleanup, window acknowledgement isolation, startup
rollback and crashed-boot quarantine are tested. A real sandboxed Electron
window and compiled preload confirmed that the staged UI can load and commit.
The existing Host same-commit test/release gate remains intact. Auto-updates can
be disabled through the existing `LOOM_DISABLE_AUTO_UPDATES=1` switch.

Operational paths: the Host data directory's `ui-updates/state.json`, numbered
bundles and `objects/` cache. Clear the updater state only for deliberate local
recovery; replacing a published manifest cannot bypass file integrity checks.
Rollback a production regression by publishing a new build ID containing the
last known good source, so quarantined/downgrade-blocked IDs are not reused.
