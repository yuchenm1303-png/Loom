# First-run contract for the Windows installer

Loom must open its local UI on a clean Windows x64 installation without Python,
provider API keys, a saved model connection, or an existing Loom account session.
Cloud generation requires signing in to Loom; provider keys remain on the account
service. A temporary authorization or network failure must not stop local startup.

The initial model resolver preserves a configured user connection. An unconfigured
or legacy default whose key is absent falls back to the account model. The local
server starts with an authorization-pending backend when necessary. Before the
first message after sign-in, Electron installs the authorized account backend
through the existing model hot-switch operation, without restarting the UI.

The installer includes the frozen Python runtime and its SDK/native dependencies,
Playwright's driver, Chromium, the browser extension and the Windows sandbox
helper. Chromium is downloaded during packaging, not on the customer's first use.
An explicitly selected installed-browser channel or attached browser remains a
user-owned external browser.

`first-run-test` validates packaged imports, launches the bundled browser without
network access, captures a PNG, and creates a local runtime/session in a temporary
empty home without requiring provider credentials. The runtime build and all
Windows release workflows fail if this gate fails. Source tests additionally
exercise a credential-free JSON-RPC initialization and account startup during
sign-out, expired-session and service-outage conditions.

These checks do not establish availability of a remote model service or third-party
accounts, and do not substitute for an installer test in a clean Windows VM.
Do not claim a release is validated merely because source unit tests passed.
