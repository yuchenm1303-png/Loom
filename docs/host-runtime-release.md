# Host runtime release

Pushing source changes to `main` runs CI without building or publishing a Host
runtime. Batch related fixes before releasing them.

To publish, open GitHub Actions → **Loom Host Runtime Release** → **Run workflow**
and select `main`. Other branches cannot publish to the stable channel.

The workflow tests the selected commit, builds the standalone runtime, and runs
the packaged first-run and self-tests. Only after these checks pass does it
publish the runtime and advance `host-runtime/stable.json`. Installed Hosts
continue to receive stable updates through the existing updater.

This does not publish the complete desktop installer. Desktop version releases
use the separate Windows release workflows.
