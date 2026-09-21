from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from urllib import error, request

DEFAULT_BASE_URL = "https://muxway.dev/v1"
DEFAULT_KEY_ENV = "LOOM_RELAY_API_KEY"


def _read_key(env_name: str) -> str:
    value = str(os.environ.get(env_name) or "").strip()
    if not value:
        raise SystemExit(
            f"Missing {env_name}. Set it in the build environment; do not pass customer Relay keys on the command line."
        )
    return value


def _models_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def _validate_key(base_url: str, api_key: str) -> list[str]:
    http_request = request.Request(
        _models_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "Loom/provisioning-helper",
        },
    )
    try:
        with request.urlopen(http_request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"Relay rejected the credential while validating /models: HTTP {exc.code}") from exc
    except OSError as exc:
        raise SystemExit(f"Could not reach Relay while validating /models: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit("Relay /models did not return valid JSON") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise SystemExit("Relay /models response does not contain a data list")
    models: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model = str(item.get("id") or "").strip()
            if model:
                models.append(model)
    if not models:
        raise SystemExit("Relay credential is valid but exposes no models")
    return models


def _write_provisioning(path: Path, base_url: str, api_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "loom.managed_relay.provisioning.v1",
        "baseUrl": base_url.rstrip("/"),
        "apiKey": api_key,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write a one-time Loom managed Relay provisioning file for a customer package. "
            "The API key is read from an environment variable so it does not land in shell history."
        )
    )
    parser.add_argument(
        "--output",
        default="loom-relay-credential.json",
        help="Provisioning file path to create. Put this next to loom_model_bridge.py in the customer package.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Muxway Relay OpenAI-compatible base URL.")
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV, help="Environment variable containing the customer Relay key.")
    parser.add_argument("--validate", action="store_true", help="Call /models before writing and print the allowed model IDs.")
    args = parser.parse_args(argv)

    api_key = _read_key(args.key_env)
    base_url = str(args.base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise SystemExit("--base-url must be a complete http/https URL")

    models: list[str] = []
    if args.validate:
        models = _validate_key(base_url, api_key)

    output = Path(args.output).expanduser().resolve()
    _write_provisioning(output, base_url, api_key)

    print(f"Wrote Relay provisioning file: {output}")
    print("The file contains a customer Relay credential. Do not commit it or share it separately.")
    if models:
        print("Models exposed by this credential:")
        for model in models:
            print(f"- {model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
