# Image input capability

Image input has three states: supported, unsupported, and unknown. Unknown is a
real state, not an alias for either boolean. Model IDs, provider names, `-vl`
suffixes, historical allowlists, and successful text/model-list requests are not
image capability evidence.

The shared `loom_image_capability.py` contract parses explicit booleans and
complete input modality lists from provider model metadata. Conflicting evidence
remains unknown. The policy service retains this evidence separately from model
permissions. The gateway returns the same declaration; Desktop records it by
endpoint and model so separate Host bridge processes use the same evidence.
Provider evidence expires after 24 hours. Different endpoints cannot share it.
A saved connection can explicitly declare supported/unsupported, or remain unknown.

For sparse listings, policy synchronization supplements existing rows using
models.dev's exact provider/model input modalities. Canonical OpenCode Go/Console,
MiniMax international/China (including their documented API aliases), and DeepSeek
endpoints qualify; custom endpoints never inherit public endpoint evidence. This
does not add models or change permissions, and explicit/conflicting upstream
metadata takes precedence. Public metadata is fetched without credentials, cached
for one hour, and retained for at most 24 hours during outages. Failed or malformed
supplemental metadata leaves capabilities unknown without failing model discovery.
Console configuration also preserves nested `modalities.input` declarations.
Both broad `image` declarations and concrete MIME types such as `image/png` are
image evidence. Text/video-only modality lists are not image evidence. Unknown
manifest rows follow the same enrichment path; explicit declarations still win.
Personal connection tests use the same origin/provider matching, and an unset
saved preference no longer overwrites discovered support. Explicit user settings
and endpoint-scoped rejection evidence retain precedence. Other groups can supply
their own explicit model metadata; missing declarations remain unknown, never a
model-name-based negative or a blanket claim of support.

On 2026-10-25 the configured MiniMax official endpoint's `MiniMax-M3.1-Flash`
correctly returned `LEFT=red;RIGHT=blue` for a generated red/blue test image.
The policy-to-Desktop integration fixture covers this official route separately
from the OpenCode Go model, with exact endpoint identity and MIME metadata.

On 2026-10-20, `minimax-m3.1-flash-free` on the configured OpenCode Go endpoint
correctly identified a synthetic image's red left half and blue right half.
Its exact models.dev entry declares image input; Go's `/models` row itself only
published identity fields. This explains the previous unknown warning and is
covered by the real HTTP policy-to-Desktop catalog integration fixture.

Model bindings and profiles represent all three states. Unknown is not registered
as the VISION capability. The request validator still allows an image request to
an unknown endpoint and preserves its image parts. Only an explicit unsupported
state filters historical image parts and blocks new image attachments. The endpoint
may reject an unknown request; the error is not silently treated as an image-free
success. If an image-bearing request is explicitly rejected as text-only (HTTP
400/422), the platform records endpoint/model-scoped negative evidence. A new
image in the latest user message produces a non-retryable input notice. A text
turn with only historical images gets one corrected request with image placeholders;
the durable history remains unchanged. Stream recovery is permitted only before
any event is emitted, so partial output and tool calls are never replayed.
Authentication failures, outages, malformed image URLs or oversized files never prove a lack
of vision support.

Provider-rejection evidence is retained for 24 hours and cannot be overwritten
by a metadata refresh reporting unknown. A saved connection's explicit setting
can override it. Other endpoints/models do not inherit it. Desktop displays
unknown support before sending a new image, blocks known-unsupported attachments
without discarding them, and renders capability rejection as a neutral localized
notice instead of a transport stack trace. Plain text remains sendable when no
new images are attached, even if the conversation has historical images.

Cold launch arguments, hot switching, thread persistence/restoration, metadata
refresh, and the model settings UI preserve unknown. A connection test can read
published modality evidence, but listing models does not prove visual reasoning.
An endpoint that omits trustworthy modality metadata cannot be definitively
classified without a separate real image test; the UI labels it unknown.

Cloud deployment requires rebuilding the model policy and gateway services as well
as publishing the Host runtime and desktop application updates.
