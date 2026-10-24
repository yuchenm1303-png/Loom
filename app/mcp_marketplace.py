"""Read-only official MCP Registry adapter and reviewable setup plans.

Registry metadata never runs commands. Only an explicit subsequent mcp/manage
request can save/start the prepared definition. Remote text is plain data.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

REGISTRY = "https://registry.modelcontextprotocol.io"
SOURCE = "Official MCP Registry"
TTL = 900
MAX_BYTES = 2_000_000
NAME = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+")
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class Unsupported(ValueError):
    """A registry recipe needs functionality Loom cannot configure yet."""


def _text(value, limit=4096):
    return str(value or "")[:limit]


def _items(value):
    if not isinstance(value, list) or len(value) > 64 or any(not isinstance(v, dict) for v in value):
        raise Unsupported("invalid-metadata")
    return value


def _link(value):
    value = _text(value)
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


def suggested_name(name):
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", name.rsplit("/", 1)[-1])[:38].strip("-") or "server"
    return f"{slug}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"


def _value(spec, key, fields, values, strict, *, required=False):
    if spec.get("isSecret"):
        raise Unsupported("secret-argument")
    if spec.get("variables"):
        if not isinstance(spec.get("value"), str) or not isinstance(spec["variables"], dict):
            raise Unsupported("invalid-metadata")
        template = spec["value"]
        if len(spec["variables"]) > 32:
            raise Unsupported("invalid-metadata")
        for variable, definition in spec["variables"].items():
            if not ENV.fullmatch(variable) or not isinstance(definition, dict):
                raise Unsupported("invalid-metadata")
            replacement = _value(definition, f"{key}.{variable}", fields, values, strict, required=True)
            template = template.replace("{" + variable + "}", replacement)
        if re.search(r"\{[^{}]+\}", template):
            raise Unsupported("unresolved-variable")
        return template
    if "value" in spec:
        return _text(spec["value"])
    is_required = required or spec.get("isRequired") is True
    choices = spec.get("choices", [])
    if not isinstance(choices, list) or any(not isinstance(x, str) for x in choices):
        raise Unsupported("invalid-metadata")
    field = {"key": key, "label": _text(spec.get("name") or spec.get("valueHint") or key, 150),
             "description": _text(spec.get("description"), 600), "required": is_required,
             "default": _text(spec.get("default")), "placeholder": _text(spec.get("placeholder"), 200),
             "choices": choices[:64], "format": spec.get("format", "string"), "environment": False}
    fields.append(field)
    value = values.get(key, field["default"])
    if not isinstance(value, str) or len(value) > 4096 or "\x00" in value:
        raise ValueError("Setup inputs must be text shorter than 4097 characters")
    if strict:
        if is_required and not value.strip():
            raise ValueError(f"Required setup input: {field['label']}")
        if value and choices and value not in choices:
            raise ValueError(f"Choose an allowed value for {field['label']}")
        if value and field["format"] == "number":
            try:
                if not math.isfinite(float(value)):
                    raise ValueError()
            except ValueError:
                raise ValueError(f"Enter a number for {field['label']}") from None
        if value and field["format"] == "boolean" and value not in {"true", "false"}:
            raise ValueError(f"Enter true or false for {field['label']}")
    return value


def _arguments(raw, prefix, fields, values, strict):
    result = []
    for index, spec in enumerate(_items(raw)):
        value = _value(spec, f"{prefix}.{index}", fields, values, strict)
        if not value and "value" not in spec:
            continue
        if spec.get("type") == "named":
            flag = spec.get("name", "")
            if not isinstance(flag, str) or not re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9_-]*", flag):
                raise Unsupported("invalid-metadata")
            result.append(flag + ("=" + value if value else ""))
        elif spec.get("type") == "positional":
            result.append(value)
        else:
            raise Unsupported("invalid-metadata")
    return result


def _recipe(server, kind, index, values=None):
    strict = values is not None
    values = values or {}
    fields = []
    config = {"name": suggested_name(server["name"]), "transport": "http", "command": "", "args": [],
              "cwd": "", "url": "", "env_from": {}, "bearer_token_env": "", "enabled": True, "timeout_seconds": 30}
    required_auth = False
    if kind == "remote":
        remote = _items(server.get("remotes", []))[index]
        if remote.get("type") != "streamable-http":
            raise Unsupported("transport")
        spec = {"value": remote.get("url", ""), "variables": remote.get("variables", {})}
        config["url"] = _value(spec, "url", fields, values, strict)
        if re.search(r"[{}]", config["url"]):
            raise Unsupported("unresolved-variable")
        if not fields or strict:
            from app.mcp_settings import parse_server
            try:
                parse_server(config)
            except (ValueError, TypeError):
                raise Unsupported("url") from None
        headers = _items(remote.get("headers", []))
        for header in headers:
            if _text(header.get("name")).casefold() != "authorization":
                raise Unsupported("custom-headers")
            if not re.fullmatch(r"Bearer(?: \{[A-Za-z0-9_]+\})?", _text(header.get("value")), re.I):
                raise Unsupported("authorization")
            required_auth = required_auth or header.get("isRequired") is True
        return config, fields, required_auth, ""
    package = _items(server.get("packages", []))[index]
    registry = package.get("registryType")
    if package.get("transport", {}).get("type") != "stdio":
        raise Unsupported("transport")
    if package.get("fileSha256"):
        raise Unsupported("checksum")
    identifier, version = _text(package.get("identifier")), _text(package.get("version"))
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+!-]{0,127}", version):
        raise Unsupported("package-version")
    if registry == "npm":
        if not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9][a-z0-9_.-]*", identifier):
            raise Unsupported("package-identifier")
        if package.get("registryBaseUrl", "https://registry.npmjs.org").rstrip("/") != "https://registry.npmjs.org":
            raise Unsupported("package-registry")
        command, pinned, prefix = "npx", f"{identifier}@{version}", ["-y"]
    elif registry == "pypi":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
            raise Unsupported("package-identifier")
        if package.get("registryBaseUrl", "https://pypi.org").rstrip("/") != "https://pypi.org":
            raise Unsupported("package-registry")
        command, pinned, prefix = "uvx", f"{identifier}=={version}", []
    else:
        raise Unsupported("package-type")
    if package.get("runtimeHint", command) != command:
        raise Unsupported("runtime")
    # Runtime flags can change package sources or execution semantics. Support
    # ordinary packages first; show advanced launchers as manual setup.
    if package.get("runtimeArguments"):
        raise Unsupported("runtime-arguments")
    config.update(transport="stdio", command=command, args=[*prefix, pinned, *_arguments(package.get("packageArguments", []), "argument", fields, values, strict)])
    for position, env in enumerate(_items(package.get("environmentVariables", []))):
        if "value" in env or env.get("variables"):
            raise Unsupported("fixed-environment")
        name = env.get("name", "")
        if not isinstance(name, str) or not ENV.fullmatch(name):
            raise Unsupported("invalid-metadata")
        key = f"environment.{position}"
        required = env.get("isRequired") is True
        fields.append({"key": key, "label": name, "description": _text(env.get("description"), 600),
                       "required": required, "default": name if required else "", "placeholder": name,
                       "choices": [], "format": "string", "environment": True})
        source = values.get(key, name if required else "")
        if not isinstance(source, str) or (source and not ENV.fullmatch(source)):
            raise ValueError("Enter an environment variable name, not its secret value")
        if strict and required and not source:
            raise ValueError(f"Required environment mapping: {name}")
        if source:
            config["env_from"][name] = source
    return config, fields, required_auth, command


def normalize(record):
    if not isinstance(record, dict) or not isinstance(record.get("server"), dict):
        raise ValueError("Invalid registry server record")
    server = record["server"]
    name, version = server.get("name"), server.get("version")
    if not isinstance(name, str) or len(name) > 200 or not NAME.fullmatch(name) or not isinstance(version, str) or not version or len(version) > 255:
        raise ValueError("Invalid registry server identity")
    official = record.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
    status = official.get("status", "unknown")
    variants = []
    for kind, raw in (("remote", server.get("remotes", [])), ("package", server.get("packages", []))):
        try:
            entries = _items(raw)
        except Unsupported:
            entries = []
        for index, entry in enumerate(entries):
            variant = {"id": f"{kind}:{index}", "kind": kind,
                       "label": _text(entry.get("url") if kind == "remote" else entry.get("identifier"), 300),
                       "type": ("http" if entry.get("type") == "streamable-http" else _text(entry.get("type"), 30)) if kind == "remote" else _text(entry.get("registryType"), 30),
                       "supported": False, "reason": "", "inputs": [], "runtime": "", "required_auth": False}
            try:
                _, fields, auth, runtime = _recipe(server, kind, index)
                variant.update(supported=status == "active", reason="" if status == "active" else "inactive",
                               inputs=fields, required_auth=auth, runtime=runtime)
            except (Unsupported, ValueError, TypeError, KeyError, AttributeError) as exc:
                variant["reason"] = str(exc) if isinstance(exc, Unsupported) else "invalid-metadata"
            variants.append(variant)
    repository = server.get("repository") if isinstance(server.get("repository"), dict) else {}
    return {"name": name, "version": version, "title": _text(server.get("title") or name.rsplit("/", 1)[-1], 120),
            "description": _text(server.get("description"), 2000), "publisher": name.split("/")[0],
            "website": _link(server.get("websiteUrl")), "repository": _link(repository.get("url")),
            "status": _text(status, 30), "updated_at": _text(official.get("updatedAt"), 60),
            "suggested_name": suggested_name(name), "variants": variants,
            "compatible": any(v["supported"] for v in variants)}


def fetch_json(url):
    import httpx
    with httpx.Client(timeout=httpx.Timeout(15, connect=6), follow_redirects=False) as client:
        with client.stream("GET", url, headers={"Accept": "application/json", "User-Agent": "Loom-MCP-Marketplace/1"}) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("Registry response is too large")
    return json.loads(data)


class MCPMarketplace:
    def __init__(self, cache_dir, *, fetcher=fetch_json, clock=time.time):
        self.cache_dir = Path(cache_dir)
        self.fetcher, self.clock = fetcher, clock
        self._lock = threading.RLock()

    def _request(self, route, params=None, *, refresh=False):
        url = REGISTRY + "/v0.1/servers" + route
        if params:
            url += "?" + urlencode(params)
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        cached = None
        with self._lock:
            try:
                raw = path.read_bytes()
                if len(raw) <= MAX_BYTES * 2:
                    candidate = json.loads(raw)
                    if candidate.get("url") == url and isinstance(candidate.get("data"), dict) and isinstance(candidate.get("time"), (float, int)):
                        if route:
                            normalize(candidate["data"])
                        elif not isinstance(candidate["data"].get("servers"), list):
                            raise ValueError("Invalid cached registry response")
                        cached = candidate
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        now = self.clock()
        if cached and not refresh and 0 <= now - cached["time"] < TTL:
            return cached["data"], {"cached": True, "stale": False, "fetched_at": cached["time"]}
        try:
            payload = self.fetcher(url)
            if not isinstance(payload, dict) or (not route and not isinstance(payload.get("servers"), list)):
                raise ValueError("Invalid registry response")
            if route:
                normalize(payload)
            # Cache failures must not prevent browsing a successful response.
            with self._lock:
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".tmp")
                    temporary.write_text(json.dumps({"url": url, "time": now, "data": payload}), encoding="utf-8")
                    temporary.replace(path)
                    files = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                    for old in files[128:]:
                        old.unlink(missing_ok=True)
                except OSError:
                    pass
            return payload, {"cached": False, "stale": False, "fetched_at": now}
        except Exception:
            if cached:
                return cached["data"], {"cached": True, "stale": True, "fetched_at": cached["time"],
                                          "warning": "Registry unavailable; showing saved results"}
            raise RuntimeError("MCP Registry is unavailable. Retry, or use quick setup/manual configuration.") from None

    def list(self, params):
        search, cursor = params.get("search", ""), params.get("cursor", "")
        if not isinstance(search, str) or len(search) > 200 or not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError("Invalid registry search or cursor")
        payload, freshness = self._request("", {"search": search.strip(), "cursor": cursor, "limit": 24, "version": "latest"}, refresh=params.get("refresh") is True)
        servers, seen = [], set()
        for record in payload["servers"][:100]:
            try:
                server = normalize(record)
            except (ValueError, TypeError, AttributeError):
                continue
            if server["status"] in {"deleted", "deprecated"} or server["name"] in seen:
                continue
            servers.append(server)
            seen.add(server["name"])
        metadata = payload.get("metadata") or {}
        cursor = metadata.get("nextCursor", "") if isinstance(metadata, dict) else ""
        return {"source": SOURCE, "source_url": REGISTRY, "servers": servers,
                "next_cursor": cursor[:2048] if isinstance(cursor, str) else "", **freshness}

    def _detail(self, params):
        name, version = params.get("name"), params.get("version", "latest")
        if not isinstance(name, str) or len(name) > 200 or not NAME.fullmatch(name) or not isinstance(version, str) or not version or len(version) > 255 or version in {".", ".."}:
            raise ValueError("Invalid registry server identity")
        raw, freshness = self._request(f"/{quote(name, safe='')}/versions/{quote(version, safe='')}", refresh=params.get("refresh") is True)
        if raw["server"]["name"] != name or (version != "latest" and raw["server"]["version"] != version):
            raise ValueError("Registry returned a different server version")
        return raw, freshness

    def detail(self, params):
        raw, freshness = self._detail(params)
        return {"server": normalize(raw), "source": SOURCE, **freshness}

    def prepare(self, params):
        raw, freshness = self._detail(params)
        server = normalize(raw)
        variant = next((v for v in server["variants"] if v["id"] == params.get("variant")), None)
        if not variant or not variant["supported"]:
            raise ValueError("This installation method requires manual setup")
        values = params.get("inputs", {})
        if not isinstance(values, dict) or set(values) - {i["key"] for i in variant["inputs"]}:
            raise ValueError("Unknown setup inputs")
        kind, index = variant["id"].split(":")
        config, _, auth, runtime = _recipe(raw["server"], kind, int(index), values)
        from app.mcp_settings import parse_server, project
        return {"server": project(parse_server(config)), "required_auth": auth,
                "runtime": runtime, "runtime_available": bool(shutil.which(runtime)) if runtime else True,
                "registry_name": server["name"], "version": server["version"], **freshness}
