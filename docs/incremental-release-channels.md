# Independent small updates

Loom has three release paths. Source changes on main enter the relevant tests
and publisher automatically; an installation is needed once to gain the new UI
updater, after which UI-only edits do not require another installer.

| Change | Publication | Activation |
| --- | --- | --- |
| React, CSS, theme, public resources | UI Resource Update; immutable `ui-*` releases and `ui-runtime/stable.json` | Background prepare; next desktop window launch |
| Agent/runtime code and builtin model definitions | Host Runtime Release; same-source pytest gate | Existing idle-task guard, self-test, rollback |
| Electron/preload, native/build infrastructure | Successful CI routes to Windows Release | Incremental installer download and restart |

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
