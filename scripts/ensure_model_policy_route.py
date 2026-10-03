"""Add the policy route to an existing shared Caddyfile without replacing vhosts."""
import pathlib
import sys


def ensure_route(text: str) -> str:
    host = "account.smirel.com {"
    start = text.index(host) + len(host)
    end = text.find("\n}\n", start)
    if end < 0:
        raise ValueError("Account vhost closing brace not found")
    if "handle_path /policy/*" in text[start:end]:
        return text
    route = """
    handle_path /policy/* {
        reverse_proxy loom-model-policy:8792 {
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
            header_up X-Forwarded-Host {host}
        }
    }
"""
    return text[:start] + route + text[start:]


if __name__ == "__main__":
    source, destination = map(pathlib.Path, sys.argv[1:])
    destination.write_text(ensure_route(source.read_text()), encoding="utf-8")
