# OpenCode Server v2 / Loom cloud Agent pilot

**Status: internal, default OFF. No free-model relay.**

The OpenCode official headless server and this optional adapter have a separate
protocol from Loom's OpenAI-compatible model providers. Do **not** add it as
another OpenAI Chat Completions Base URL or conflate it with OpenCode Go.

## Verified cloud deployment (pilot)

The connected Tencent Cloud Linux host runs an official OpenCode v2.0.31
under a dedicated OS account, as a limited systemd unit:

- \`loom-opencode-pilot.service\`; root-owned env file and secret.
- HTTP URL \`127.0.0.1:14096\` **loopback only**, HTTP Basic Auth.
- Workspace \`/var/lib/loom-opencode/workspace\`, separate from Loom runtime.
- Unit CPU and memory cgroup limits; original Loom gateway left untouched.

The official v2 API \`GET /api/info\` responds 200 with correct credentials
and 401 without. An isolated test session was successfully created, read,
permission-inspected and deleted. Model discovery lists free DeepSeek/GLM
IDs, but no model inference was performed as part of the pilot.

## Legal / commercial boundary

OpenCode's hosted Service Terms (effective Aug 15 2026) specify internal use
and prohibit offering the hosted service to third parties and programmatically
extracting output, unless separately authorized. The Console free inference
API additionally returned 403 \`FreeTierError\` to Loom's external call.

This pilot MUST NOT be used to relay those restricted free/hosted models to
Loom users. **Seeing an OpenCode free model ID is not proof that Loom may
invoke or resell it.** The adapter explicitly rejects the \`opencode/\`,
\`opencode-go/\`, \`opencode-console/\`, \`zen/\`, and \`*-free\` families,
even when added to the allowlist.

The underlying open-source OpenCode **software** has separate licensing. An
internal BYOK setup with a model provider that explicitly permits this use
can be explored, after reviewing that provider's terms, cost, and permissions.

- https://opencode.ai/legal/terms-of-service
- https://docs.opencode.ai/docs/server/
- https://docs.opencode.ai/docs/sdk/

## How to verify without inference

From the server as an administrator:

\`\`\`bash
sudo systemctl status loom-opencode-pilot.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:14096/api/info
sudo curl -fsS -u "opencode:$(sudo cat /etc/loom-opencode/password)" \
  http://127.0.0.1:14096/api/info
\`\`\`

Expect 401 unauthenticated; authenticated response includes v2 version.
Do not copy the server password into Loom Web, the desktop browser, or logs.

The Python adapter can be used for a read-only status handshake:

\`\`\`python
from services.loom_opencode_agent.client import OpenCodeAgentConfig, OpenCodeAgentClient

# Pass a secret from a protected server-side secret store, never source code.
client = OpenCodeAgentClient(OpenCodeAgentConfig(
    base_url="http://127.0.0.1:14096",
    password=secret_from_environment,
))
print(client.status())
\`\`\`

This only verifies connectivity; it does not create an inference endpoint.

## Explicit BYOK opt-in for further private testing

The adapter starts with \`enabled=False\` and zero approved models.
To test **only your own authorized third-party BYOK model**:

1. Configure the model's own provider credential within the *separate*
   OpenCode instance and verify you have permission and a budget.
2. Set \`enabled=True\` and a short explicit
   \`permitted_models=("provider/model",)\` allowlist.
3. Only construct the client inside an administrator-controlled service.
4. The create-session request includes \`permissions=[{"action":"*",
   "resource":"*","effect":"deny"}]\`, so remote shell and file tools are not
   automatically granted. Conduct a separate security review before enabling
   any destructive or executable action.
5. Each client retains its own known session IDs; unknown IDs are rejected.
   **This in-memory check is not sufficient for multi-user isolation.**

Not yet implemented: signed-in Loom account-scoped session ownership in
persistent storage, rate limits/quotas, SSE event bridge, secure file mounts,
tenant-specific OS sandboxing, role-restricted admin UI, agent permission
approvals, verified multi-user hosted-service licensing, and production rollout.

The adapter is deliberately **not registered as a public gateway route**,
not a model picker option, and not available to Loom users. Enabling the
internal test switch alone does not make multi-user hosting safe.

## Rollback

\`sudo systemctl disable --now loom-opencode-pilot.service\` stops the
isolated pilot without changing Loom's current model policy or Go/Console
configuration. Do not remove credentials or sessions until retention needs
have been reviewed.
