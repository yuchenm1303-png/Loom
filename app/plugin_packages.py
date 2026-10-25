"""Acquisition and validation of declarative plugin bundles, without execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 2048
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class PluginPackageError(ValueError):
    pass


def valid_name(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or value in {".", ".."}:
        raise PluginPackageError("Plugin names must contain 1–128 letters, digits, dots, underscores or hyphens")
    return value


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def inside(root, relative, *, exists=True):
    raw = str(relative).replace("\\", "/")
    if not raw or PurePosixPath(raw).is_absolute() or any(part in {"..", ""} or ":" in part for part in raw.split("/") if part != "."):
        raise PluginPackageError("Component paths must stay inside the plugin")
    target = root.joinpath(*PurePosixPath(raw).parts)
    resolved = target.resolve(strict=exists)
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise PluginPackageError("Component path escapes the plugin") from None
    probe = target
    while probe != root and probe != probe.parent:
        if linked(probe):
            raise PluginPackageError("Plugin components cannot use symlinks or junctions")
        probe = probe.parent
    return resolved


def copy_tree(source, destination):
    source = Path(source)
    if linked(source):
        raise PluginPackageError("Plugin source cannot be a symlink or junction")
    source = source.resolve(strict=True)
    destination = Path(destination)
    # Never walk into our own staging directory when source is an ancestor.
    if destination.resolve().is_relative_to(source):
        raise PluginPackageError("Choose a plugin folder, not an ancestor of the runtime directory")
    destination.mkdir(parents=True, exist_ok=False)
    count = size = 0
    for current, directories, files in os.walk(source, followlinks=False):
        parent = Path(current)
        directories[:] = [name for name in directories if name not in {".git", "node_modules", ".venv", "__pycache__"}]
        for name in directories:
            if linked(parent / name):
                raise PluginPackageError("Plugin bundle contains a directory link")
        output = destination / parent.relative_to(source)
        output.mkdir(parents=True, exist_ok=True)
        for name in files:
            item = parent / name
            if linked(item) or not item.is_file():
                raise PluginPackageError("Plugin bundle contains a link or non-regular file")
            count += 1
            size += item.stat().st_size
            if count > MAX_FILES or size > MAX_BYTES:
                raise PluginPackageError("Plugin exceeds the 2048-file / 64 MB bundle limit")
            shutil.copy2(item, output / name)


def tree_hash(root):
    digest = hashlib.sha256()
    count = size = 0
    for item in sorted(root.rglob("*")):
        if linked(item):
            raise PluginPackageError("Plugin bundle contains a filesystem link")
        if item.is_file():
            count += 1
            size += item.stat().st_size
            if count > MAX_FILES or size > MAX_BYTES:
                raise PluginPackageError("Plugin exceeds the bundle size limit")
            digest.update(item.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


def _extract(archive, destination):
    if archive.stat().st_size > MAX_BYTES:
        raise PluginPackageError("Plugin archive exceeds 64 MB")
    destination.mkdir(parents=True)
    size = 0
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist()) > MAX_FILES * 2:
            raise PluginPackageError("Plugin archive contains too many entries")
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/").rstrip("/")
            if not name:
                continue
            parts = name.split("/")
            if any(not part or part in {".", ".."} or ":" in part or part.endswith((".", " ")) or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I) for part in parts):
                raise PluginPackageError("Unsafe path in plugin ZIP")
            if name.casefold() in seen or stat.S_ISLNK(info.external_attr >> 16):
                raise PluginPackageError("Plugin ZIP contains duplicate paths or links")
            seen.add(name.casefold())
            size += info.file_size
            if size > MAX_BYTES:
                raise PluginPackageError("Expanded plugin archive exceeds 64 MB")
            target = inside(destination, name, exists=False)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)


def acquire(source, temporary):
    metadata = dict(source) if isinstance(source, dict) else {"url": str(source).strip()}
    if set(metadata) - {"url", "path", "ref"}:
        raise PluginPackageError("Unsupported plugin source fields")
    raw, subpath, ref = metadata.get("url", ""), metadata.get("path", ""), metadata.get("ref", "")
    if not all(isinstance(v, str) for v in (raw, subpath, ref)) or not raw:
        raise PluginPackageError("Choose a local folder, ZIP, or HTTPS Git repository")
    local = Path(raw).expanduser()
    revision = ""
    if local.exists():
        if linked(local):
            raise PluginPackageError("Source cannot be a filesystem link")
        if local.is_dir():
            root = local.resolve()
        elif local.is_file() and zipfile.is_zipfile(local):
            root = temporary / "extracted"
            _extract(local, root)
        else:
            raise PluginPackageError("Local source must be a folder or ZIP archive")
        metadata["url"] = str(local.resolve())
    else:
        parsed = urlsplit(raw)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PluginPackageError("Remote sources require HTTPS without embedded credentials")
        if parsed.path.endswith(".zip"):
            import httpx
            archive = temporary / "source.zip"
            total = 0
            with httpx.Client(timeout=30, follow_redirects=True, max_redirects=3) as client:
                with client.stream("GET", raw) as response, archive.open("wb") as writer:
                    response.raise_for_status()
                    if urlsplit(str(response.url)).scheme != "https":
                        raise PluginPackageError("Archive redirects must remain HTTPS")
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise PluginPackageError("Downloaded plugin exceeds 64 MB")
                        writer.write(chunk)
            root = temporary / "extracted"
            _extract(archive, root)
        else:
            if parsed.hostname == "github.com":
                parts = parsed.path.strip("/").split("/")
                if len(parts) < 2:
                    raise PluginPackageError("GitHub source must include owner and repository")
                if len(parts) > 2:
                    if len(parts) < 4 or parts[2] != "tree":
                        raise PluginPackageError("Use a repository or GitHub /tree/<ref>/<folder> URL")
                    ref = ref or unquote(parts[3])
                    subpath = subpath or unquote("/".join(parts[4:]))
                raw = f"https://github.com/{parts[0]}/{parts[1].removesuffix('.git')}.git"
                metadata = {"url": raw, "path": subpath, "ref": ref}
            if ref and (not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", ref) or ref.startswith("-") or ".." in ref):
                raise PluginPackageError("Invalid Git branch, tag, or commit")
            root = temporary / "repository"
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
            hooks = temporary / "empty-hooks"
            hooks.mkdir()
            options = ["git", "-c", f"core.hooksPath={hooks}", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", "-c", "http.sslBackend=openssl"]
            def git(*args):
                try:
                    result = subprocess.run([*options, *args], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    raise PluginPackageError("Git is unavailable or acquisition timed out") from None
                if result.returncode:
                    raise PluginPackageError("Could not acquire repository; check its URL, ref and network access")
                return result.stdout.strip()
            git("init", str(root))
            git("-C", str(root), "fetch", "--depth=1", "--no-tags", "--", raw, ref or "HEAD")
            git("-C", str(root), "checkout", "--detach", "FETCH_HEAD")
            revision = git("-C", str(root), "rev-parse", "HEAD")
    # Standard ZIP wrappers contain one repository directory.
    if not subpath and not any((root / name).exists() for name in ("loom-plugin.json", "plugin.json", ".claude-plugin", "SKILL.md", "skills")):
        children = list(root.iterdir())
        if len(children) == 1 and children[0].is_dir():
            root = children[0]
    if subpath:
        root = inside(root, subpath)
    if not root.is_dir():
        raise PluginPackageError("Plugin subdirectory was not found")
    return root, metadata, revision


def _json(path):
    if path.stat().st_size > 512 * 1024:
        raise PluginPackageError("Plugin manifest is too large")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise PluginPackageError("Plugin manifest must be an object")
    return value


def namespace(plugin, local):
    name = f"plugin.{plugin}.{local}"
    if len(name) <= 64:
        return name
    return f"plugin.{plugin[:20]}.{local[:19]}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"


def inspect_package(root, *, name_hint=""):
    from app.agent_runtime.skills import parse_skill_document
    candidates = [root / "loom-plugin.json", root / ".claude-plugin" / "plugin.json", root / "plugin.json"]
    manifest_path = next((p for p in candidates if p.is_file()), None)
    manifest = _json(manifest_path) if manifest_path else {}
    name = manifest.get("name") or name_hint or root.name
    name = valid_name(name)
    if manifest.get("schemaVersion", 1) != 1:
        raise PluginPackageError("Unsupported plugin manifest schemaVersion")
    unsupported = [key for key in ("hooks", "agents", "lspServers", "outputStyles", "settings", "modules", "main", "entrypoint", "tools", "ui") if manifest.get(key)]
    for folder in ("hooks", "agents"):
        if (root / folder).is_dir() and any((root / folder).iterdir()) and folder not in unsupported:
            unsupported.append(folder)
    skills = []
    def paths(key, default):
        value = manifest.get(key, default)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value) or len(value) > 64:
            raise PluginPackageError(f"{key} must contain relative component paths")
        if manifest_path and ".claude-plugin" in manifest_path.parts:
            value = [*default, *value]
        return list(dict.fromkeys(inside(root, item) for item in value))
    skill_defaults = ["skills"] if (root / "skills").is_dir() else (["."] if not manifest_path else [])
    for directory in paths("skills", skill_defaults):
        files = [directory] if directory.is_file() else sorted(directory.rglob("SKILL.md"))
        for file in files:
            if file.name != "SKILL.md" or any(p.startswith(".") for p in file.relative_to(root).parts[:-1]):
                continue
            if file.stat().st_size > 128 * 1024:
                raise PluginPackageError("A bundled skill is too large")
            parsed = parse_skill_document(file.read_text(encoding="utf-8-sig"), default_name=file.parent.name)
            skills.append({"name": namespace(name, parsed.name), "local_name": parsed.name, "description": parsed.description,
                           "path": file.relative_to(root).as_posix(), "kind": "skill"})
    command_defaults = ["commands"] if (root / "commands").is_dir() else []
    for directory in paths("commands", command_defaults):
        files = [directory] if directory.is_file() else sorted(directory.rglob("*.md"))
        for file in files:
            text = file.read_text(encoding="utf-8-sig")
            if len(text.encode()) > 128 * 1024:
                raise PluginPackageError("A bundled command is too large")
            parsed = parse_skill_document(text, default_name=file.stem)
            skills.append({"name": namespace(name, parsed.name), "local_name": parsed.name, "description": parsed.description,
                           "path": file.relative_to(root).as_posix(), "kind": "command"})
    if len(skills) > 128 or len({s["name"].casefold() for s in skills}) != len(skills):
        raise PluginPackageError("Plugin has too many skills or duplicate skill/command names")
    servers = manifest.get("mcpServers", ".mcp.json" if (root / ".mcp.json").is_file() else {})
    if isinstance(servers, str):
        servers = _json(inside(root, servers))
    if isinstance(servers, dict) and "mcpServers" in servers:
        servers = servers["mcpServers"]
    if not isinstance(servers, dict) or len(servers) > 16:
        raise PluginPackageError("mcpServers must be a mapping with at most 16 entries")
    env_names = set()
    mcp = []
    for server_name, raw in servers.items():
        valid_name(server_name)
        config, required = mcp_definition(name, server_name, raw, root)
        env_names.update(required)
        mcp.append({"name": config["name"], "local_name": server_name, "raw": raw})
    if not skills and not mcp:
        raise PluginPackageError("Plugin contains no supported Skills, commands, or MCP servers")
    return {"name": name, "version": str(manifest.get("version", "0.0.0"))[:100],
            "description": str(manifest.get("description", ""))[:2000],
            "format": "claude" if manifest_path and ".claude-plugin" in manifest_path.parts else "loom" if manifest_path else "skills",
            "skills": skills, "mcp": mcp, "credentials": sorted(env_names), "unsupported": unsupported}


def mcp_definition(plugin, name, raw, root, bindings=None):
    from app.mcp_settings import parse_server, project
    if not isinstance(raw, dict):
        raise PluginPackageError("MCP server definitions must be objects")
    raw = dict(raw)
    transport = raw.pop("type", raw.get("transport", "stdio" if raw.get("command") else "http"))
    if transport in {"streamable-http", "http"}:
        transport = "http"
    config = {"name": namespace(plugin, name), "transport": transport}
    needed = set()
    for key in ("command", "args", "cwd", "url", "timeout_seconds", "enabled"):
        if key in raw:
            config[key] = raw[key]
    def expand(value):
        if not isinstance(value, str):
            raise PluginPackageError("MCP command arguments must be text")
        value = value.replace("${CLAUDE_PLUGIN_ROOT}", str(root)).replace("${LOOM_PLUGIN_ROOT}", str(root))
        if "${" in value:
            raise PluginPackageError("Use env/environment references for credentials; only plugin-root placeholders are supported in commands")
        return value
    for key in ("command", "cwd", "url"):
        if key in config:
            config[key] = expand(config[key])
    if "args" in config:
        if not isinstance(config["args"], list):
            raise PluginPackageError("MCP arguments must be an array")
        config["args"] = [expand(value) for value in config["args"]]
    if transport == "stdio":
        config.setdefault("cwd", str(root))
    env_from = raw.get("env_from", {})
    if not isinstance(env_from, dict) or not isinstance(raw.get("env", {}), dict):
        raise PluginPackageError("MCP environment references must be a mapping")
    env_from = dict(env_from)
    for key, value in raw.get("env", {}).items():
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(value))
        if not match:
            raise PluginPackageError("Do not bundle literal environment values or credentials; use ${VARIABLE_NAME}")
        env_from[key] = match.group(1)
    for source in env_from.values():
        if not isinstance(source, str) or not ENV.fullmatch(source):
            raise PluginPackageError("Invalid MCP environment reference")
        if source.startswith("LOOM_MCP_STORED_"):
            raise PluginPackageError("Plugin manifests cannot reference host credential-store identifiers")
        needed.add(source)
    token_env = raw.get("bearer_token_env", "")
    if not isinstance(raw.get("headers", {}), dict):
        raise PluginPackageError("MCP headers must be a mapping")
    for header, value in raw.get("headers", {}).items():
        match = re.fullmatch(r"Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(value), re.I)
        if str(header).lower() != "authorization" or not match:
            raise PluginPackageError("Only bearer-token environment references are supported for plugin HTTP headers")
        token_env = match.group(1)
    if token_env:
        if not isinstance(token_env, str) or not ENV.fullmatch(token_env):
            raise PluginPackageError("Invalid bearer-token reference")
        if token_env.startswith("LOOM_MCP_STORED_"):
            raise PluginPackageError("Plugin manifests cannot reference host credential-store identifiers")
        needed.add(token_env)
    bindings = bindings or {}
    config["env_from"] = {child: bindings.get(source, source) for child, source in env_from.items()}
    config["bearer_token_env"] = bindings.get(token_env, token_env)
    unknown = set(raw) - {"transport", "command", "args", "cwd", "url", "timeout_seconds", "enabled", "env", "env_from", "headers", "bearer_token_env"}
    if unknown:
        raise PluginPackageError("Unsupported MCP settings: " + ", ".join(sorted(unknown)))
    return project(parse_server(config)), needed
