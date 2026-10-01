from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


ANT_LING_MODELS = (
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "Ling-3.0-tiny",
    "Ling-2.6-1T",
    "Ring-2.6-1T",
    "Ling-2.6-flash",
)


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
    upstream_timeout_seconds: float = 180.0


class LoomModelGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def _account_access(self, authorization: str) -> dict[str, Any]:
        token = str(authorization or "").strip()
        if not token.lower().startswith("bearer "):
            raise GatewayError(HTTPStatus.UNAUTHORIZED, "MISSING_TOKEN", "Sign in to Loom to use built-in models.")
        request = urllib.request.Request(
            f"{self.config.account_base_url.rstrip('/')}/models/access",
            headers={"Authorization": token, "Accept": "application/json", "User-Agent": "LoomModelGateway/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
                message = str(body.get("error", {}).get("message") or "Loom account authorization failed.")
                code = str(body.get("error", {}).get("code") or "ACCOUNT_AUTH_FAILED")
            except json.JSONDecodeError:
                message, code = "Loom account authorization failed.", "ACCOUNT_AUTH_FAILED"
            raise GatewayError(exc.code, code, message) from None
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "ACCOUNT_SERVICE_UNAVAILABLE", "Loom account service is unavailable.") from None
        access = payload.get("access") if isinstance(payload, dict) else None
        if not isinstance(access, dict):
            raise GatewayError(HTTPStatus.BAD_GATEWAY, "ACCOUNT_RESPONSE_INVALID", "Loom account service returned invalid model access data.")
        return access

    @staticmethod
    def _allowed_models(access: dict[str, Any]) -> set[str]:
        if not bool(access.get("enabled", True)):
            return set()
        models = access.get("models")
        if not isinstance(models, list):
            return set()
        return {str(model).strip().casefold() for model in models if str(model).strip()}

    def catalog(self, authorization: str) -> dict[str, Any]:
        allowed = self._allowed_models(self._account_access(authorization))
        data = []
        for model in ANT_LING_MODELS:
            if model.casefold() not in allowed:
                continue
            data.append({
                "id": model,
                "object": "model",
                "owned_by": "loom/ant-ling",
                "provider": "ant-ling",
                "vision": model.casefold().endswith("-vl"),
            })
        return {"object": "list", "data": data}

    def authorize_model(self, authorization: str, model: str) -> None:
        normalized = str(model or "").strip()
        if not normalized:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "MODEL_REQUIRED", "model is required.")
        supported = {item.casefold() for item in ANT_LING_MODELS}
        if normalized.casefold() not in supported:
            raise GatewayError(HTTPStatus.NOT_FOUND, "MODEL_NOT_FOUND", f"Model {normalized!r} is not a Loom built-in model.")
        allowed = self._allowed_models(self._account_access(authorization))
        if normalized.casefold() not in allowed:
            raise GatewayError(HTTPStatus.FORBIDDEN, "MODEL_NOT_ENTITLED", "This Loom account is not entitled to this built-in model.")
        if not self.config.ant_ling_api_key:
            raise GatewayError(HTTPStatus.SERVICE_UNAVAILABLE, "UPSTREAM_NOT_CONFIGURED", "Ant Ling upstream is not configured on the Loom gateway.")

    def upstream_request(self, body: bytes, authorization: str):
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise GatewayError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be a JSON object.")
        self.authorize_model(authorization, str(payload.get("model") or ""))
        request = urllib.request.Request(
            f"{self.config.ant_ling_base_url.rstrip('/')}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.ant_ling_api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream, application/json",
                "User-Agent": "LoomModelGateway/1",
            },
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
                self._write_json(HTTPStatus.OK, {"ok": True, "service": "loom-model-gateway"})
                return
            if path == "/v1/models":
                self._write_json(HTTPStatus.OK, self.application.catalog(self._authorization()))
                return
            raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")
        except GatewayError as exc:
            self._write_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path != "/v1/chat/completions":
                raise GatewayError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > self._MAX_BODY_BYTES:
                raise GatewayError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE", "Request is too large.")
            body = self.rfile.read(length)
            upstream = self.application.upstream_request(body, self._authorization())
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
