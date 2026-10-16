# Windows incremental download optimization

The 0.1.74 -> 0.1.78 update required 8,935,994 network bytes in 77 serial ranges,
71 smaller than 256 KiB. Electron's updater network session resolved the release
URL directly (no system proxy). A 256 KiB range took 1.8 seconds while a 4 MiB
range reached 3.8 MB/s. The user's browser download history separately recorded
a 324 MB installer downloaded in 62.5 seconds.

The new downloader coalesces nearby ranges, caps each request at 4 MiB and runs
at most four workers. The same update becomes 13 requests totaling 11,116,981
bytes. Extra gap downloads are bounded; the implementation never buffers an
entire installer. The initial GitHub redirect is resolved once and reused, with
cross-origin credential headers removed. Network progress excludes disk copy
time.

Validation used Electron itself, the published 0.1.78 assets, and the original
0.1.74 installer from Downloads. Reconstructed output was 324,237,263 bytes and
matched the published SHA512. Final redirect-reusing implementation completed
two live runs in 10.85 and 15.39 seconds including blockmap retrieval, disk copy
and final hash verification. A subsequent serial upstream baseline did not
finish in its approximately 69-second remaining comparison budget; the helper
was terminated. These observations are network-dependent, not a guaranteed
speed multiplier.

17 regression tests cover range planning, concurrency limits, relocated copy
blocks, redirects and header handling, truncated/oversized/unsupported ranges,
SHA512 failure, retries, cancellation, full-download fallback, pending blockmap
promotion and existing update scheduling. Electron and renderer type checks pass.

Only differential transfer is overridden through the NsisUpdater subclass.
Upstream release discovery, cache handling, configured publisher signature
verification and installation stay in place. Failed transfers finish/abort all
workers before the upstream full-download fallback starts. Cancellation is
propagated instead of triggering fallback. Cache download-metrics.jsonl records
counts, sizes and durations without account data, credentials or signed URLs.

## Slow-network follow-up, 2026-10-16

The local metrics contain repeated full-download fallbacks for 0.1.87; they
do not record sufficient detail to prove the cause of each historical fallback.
The current updater still downloads directly from GitHub, without a first-party
China CDN. A small live probe of the 0.1.90 installer returned HTTP 206 and
262,144 bytes in 3.97 seconds (about 66 KB/s including connection/redirect time).
This is a single-machine observation, not a nationwide throughput benchmark.

Range and blockmap transfers now use a 30-second inactivity timeout instead of
a fixed 20-second total deadline. Each received chunk renews the deadline, so
a steadily progressing slow request is retained. Stalled requests and caller
cancellation still abort. Expired signed asset URLs are discarded on 401/403/404
so the existing bounded range retry obtains a fresh redirect from the original
release URL; credentials remain stripped across origins. SHA512 verification
and the four-worker limit are unchanged.

All 19 download regression tests passed, including continuous slow transfers,
stalls, cancellation, expired signed URLs, range validation and checksum failure.
This source change has not been released or installed by this follow-up. It
reduces avoidable retries; it does not supply domestic CDN capacity. A managed
download origin and testing from actual target networks remain necessary before
claiming reliable China-region download speed.
