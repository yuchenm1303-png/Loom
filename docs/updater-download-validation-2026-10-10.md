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
