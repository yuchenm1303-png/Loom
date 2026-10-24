"""Write-only MCP bearer tokens in the OS credential store."""
import hashlib
import os

SERVICE = 'loom-agent/mcp'
PREFIX = 'LOOM_MCP_STORED_'


def reference(name):
    return PREFIX + hashlib.sha256(name.encode()).hexdigest().upper()


def resolve(name):
    if name.startswith(PREFIX):
        try:
            import keyring
            return keyring.get_password(SERVICE, name)
        except Exception:
            return None
    return os.environ.get(name)


def write(name, token):
    try:
        import keyring
        keyring.set_password(SERVICE, name, token)
    except Exception as exc:
        raise RuntimeError('OS credential store unavailable; use a server environment variable instead') from exc


def delete(name):
    if not name.startswith(PREFIX):
        return
    try:
        import keyring
        keyring.delete_password(SERVICE, name)
    except Exception:
        pass
