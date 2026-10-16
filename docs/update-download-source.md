# Loom update download source

## Current state

The repository supports a first-party HTTPS download source backed by
S3-compatible object storage, with GitHub remaining the canonical fallback.
No bucket, domain, paid account, DNS record or secret is created by this change.
Until the configuration below is supplied and a new client is released, existing
clients continue to use GitHub. This is implemented infrastructure, not a claim
that China-region acceleration is already live or measured.

## What the owner needs to supply

Use a storage bucket with an S3-compatible API and a public HTTPS download
domain. An optional CDN sits in front of this bucket. The domain's root/path
must correspond exactly to the bucket prefix below. It must support HTTP Range
requests and preserve Content-Range, Content-Length and binary response bytes.
Do not enable transformations or compression for EXE/blockmap/ZIP/hash objects.

In GitHub repository **Settings → Secrets and variables → Actions**, configure:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `LOOM_UPDATE_SOURCE_URL` | Public HTTPS root, e.g. `https://downloads.example.com/loom`; replace with the actual owned domain |
| Variable | `LOOM_UPDATE_BUCKET` | Bucket name |
| Variable | `LOOM_UPDATE_PREFIX` | Optional object prefix, e.g. `loom`, matching the domain path |
| Variable | `LOOM_UPDATE_S3_ENDPOINT` | HTTPS S3-compatible API endpoint; omit for standard AWS S3 |
| Variable | `LOOM_UPDATE_REGION` | Storage region; defaults to `us-east-1` |
| Secret | `LOOM_UPDATE_ACCESS_KEY_ID` | Upload identity for this bucket/prefix |
| Secret | `LOOM_UPDATE_SECRET_ACCESS_KEY` | Its secret access key |

Restrict the upload identity to this bucket/prefix. Public clients need read-only
HTTP access, never storage credentials. Domain and bucket values are public
configuration; access keys stay in GitHub Secrets and the runner environment.
Do not paste them into chat, source files or manifests.

## Activation order

1. Provision storage, HTTPS/domain and read access; keep all current release
   gates and GitHub publications in place.
2. Add the variables/secrets above, then manually run **Sync Update Download
   Source**. It requires no application version bump just to populate storage.
3. Verify the sync job succeeds. It uploads historical referenced objects and
   current installer assets, checks their hashes/sizes, and probes the public
   installer's HTTP 206 range support before advertising any channel.
4. Build/release a new Windows installer with the configured domain embedded.
   Users on older clients need this one upgrade to learn the new source.
5. Validate a real old-to-new upgrade, primary-source download, disconnected
   primary with GitHub fallback, cancellation and interrupted download from
   actual target networks. No nationwide speed guarantee follows from local
   mocked tests.

Do not publish a mutable channel before uploading its referenced objects. The
sync workflow runs after successful release workflows, serializes sync jobs,
and can also be run manually. An upload or public-range probe failure leaves
existing channel objects untouched. Files uploaded before failure are harmless
unreferenced immutable objects; retrying the sync is safe.

## Object layout and caching

```text
releases/<GitHub-release-tag>/<original-asset-name>
channels/application-runtime/stable.json
channels/ui-runtime/stable.json
channels/host-runtime/stable.json
installer/latest.yml
```

Versioned release assets have `Cache-Control: public, max-age=31536000, immutable`.
Channels and `latest.yml` use `no-cache, max-age=0, must-revalidate`. Configure
the CDN to honor these headers. Do not cache a missing release asset for a long
period or rewrite error responses to HTTP 200.

Manifests retain their original GitHub URLs. The new client's local source
configuration maps only this repository's known update/channel paths to the
mirror. Existing path, size, SHA256/SHA512 and bootstrap-compatibility checks
remain unchanged. Account/API requests never map to the download source and
authorization/cookie headers are stripped from mirror requests.

Installer discovery uses electron-updater's generic provider at
`installer/latest.yml`. Its file paths refer to versioned release directories,
so cached metadata cannot select a same-named EXE from another release. A
discovery failure immediately retries GitHub. A later installer download error
switches subsequent updater retry checks to GitHub for the rest of that process.
Missing historical blockmaps fall back to their original GitHub release.

Application/UI/Host transfers prefer the mapped public URL and fall back on
connection/header failures and HTTP errors. A mirror has an eight-second header
deadline so an unavailable source does not consume the caller's full transfer
timeout. Corrupt content remains rejected by the existing hash checks; it is
never accepted just because the mirror returned HTTP 200. A stall after response
headers is handled by the existing transfer deadlines/retry path.

## Local operation and rollback

`npm run build:electron` writes public configuration to
`dist-electron/update-source.json`, which is included in packaged and component
builds. Release workflows get it from `LOOM_UPDATE_BUILD_SOURCE_URL`, populated
from the repository variable. An explicit runtime `LOOM_UPDATE_SOURCE_URL`
overrides the embedded setting; an explicitly empty value disables the source.
No upload credentials are embedded.

The sync command is `node scripts/sync-update-source.mjs` from `desktop-react`
after installing dependencies and compiling Electron. It needs `gh`, AWS CLI,
the public variables above and upload credentials in the environment.

Removing the repository variable disables mirroring for future builds; it does
not remove the domain from clients already installed. Keep the domain under
your control and preserve existing immutable files. During an outage clients
fall back to GitHub; to remove the source permanently, release a client without
it. Do not delete historical objects still referenced by supported clients.
