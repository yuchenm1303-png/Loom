# Account-owned built-in models

MiniMax, DeepSeek, OpenCode Go, Muxway Relay, Ant Ling and Intern now share the Loom account gateway. Desktop catalog and launch resolution never read provider keys or call upstream discovery. The authenticated server catalog controls live model availability. Personal saved API connections retain their independent endpoint and credential.

A shared route registry supplies provider groups, gateway paths, server environment aliases and protocol endpoints. The gateway authorizes each selected model using account policy, then injects only the server's upstream credential. OpenCode Go preserves its Chat/Responses/Messages router while using the configured gateway in both cold bootstrap and hot switches. Messages authentication and session headers are translated at the gateway.

Existing built-in conversations discard cached provider platforms at turn boundaries, resolve the canonical gateway again and materialize the current account credential. Old local endpoints and machine keys cannot survive catalog reprojection. Personal connections remain separate. Fresh catalogs include the preferred MiniMax-M3.1-Flash-Preview identity, with final availability still decided by the server.

Production gateway deployment loads .env.models and .env.catalog, matching catalog discovery. No provider keys are embedded in Git, application metadata or installers. Public routes reject anonymous requests; gateway health reports only provider configuration booleans. Reference deployment backups remain under /opt/loom-account/gateway-backup-*.

Validation includes empty-key desktops, all six gateway groups, authorization denial, upstream credential replacement, raw protocol/body preservation, all three OpenCode transports, old conversation migration, account change rebinding and personal connection isolation. Existing direct-key/discovery tests were updated for server ownership. A stale release-gate assertion requiring the removed conversation spinner was aligned with the current surface transition; no conversation UI was changed here.
