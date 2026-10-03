from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .policy import DEFAULT_MODEL_IDS, PolicyStore


class PolicyError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class PolicyConfig:
    db_path: Path
    account_db_path: Path
    account_base_url: str
    model_ids: tuple[str, ...] = DEFAULT_MODEL_IDS


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer(authorization: str) -> str:
    scheme, _, token = str(authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise PolicyError(HTTPStatus.UNAUTHORIZED, "MISSING_TOKEN", "Sign in to Loom to continue.")
    return token.strip()


class AccountReader:
    """Resolve model tokens to accounts without making the policy DB authoritative.

    The account SQLite volume is mounted read-only. Model entitlement validation
    still goes through the account service, preserving its existing token and
    session checks as well as the per-account override table.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def _connect_ro(self) -> sqlite3.Connection:
        uri = f"file:{self.config.account_db_path.as_posix()}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _json(self, path: str, authorization: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.config.account_base_url.rstrip('/')}/{path.lstrip('/')}",
            headers={
                "Authorization": str(authorization or ""),
                "Accept": "application/json",
                "User-Agent": "LoomModelPolicy/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
                error = body.get("error") if isinstance(body, dict) else {}
                code = str((error or {}).get("code") or "ACCOUNT_AUTH_FAILED")
                message = str((error or {}).get("message") or "Loom account authorization failed.")
            except json.JSONDecodeError:
                code, message = "ACCOUNT_AUTH_FAILED", "Loom account authorization failed."
            raise PolicyError(exc.code, code, message) from None
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            raise PolicyError(
                HTTPStatus.BAD_GATEWAY,
                "ACCOUNT_SERVICE_UNAVAILABLE",
                "Loom account service is unavailable.",
            ) from None
        if not isinstance(payload, dict):
            raise PolicyError(
                HTTPStatus.BAD_GATEWAY,
                "ACCOUNT_RESPONSE_INVALID",
                "Loom account service returned invalid data.",
            )
        return payload

    def admin(self, authorization: str) -> dict[str, Any]:
        payload = self._json("admin/me", authorization)
        user = payload.get("user")
        if not isinstance(user, dict):
            raise PolicyError(HTTPStatus.BAD_GATEWAY, "ACCOUNT_RESPONSE_INVALID", "Invalid admin identity.")
        if str(user.get("role") or "") not in {"owner", "admin"}:
            raise PolicyError(HTTPStatus.FORBIDDEN, "ADMIN_REQUIRED", "Administrator access is required.")
        return user

    def user_id_for_authorization(self, authorization: str) -> int:
        token = _bearer(authorization)
        if not token.startswith("loom_model_"):
            payload = self._json("auth/me", authorization)
            user = payload.get("user")
            if not isinstance(user, dict) or int(user.get("id") or 0) <= 0:
                raise PolicyError(HTTPStatus.UNAUTHORIZED, "INVALID_TOKEN", "Loom sign-in has expired.")
            return int(user["id"])

        now = int(time.time())
        try:
            with self._connect_ro() as db:
                row = db.execute(
                    """SELECT users.id, users.status
                    FROM model_credentials
                    JOIN sessions ON sessions.id = model_credentials.session_id
                    JOIN users ON users.id = model_credentials.user_id
                    WHERE model_credentials.token_hash = ?
                      AND model_credentials.expires_at > ?
                      AND sessions.revoked_at IS NULL
                      AND sessions.refresh_expires_at > ?
                      AND sessions.user_id = model_credentials.user_id""",
                    (_token_hash(token), now, now),
                ).fetchone()
        except sqlite3.Error as exc:
            raise PolicyError(
                HTTPStatus.BAD_GATEWAY,
                "ACCOUNT_DATABASE_UNAVAILABLE",
                "Loom account identity store is unavailable.",
            ) from exc
        if row is None:
            raise PolicyError(
                HTTPStatus.UNAUTHORIZED,
                "INVALID_MODEL_TOKEN",
                "Built-in model authorization has expired. Sign in again.",
            )
        if str(row["status"]) != "active":
            raise PolicyError(HTTPStatus.FORBIDDEN, "ACCOUNT_DISABLED", "This account is not active.")
        return int(row["id"])

    def model_access(self, authorization: str) -> dict[str, Any]:
        payload = self._json("models/access", authorization)
        access = payload.get("access")
        if not isinstance(access, dict):
            raise PolicyError(
                HTTPStatus.BAD_GATEWAY,
                "ACCOUNT_RESPONSE_INVALID",
                "Loom account service returned invalid model access data.",
            )
        return access

    def admin_user_model_access(self, user_id: int, authorization: str) -> dict[str, Any]:
        payload = self._json(f"admin/users/{int(user_id)}/model-access", authorization)
        access = payload.get("access")
        if not isinstance(access, dict):
            raise PolicyError(
                HTTPStatus.BAD_GATEWAY,
                "ACCOUNT_RESPONSE_INVALID",
                "Loom account service returned invalid model access data.",
            )
        return access

    def ensure_user(self, user_id: int, authorization: str) -> None:
        self._json(f"admin/users/{int(user_id)}", authorization)


class ModelPolicyApplication:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self.store = PolicyStore(config.db_path, config.model_ids)
        self.accounts = AccountReader(config)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "loom-model-policy"}

    def access(self, authorization: str) -> dict[str, Any]:
        user_id = self.accounts.user_id_for_authorization(authorization)
        legacy = self.accounts.model_access(authorization)
        return {"access": self.store.effective_access(user_id, legacy)}

    def check(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        model_id = str(body.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id is required")
        user_id = self.accounts.user_id_for_authorization(authorization)
        legacy = self.accounts.model_access(authorization)
        access = self.store.effective_access(user_id, legacy, [model_id])
        decision = next((item for item in access.get("decisions", []) if item.get("model_id") == model_id), None)
        if not isinstance(decision, dict):
            raise PolicyError(HTTPStatus.INTERNAL_SERVER_ERROR, "POLICY_DECISION_MISSING", "Model policy could not resolve this model.")
        return {"decision": decision, "access": access}

    def admin_state(self, authorization: str) -> dict[str, Any]:
        self.accounts.admin(authorization)
        return self.store.snapshot()

    def admin_group(self, group_id: int, authorization: str) -> dict[str, Any]:
        self.accounts.admin(authorization)
        return {"group": self.store.group_detail(group_id)}

    def admin_effective(self, user_id: int, authorization: str) -> dict[str, Any]:
        self.accounts.admin(authorization)
        legacy = self.accounts.admin_user_model_access(user_id, authorization)
        return {"access": self.store.effective_access(user_id, legacy)}

    def admin_user_policy(self, user_id: int, authorization: str) -> dict[str, Any]:
        self.accounts.admin(authorization)
        legacy = self.accounts.admin_user_model_access(user_id, authorization)
        return self.store.user_detail(user_id, legacy)

    def admin_audit(self, authorization: str) -> dict[str, Any]:
        self.accounts.admin(authorization)
        return {"events": self.store.audit()}

    def set_global(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        return {
            "model": self.store.set_global_rule(
                int(actor["id"]), str(body.get("model_id") or ""), body.get("enabled")
            )
        }

    def set_global_model_group(self, group_id: str, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        return {"model_group": self.store.set_global_model_group(int(actor["id"]), group_id, body.get("enabled"))}

    def set_global_bulk(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        model_ids = body.get("model_ids")
        if model_ids is not None and not isinstance(model_ids, list):
            raise ValueError("model_ids must be a list")
        return {
            "models": self.store.set_global_bulk(
                int(actor["id"]), body.get("enabled"), model_ids
            )
        }

    def set_user_model(self, user_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        self.accounts.ensure_user(user_id, authorization)
        rule = self.store.set_user_rule(
            int(actor["id"]), user_id, str(body.get("model_id") or ""), body.get("enabled")
        )
        legacy = self.accounts.admin_user_model_access(user_id, authorization)
        return {"rule": rule, "policy": self.store.user_detail(user_id, legacy)}

    def set_user_models_bulk(self, user_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        self.accounts.ensure_user(user_id, authorization)
        model_ids = body.get("model_ids")
        if model_ids is not None and not isinstance(model_ids, list):
            raise ValueError("model_ids must be a list")
        self.store.set_user_rules_bulk(
            int(actor["id"]), user_id, body.get("enabled"), model_ids
        )
        legacy = self.accounts.admin_user_model_access(user_id, authorization)
        return {"policy": self.store.user_detail(user_id, legacy)}

    def clear_user_models(self, user_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        self.accounts.ensure_user(user_id, authorization)
        model_ids = body.get("model_ids")
        if model_ids is not None and not isinstance(model_ids, list):
            raise ValueError("model_ids must be a list")
        self.store.clear_user_rules(int(actor["id"]), user_id, model_ids)
        legacy = self.accounts.admin_user_model_access(user_id, authorization)
        return {"policy": self.store.user_detail(user_id, legacy)}

    def create_group(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        return {
            "group": self.store.create_group(
                int(actor["id"]), str(body.get("name") or ""), body.get("enabled", True)
            )
        }

    def update_group(self, group_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        name = body.get("name") if "name" in body else None
        enabled = body.get("enabled") if "enabled" in body else None
        return {
            "group": self.store.update_group(
                int(actor["id"]), group_id, name=name, enabled=enabled
            )
        }

    def delete_group(self, group_id: int, authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        self.store.delete_group(int(actor["id"]), group_id)
        return {"ok": True}

    def set_member(self, group_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        user_id = int(body.get("user_id") or 0)
        enabled = body.get("enabled")
        if enabled is True:
            self.accounts.ensure_user(user_id, authorization)
        return {
            "group": self.store.set_membership(
                int(actor["id"]), group_id, user_id, enabled
            )
        }

    def set_members_bulk(self, group_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        user_ids = body.get("user_ids")
        if not isinstance(user_ids, list):
            raise ValueError("user_ids must be a list")
        normalized = sorted({int(item) for item in user_ids if int(item) > 0})
        for user_id in normalized:
            self.accounts.ensure_user(user_id, authorization)
        return {
            "group": self.store.set_members_bulk(
                int(actor["id"]), group_id, normalized
            )
        }

    def set_group_model(self, group_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        return {
            "group": self.store.set_group_rule(
                int(actor["id"]),
                group_id,
                str(body.get("model_id") or ""),
                body.get("enabled"),
            )
        }

    def set_group_models_bulk(self, group_id: int, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self.accounts.admin(authorization)
        model_ids = body.get("model_ids")
        if model_ids is not None and not isinstance(model_ids, list):
            raise ValueError("model_ids must be a list")
        return {
            "group": self.store.set_group_rules_bulk(
                int(actor["id"]), group_id, body.get("enabled"), model_ids
            )
        }


class PolicyRequestHandler(BaseHTTPRequestHandler):
    server_version = "LoomModelPolicy/1"
    _MAX_BODY = 1024 * 1024

    @property
    def application(self) -> ModelPolicyApplication:
        return self.server.application  # type: ignore[attr-defined]

    def _path(self) -> str:
        return urlsplit(self.path).path.rstrip("/") or "/"

    def _authorization(self) -> str:
        return str(self.headers.get("Authorization") or "")

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise PolicyError(HTTPStatus.BAD_REQUEST, "INVALID_BODY", "Invalid Content-Length.") from exc
        if length <= 0:
            return {}
        if length > self._MAX_BODY:
            raise PolicyError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE", "Request is too large.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise PolicyError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Request body must be a JSON object.")
        return payload

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _run(self, fn) -> None:
        try:
            self._write(HTTPStatus.OK, fn())
        except PolicyError as exc:
            self._write(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except KeyError as exc:
            self._write(HTTPStatus.NOT_FOUND, {"error": {"code": "NOT_FOUND", "message": str(exc).strip("'")}})
        except (TypeError, ValueError) as exc:
            self._write(HTTPStatus.BAD_REQUEST, {"error": {"code": "INVALID_POLICY", "message": str(exc)}})
        except sqlite3.Error:
            self._write(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": {"code": "POLICY_DATABASE_ERROR", "message": "Model policy database operation failed."}},
            )

    def do_GET(self) -> None:
        import re
        path = self._path()
        auth = self._authorization()
        if path == "/healthz":
            return self._run(self.application.health)
        if path == "/v1/access":
            return self._run(lambda: self.application.access(auth))
        if path == "/v1/admin/state":
            return self._run(lambda: self.application.admin_state(auth))
        if path == "/v1/admin/audit":
            return self._run(lambda: self.application.admin_audit(auth))
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)", path):
            return self._run(lambda: self.application.admin_group(int(match.group(1)), auth))
        if match := re.fullmatch(r"/v1/admin/users/(\d+)/effective", path):
            return self._run(lambda: self.application.admin_effective(int(match.group(1)), auth))
        if match := re.fullmatch(r"/v1/admin/users/(\d+)/policy", path):
            return self._run(lambda: self.application.admin_user_policy(int(match.group(1)), auth))
        self._write(HTTPStatus.NOT_FOUND, {"error": {"code": "NOT_FOUND", "message": "Endpoint not found."}})

    def do_POST(self) -> None:
        import re
        path = self._path()
        auth = self._authorization()
        body = self._json_body()
        if path == "/v1/check":
            return self._run(lambda: self.application.check(body, auth))
        if path == "/v1/admin/global":
            return self._run(lambda: self.application.set_global(body, auth))
        if match := re.fullmatch(r"/v1/admin/model-groups/([^/]+)", path):
            return self._run(lambda: self.application.set_global_model_group(match.group(1), body, auth))
        if path == "/v1/admin/global/bulk":
            return self._run(lambda: self.application.set_global_bulk(body, auth))
        if match := re.fullmatch(r"/v1/admin/users/(\d+)/models", path):
            return self._run(lambda: self.application.set_user_model(int(match.group(1)), body, auth))
        if match := re.fullmatch(r"/v1/admin/users/(\d+)/models/bulk", path):
            return self._run(lambda: self.application.set_user_models_bulk(int(match.group(1)), body, auth))
        if match := re.fullmatch(r"/v1/admin/users/(\d+)/models/reset", path):
            return self._run(lambda: self.application.clear_user_models(int(match.group(1)), body, auth))
        if path == "/v1/admin/groups":
            return self._run(lambda: self.application.create_group(body, auth))
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)", path):
            return self._run(lambda: self.application.update_group(int(match.group(1)), body, auth))
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)/members", path):
            return self._run(lambda: self.application.set_member(int(match.group(1)), body, auth))
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)/members/bulk", path):
            return self._run(lambda: self.application.set_members_bulk(int(match.group(1)), body, auth))
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)/models", path):
            return self._run(lambda: self.application.set_group_model(int(match.group(1)), body, auth))
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)/models/bulk", path):
            return self._run(lambda: self.application.set_group_models_bulk(int(match.group(1)), body, auth))
        self._write(HTTPStatus.NOT_FOUND, {"error": {"code": "NOT_FOUND", "message": "Endpoint not found."}})

    def do_DELETE(self) -> None:
        import re
        path = self._path()
        auth = self._authorization()
        if match := re.fullmatch(r"/v1/admin/groups/(\d+)", path):
            return self._run(lambda: self.application.delete_group(int(match.group(1)), auth))
        self._write(HTTPStatus.NOT_FOUND, {"error": {"code": "NOT_FOUND", "message": "Endpoint not found."}})

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("LOOM_MODEL_POLICY_QUIET") == "1":
            return
        super().log_message(format, *args)


class PolicyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: ModelPolicyApplication) -> None:
        super().__init__(address, PolicyRequestHandler)
        self.application = application


def _model_ids_from_env() -> tuple[str, ...]:
    raw = str(os.environ.get("LOOM_BUILTIN_MODEL_IDS") or "").strip()
    if not raw:
        return DEFAULT_MODEL_IDS
    items = tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    return items or DEFAULT_MODEL_IDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom built-in model policy service")
    parser.add_argument("--host", default=os.environ.get("LOOM_MODEL_POLICY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOOM_MODEL_POLICY_PORT", "8792")))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = PolicyConfig(
        db_path=Path(os.environ.get("LOOM_MODEL_POLICY_DB") or "/data/model-policy.db"),
        account_db_path=Path(os.environ.get("LOOM_ACCOUNT_DB_READONLY") or "/account-data/accounts.db"),
        account_base_url=str(os.environ.get("LOOM_ACCOUNT_INTERNAL_URL") or "http://127.0.0.1:8787/v1"),
        model_ids=_model_ids_from_env(),
    )
    server = PolicyServer((str(args.host), int(args.port)), ModelPolicyApplication(config))
    print(f"Loom Model Policy listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
