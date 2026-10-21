"""Read-only provider usage. Never return upstream bodies or credentials."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

NAMES = {"minimax": "MiniMax", "deepseek": "DeepSeek", "opencode-go": "OpenCode Go", "opencode-console": "OpenCode Console",
         "intern": "Intern Discovery", "ant-ling": "Ant Ling", "managed-relay": "Muxway Relay"}
CONSOLES = {"intern": "https://discovery.intern-ai.org.cn/token-plan/home",
            "ant-ling": "https://chat.ant-ling.com", "opencode-go": "https://opencode.ai/console", "opencode-console": "https://opencode.ai/console"}


def number(value):
    if isinstance(value, bool) or value is None or not str(value).strip():
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def snapshot(group, status="ok", **extra):
    return {"status": status, "provider": NAMES[group], "checkedAt": int(time.time() * 1000),
            "windows": [], "balances": [], "scope": "provider-account", **extra}


def endpoint(group, base):
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return None
    host = parsed.hostname
    if group == "deepseek" and host == "api.deepseek.com":
        return "https://api.deepseek.com/user/balance"
    if group == "minimax" and host in {"api.minimaxi.com", "api.minimax.io"}:
        return "https://www." + host.removeprefix("api.") + "/v1/token_plan/remains"
    if group == "opencode-go" and host == "opencode.ai":
        return "https://opencode.ai/zen/go/v1/usage"
    if group == "managed-relay" and host == "muxway.dev":
        return "https://muxway.dev/api/usage/token"
    return None


def parse_usage(group, payload, model, checked_at=None):
    result = snapshot(group)
    if checked_at is not None:
        result["checkedAt"] = checked_at
    if not isinstance(payload, dict):
        raise ValueError("invalid usage response")
    if group == "deepseek":
        for row in payload.get("balance_infos", []):
            if not isinstance(row, dict) or not isinstance(row.get("currency"), str) or number(row.get("total_balance")) is None:
                continue
            item = {"currency": row["currency"], "total": str(row["total_balance"])}
            for field, key in [("toppedUp", "topped_up_balance"), ("granted", "granted_balance")]:
                if number(row.get(key)) is not None:
                    item[field] = str(row[key])
            result["balances"].append(item)
    elif group == "minimax":
        code = payload.get("base_resp", {}).get("status_code", 0)
        if str(code) != "0":
            raise ValueError("provider business error")
        rows = (payload.get("data") or payload).get("model_remains", [])
        row = next((r for r in rows if r.get("model_name") == model), None)
        if row is None and model.lower().startswith("minimax-m"):
            row = next((r for r in rows if r.get("model_name") == "general"), None)
        if row is None:
            raise ValueError("selected model quota missing")
        for prefix, label, end, duration in [("current_interval", "5h", "end_time", "remains_time"),
                                             ("current_weekly", "Weekly", "weekly_end_time", "weekly_remains_time")]:
            if number(row.get(prefix + "_status")) == 3:
                continue
            percent = number(row.get(prefix + "_remaining_percent"))
            total, used = number(row.get(prefix + "_total_count")), number(row.get(prefix + "_usage_count"))
            if percent is None and total is not None and total > 0 and used is not None:
                percent = 100 * (total - used) / total
            if percent is None:
                continue
            window = {"label": label, "remainingPercent": max(0, min(100, percent))}
            reset, remain = number(row.get(end)), number(row.get(duration))
            if reset is not None and reset > 0:
                window["resetsAt"] = reset * 1000 if reset < 1e12 else reset
            elif remain is not None and remain >= 0:
                window["resetsAt"] = result["checkedAt"] + remain
            result["windows"].append(window)
    elif group == "opencode-go":
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("missing Go usage")
        for key, label in [("rolling", "5h"), ("weekly", "Weekly"), ("monthly", "Monthly")]:
            row = usage.get(key)
            if not isinstance(row, dict):
                continue
            used = number(row.get("percent"))
            if used is None:
                continue
            window = {"label": label, "remainingPercent": max(0, min(100, 100 - used))}
            reset = row.get("resetsAt")
            if isinstance(reset, str):
                try:
                    stamp = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                    if stamp.tzinfo is not None:
                        window["resetsAt"] = int(stamp.timestamp() * 1000)
                except ValueError:
                    pass
            result["windows"].append(window)
        if not result["windows"] and payload.get("billing", {}).get("balance") is True:
            return snapshot(group, "unsupported", code="BALANCE_AMOUNT_UNAVAILABLE",
                message="当前为余额计费，Go 接口未返回余额金额；不能据此判断为零。")
    elif group == "managed-relay":
        if payload.get("code") is False or (payload.get("code") is not None and payload["code"] is not True and payload["code"] not in (0, "0", 200, "200")):
            raise ValueError("relay business error")
        data = payload.get("data")
        if not isinstance(data, dict) or payload.get("success") is False:
            raise ValueError("missing relay token usage")
        # New API reports token grant/use/availability, not the user's wallet.
        if data.get("unlimited_quota") is True:
            return snapshot(group, message="此 Key 未设置独立额度上限；不代表上游账号余额无限。", scope="api-key")
        if number(data.get("total_available")) is not None:
            result["balances"].append({"currency": "额度单位", "total": str(data["total_available"])})
            result["scope"] = "api-key"
    if not result["balances"] and not result["windows"]:
        raise ValueError("no reported usage")
    return result


class UsageService:
    def __init__(self):
        self._guard = threading.Lock()
        self._cache = {}
        self._locks = {}
        self._successful = {}

    def query(self, group, base, key, model, force=False):
        started = time.monotonic()
        url = endpoint(group, base)
        if not key:
            return snapshot(group, "unconfigured", code="MISSING_UPSTREAM_KEY", message="服务器尚未配置此供应商凭证。")
        if not url:
            return snapshot(group, "unsupported", code="NO_VERIFIED_API", dashboardUrl=CONSOLES.get(group),
                message=("Intern 余额接口需要控制台登录态，API Key 不能直接读取；请在供应商控制台查看。"
                    if group == "intern" else "此供应商尚无已验证的 API Key 额度查询接口；请在供应商控制台查看，不代表没有额度。"))
        cache_key = hashlib.sha256((url + "\0" + key).encode()).hexdigest()
        with self._guard:
            lock = self._locks.setdefault(cache_key, threading.Lock())
        with lock:
            with self._guard:
                cached = self._cache.get(cache_key)
            now = time.monotonic()
            if cached is None or (force and cached[0] < started) or now - cached[0] >= (60 if cached[1] is not None else 15):
                cached = self._fetch(url, key)
                with self._guard:
                    self._cache[cache_key] = cached
                    if len(self._cache) > 64:
                        oldest = next(iter(self._cache))
                        self._cache.pop(oldest)
                        self._locks.pop(oldest, None)
                        self._successful.pop(oldest, None)
            _, payload, checked, code = cached
        if payload is None:
            if code in {"HTTP_401", "HTTP_403"}:
                with self._guard:
                    self._successful.pop(cache_key, None)
            if code in {"HTTP_404", "HTTP_405"}:
                return snapshot(group, "unsupported", checkedAt=checked, code="USAGE_ENDPOINT_UNAVAILABLE",
                    message="当前服务商未开放此额度查询接口，请在其控制台查看；不代表没有额度。")
            if code not in {"HTTP_401", "HTTP_403"}:
                with self._guard:
                    previous = self._successful.get(cache_key)
                if previous and time.monotonic() - previous[0] < 900:
                    try:
                        return {**parse_usage(group, previous[1], model, previous[2]), "stale": True, "code": code,
                            "message": "刷新暂时失败，显示上次成功查询的额度；不是实时余额。"}
                    except (ValueError, TypeError, AttributeError, KeyError):
                        pass
            return snapshot(group, "error", checkedAt=checked, code=code,
                message=("供应商拒绝读取此凭证的额度，请检查服务器 Key 的区域、套餐类型和查询权限；不代表额度为零。"
                    if code in {"HTTP_401", "HTTP_403"} else
                    "供应商额度接口暂时无法读取（" + code + "），请稍后刷新；这不代表额度为零。"))
        try:
            result = parse_usage(group, payload, model, checked)
            with self._guard:
                self._successful[cache_key] = (time.monotonic(), payload, checked)
            return result
        except (ValueError, TypeError, AttributeError, KeyError):
            return snapshot(group, "error", checkedAt=checked, code="INVALID_USAGE_RESPONSE",
                message="供应商未返回此模型的有效额度数据；请确认套餐类型或在控制台查看。")

    @staticmethod
    def _fetch(url, key):
        payload, code = None, "NETWORK_ERROR"
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
            "Accept": "application/json", "User-Agent": "Loom/0.1 (coding-agent)"})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("oversize")
                payload = json.loads(raw)
        except urllib.error.HTTPError as exc:
            code = "HTTP_" + str(exc.code)
            exc.close()
        except (OSError, ValueError, urllib.error.URLError):
            pass
        return time.monotonic(), payload, int(time.time() * 1000), code
