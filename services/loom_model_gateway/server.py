from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from services.loom_model_policy.catalog import selection_for_model, is_opencode_console_free
from .routes import PROVIDERS, request_route, server_upstreams
from .usage import UsageService


ANT_LING_MODELS = (
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "Ling-3.0-tiny",
    "Ling-2.6-1T",
    "Ring-2.6-1T",
    "Ling-2.6-flash",
)

ANT_LING_DEFAULT_MODEL = "Ling-3.0-flash"

def _policy_model_id(model: str, group_id: str = "ant-ling") -> str:
    return selection_for_model(group_id, str(model or "").strip())


class GatewayError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class GatewayConfig:
    account_base_url: str
    ant_ling_base_url: str
    ant_ling_api_key: str
    model_policy_base_url: str = ""
    upstream_timeout_seconds: float = 180.0
    intern_base_url: str = "https://discovery-api.intern-ai.org.cn/v1"
    intern_api_key: str = ""
    upstreams: dict[str, tuple[str, str]] = field(default_factory=dict)


class LoomModelGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.usage_service = UsageService()

    def usage(self, authorization: str, group_id: str, model: str, force=False):
        self.authorize_model(authorization, model, group_id)
        base, key = self._upstream(group_id)
        result = self.usage_service.query(group_id, base, key, model, force)
        # These are the upstream account/key's shared limits, not an allocation
        # independently granted to the signed-in Loom user or selected model.
        result["scope"] = "loom-shared-" + result.get("scope", "provider-account")
        result.pop("dashboardUrl", None)
        return result

    def _upstream(self, group_id: str) -> tuple[str, str]:
        if group_id not in PROVIDERS:
            raise GatewayError(HTTPStatus.NOT_FOUND, "PROVIDER_NOT_FOUND", "Unknown built-in provider.")
        if group_id in self.config.upstreams:
            return self.config.upstreams[group_id]
        if group_id == "ant-ling":
            return self.config.ant_ling_base_url, self.config.ant_ling_api_key
        if group_id == "intern":
            return self.config.intern_base_url, self.config.intern_api_key
        return PROVIDERS[group_id][0], ""

    def _account_access(self, authorization: str) -> dict[str, Any]:
        token = str(authorization or "").strip()
        if not token.lower().startswith("bearer "):
            raise GatewayError(HTTPStatus.UNAUTHORIZED, "MISSING_TOKEN", "Sign in to Loom to use built-in models.")
        policy_base = str(self.config.model_policy_base_url or "").strip().rstrip("/")
        if policy_base:
            endpoint = f"{policy_base}/access"
            service_name = "model policy"
        else:
            endpoint = f"{self.config.account_base_url.rstrip('/')}/models/access"
            service_name = "account"
        request = urllib.request.Request(
            endpoint,
            headers={"Authorization": token, "Accept": "application/json", "User-Agent": "LoomModelGateway/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
                message = str(body.get("error", {}).get("message") or "Loom model authorization failed.")
                code = str(body.get("error", {}).get("code") or "MODEL_AUTH_FAILED")
            except json.JSONDecodeError:
                message, code = "Loom model authorization failed.", "MODEL_AUTH_FAILED"
            raise GatewayError(exc.code, code, message) from None
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            raise GatewayError(
                HTTPStatus.BAD_GATEWAY,
                "MODEL_POLICY_UNAVAILABLE" if policy_base else "ACCOUNT_SERVICE_UNAVAILABLE",
                f"Loom {service_name} service is unavailable.",
            ) from None
        access = payload.get("access") if isinstance(payload, dict) else None
        if not isinstance(access, dict):
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "ACCOUNT_RESPONSE_INVALID", "Loom model policy returned invalid access data.")
        return access

    @staticmethod
    def _allowed_models(access: dict[str, Any]) -> set[str]:
        if not bool(access.get("enabled", True)):
            return set()
        models = access.get("models")
        if not isinstance(models, list):
            return set()
        return {str(model).strip().casefold() for model in models if str(model).strip()}

    def catalog(self, authorization: str, group_id: str = "ant-ling") -> dict[str, Any]:
        access = self._account_access(authorization)
        allowed = self._allowed_models(access)
        data = []
        for model in self._catalog_models(access, group_id):
            access_id = _policy_model_id(model, group_id) if self.config.model_policy_base_url else model
            if access_id.casefold() not in allowed:
                continue
            data.append({
                "id": model,
                "object": "model",
                "owned_by": "loom/" + group_id,
                "provider": group_id,
                "vision": next((row.get("image_input", {}).get("supported") for row in access.get("catalog", {}).get("models", []) if row.get("group_id") == group_id and row.get("model") == model), None),
            })
        return {"object": "list", "data": data}

    def _catalog_models(self, access: dict[str, Any], group_id: str = "ant-ling") -> list[str]:
        catalog = access.get("catalog")
        if self.config.model_policy_base_url and isinstance(catalog, dict):
            return [str(item["model"]) for item in catalog.get("models", [])
                if item.get("group_id") == group_id and item.get("available")]
        return list(ANT_LING_MODELS) if group_id == "ant-ling" else []

    def authorize_model(self, authorization: str, model: str, group_id: str = "ant-ling") -> None:
        normalized = str(model or "").strip()
        if not normalized:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "MODEL_REQUIRED", "model is required.")
        access = self._account_access(authorization)
        supported = {item.casefold() for item in self._catalog_models(access, group_id)}
        if normalized.casefold() not in supported:
            raise GatewayError(HTTPStatus.NOT_FOUND, "MODEL_NOT_FOUND", f"Model {normalized!r} is not a Loom built-in model.")
        allowed = self._allowed_models(access)
        access_id = _policy_model_id(normalized, group_id) if self.config.model_policy_base_url else normalized
        if access_id.casefold() not in allowed:
            raise GatewayError(
                HTTPStatus.FORBIDDEN,
                "MODEL_NOT_ENTITLED",
                "This Loom account is not allowed to use this built-in model by the current model policy.",
            )
        # OpenCode Console explicitly supports unauthenticated free chat.
        # Never send a Go key to Console, or permit keyless paid models.
        if not self._upstream(group_id)[1] and not (
            group_id == "opencode-console" and is_opencode_console_free(normalized)
        ):
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "UPSTREAM_NOT_CONFIGURED", "Selected upstream is not configured on the Loom gateway.")

    def upstream_request(self, body: bytes, authorization: str, group_id: str = "ant-ling", endpoint: str = "chat/completions", *, session_id: str = ""):
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be a JSON object.")
        self.authorize_model(authorization, str(payload.get("model") or ""), group_id)
        if endpoint not in {"chat/completions", "responses", "messages"} or (endpoint != "chat/completions" and group_id != "opencode-go"):
            raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")
        base_url, api_key = self._upstream(group_id)
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": "LoomModelGateway/2",
        }
        # Keep zero-priced calls anonymous even when an unrelated paid Console
        # key is configured; billing changes must fail closed, not bill silently.
        free_console = group_id == "opencode-console" and is_opencode_console_free(str(payload.get("model") or ""))
        if api_key and not free_console:
            headers["Authorization"] = f"Bearer {api_key}"
        if endpoint == "messages":
            headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        if group_id == "opencode-go" and session_id and len(session_id) <= 256 and not any(ord(c) < 32 for c in session_id):
            headers["x-opencode-session"] = session_id
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/{endpoint}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            return urllib.request.urlopen(request, timeout=self.config.upstream_timeout_seconds)
        except urllib.error.HTTPError as exc:
            return exc
        except (OSError, urllib.error.URLError) as exc:
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "UPSTREAM_UNAVAILABLE", "The model upstream could not be reached.") from exc


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server_version = "LoomModelGateway/1"
    _MAX_BODY_BYTES = 16 * 1024 * 1024

    @property
    def application(self) -> LoomModelGateway:
        return self.server.application  # type: ignore[attr-defined]

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Loom-Gateway", "model")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorization(self) -> str:
        return str(self.headers.get("Authorization") or "")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path == "/healthz":
                self._write_json(HTTPStatus.OK, {"ok": True, "service": "loom-model-gateway", "providers": {group: bool(self.application._upstream(group)[1]) for group in PROVIDERS}})
                return
            try:
                group_id, endpoint = request_route(path)
            except ValueError:
                raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.") from None
            if endpoint == "models":
                self._write_json(HTTPStatus.OK, self.application.catalog(self._authorization(), group_id))
                return
            if endpoint == "usage":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self._write_json(HTTPStatus.OK, self.application.usage(self._authorization(), group_id,
                    params.get("model", [""])[0], params.get("force", [""])[0] == "1"))
                return
            raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")
        except GatewayError as exc:
            self._write_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            try:
                group_id, endpoint = request_route(path)
            except ValueError:
                raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.") from None
            if endpoint in {"models", "usage"}:
                raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > self._MAX_BODY_BYTES:
                raise GatewayError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE", "Request is too large.")
            body = self.rfile.read(length)
            upstream = self.application.upstream_request(body, self._authorization(), group_id, endpoint, session_id=str(self.headers.get("x-opencode-session") or ""))
            try:
                status = int(getattr(upstream, "status", getattr(upstream, "code", 502)) or 502)
                content_type = str(upstream.headers.get("Content-Type") or "application/json")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Loom-Gateway", "model")
                self.send_header("Connection", "close")
                self.end_headers()
                reader = getattr(upstream, "read1", upstream.read)
                while True:
                    chunk = reader(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                upstream.close()
                self.close_connection = True
        except GatewayError as exc:
            self._write_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("LOOM_MODEL_GATEWAY_QUIET") == "1":
            return
        super().log_message(format, *args)


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: LoomModelGateway) -> None:
        super().__init__(address, GatewayRequestHandler)
        self.application = application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom built-in model gateway")
    parser.add_argument("--host", default=os.environ.get("LOOM_MODEL_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOOM_MODEL_GATEWAY_PORT", "8790")))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = GatewayConfig(
        account_base_url=str(os.environ.get("LOOM_ACCOUNT_INTERNAL_URL") or "http://127.0.0.1:8787/v1"),
        ant_ling_base_url=str(os.environ.get("LOOM_ANT_LING_BASE_URL") or "https://api.ant-ling.com/v1"),
        ant_ling_api_key=str(os.environ.get("LOOM_ANT_LING_API_KEY") or "").strip(),
        intern_base_url=str(os.environ.get("LOOM_INTERN_BASE_URL") or "https://discovery-api.intern-ai.org.cn/v1").strip(),
        intern_api_key=str(os.environ.get("LOOM_INTERN_API_KEY") or "").strip(),
        model_policy_base_url=str(os.environ.get("LOOM_MODEL_POLICY_INTERNAL_URL") or "").strip(),
        upstreams=server_upstreams(os.environ),
        upstream_timeout_seconds=max(10.0, float(os.environ.get("LOOM_MODEL_UPSTREAM_TIMEOUT", "180"))),
    )
    server = GatewayServer((str(args.host), int(args.port)), LoomModelGateway(config))
    print(f"Loom Model Gateway listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
