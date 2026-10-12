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

Model bindings and profiles represent all three states. Unknown is not registered
as the VISION capability. The request validator still allows an image request to
an unknown endpoint and preserves its image parts. Only an explicit unsupported
state filters historical image parts and blocks new image attachments. The endpoint
may reject an unknown request; the error is not silently treated as an image-free
success. Authentication failures, outages, or unlisted models never prove a lack
of vision support.

Cold launch arguments, hot switching, thread persistence/restoration, metadata
refresh, and the model settings UI preserve unknown. A connection test can read
published modality evidence, but listing models does not prove visual reasoning.
An endpoint that omits trustworthy modality metadata cannot be definitively
classified without a separate real image test; the UI labels it unknown.

Cloud deployment requires rebuilding the model policy and gateway services as well
as publishing the Host runtime and desktop application updates.
