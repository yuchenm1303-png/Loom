"""Ask OpenCode Go which of its models accept image input, one model at a time.

OpenCode's `/models` listing publishes ids and nothing else -- no modalities,
no capability block -- so the only honest source for this answer is the gateway
itself.  Send each model a one-pixel PNG and read what comes back.  A 200 is the
provider confirming it; anything else is not, and the reason matters:

    "Model only supports text"      the model has no vision.  Settled.
    "This model does not support"   same, phrased by a different upstream.
    "Model is unavailable"          says nothing about vision.  The model is
    "[404] No endpoints found"      down, so it cannot be used at all today.

Run this when OpenCode adds or renames models, then copy the VISION ids into
`_OPENCODE_GO_VISION_MODELS` in loom_model_bridge.py:

    python scripts/probe_opencode_vision.py

Each probe costs one image request against the configured subscription key.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loom_model_bridge as bridge
from app.ai.model_store import ModelConfigStore
from app.ai.opencode_go_runtime import OPENCODE_GO_BASE_URL, opencode_go_protocol


# A 1x1 transparent PNG: large enough to be a real image, small enough that a
# model which accepts it is answering about the format and not about the size.
_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_DATA_URL = "data:image/png;base64," + base64.b64encode(_PIXEL_PNG).decode("ascii")

# Phrases an upstream uses to say the model itself cannot read images, as
# opposed to the gateway saying it could not reach the model at all.
_NO_VISION_MARKERS = (
    "only supports text",
    "does not support image",
    "not support image",
    "image input is not",
)
_UNREACHABLE_MARKERS = (
    "model is unavailable",
    "no endpoints found",
)


def _post(path: str, payload: dict, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(
        OPENCODE_GO_BASE_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            message = body
        return exc.code, str(message)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        return 0, f"{type(exc).__name__}: {exc}"


def probe(model: str, api_key: str) -> tuple[str, int, str]:
    """Send ``model`` one image and report the protocol, status and reason."""

    protocol = opencode_go_protocol(model)
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Loom/0.1 (coding-agent)",
        "x-opencode-session": str(uuid.uuid4()),
    }

    if protocol == "responses":
        status, detail = _post(
            "/responses",
            {
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "what color is this?"},
                            {"type": "input_image", "image_url": _DATA_URL},
                        ],
                    }
                ],
            },
            headers,
        )
    elif protocol == "messages":
        # This endpoint speaks Anthropic's dialect and rejects the bearer token
        # on its own; the runtime sends all three of these and so must a probe.
        headers = {**headers, "x-api-key": api_key, "anthropic-version": "2023-06-01"}
        status, detail = _post(
            "/messages",
            {
                "model": model,
                "max_tokens": 16,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "what color is this?"},
                            {"type": "image", "source": {"type": "url", "url": _DATA_URL}},
                        ],
                    }
                ],
            },
            headers,
        )
    else:
        status, detail = _post(
            "/chat/completions",
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "what color is this?"},
                            {"type": "image_url", "image_url": {"url": _DATA_URL}},
                        ],
                    }
                ],
            },
            headers,
        )
    return protocol, status, detail


def classify(status: int, detail: str) -> str:
    if status == 200:
        return "VISION"
    folded = detail.casefold()
    if any(marker in folded for marker in _UNREACHABLE_MARKERS):
        return "UNREACHABLE"
    if any(marker in folded for marker in _NO_VISION_MARKERS):
        return "TEXT-ONLY"
    return "REFUSED"


def main() -> int:
    api_key = bridge._opencode_go_key(ModelConfigStore(None))
    if not api_key:
        print("OpenCode Go API key is not configured; nothing to probe.", file=sys.stderr)
        return 1

    models = bridge._fetch_opencode_go_model_ids()
    if not models:
        print("OpenCode Go published no models.", file=sys.stderr)
        return 1

    print(f"probing {len(models)} models for image input\n")
    verdicts: dict[str, list[str]] = {}
    for model in models:
        protocol, status, detail = probe(model, api_key)
        verdict = classify(status, detail)
        verdicts.setdefault(verdict, []).append(model)
        reason = "" if status == 200 else f"  {status} {detail[:88]}"
        print(f"{verdict:12s} {model:32s} {protocol:16s}{reason}")
        sys.stdout.flush()

    confirmed = sorted(verdicts.get("VISION", []))
    print("\n--- paste into _OPENCODE_GO_VISION_MODELS ---")
    for model in confirmed:
        print(f'    "{model}",')
    for label in ("TEXT-ONLY", "REFUSED", "UNREACHABLE"):
        names = verdicts.get(label)
        if names:
            print(f"\n{label} ({len(names)}): {', '.join(sorted(names))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
