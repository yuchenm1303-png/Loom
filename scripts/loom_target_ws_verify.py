#!/usr/bin/env python3
"""WebSocket verification against loom.smirel.com."""
from __future__ import annotations
import socket
import ssl
import sys

HOST = "loom.smirel.com"
PORT = 443


def handshake(path, headers):
    sock = socket.create_connection((HOST, PORT), timeout=10)
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    sock = ctx.wrap_socket(sock, server_hostname=HOST)
    request_lines = [
        f"GET {path} HTTP/1.1", f"Host: {HOST}", "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    for k, v in headers.items():
        request_lines.append(f"{k}: {v}")
    request_lines.append("")
    request_lines.append("")
    request = "\r\n".join(request_lines).encode("ascii")
    sock.sendall(request)
    buf = b""
    sock.settimeout(8)
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    head, _, rest = buf.partition(b"\r\n\r\n")
    head_str = head.decode("iso-8859-1", errors="replace")
    status_line, *header_lines = head_str.split("\r\n")
    print(f"  status: {status_line}")
    response_headers = {}
    for line in header_lines:
        if ":" in line:
            k, _, v = line.partition(":")
            response_headers[k.strip().lower()] = v.strip()
    return response_headers, rest


def main():
    print("=== /api/ws/browser (no cookie) ===")
    hdrs, _ = handshake("/api/ws/browser", {})
    _UPGRADE = hdrs.get('upgrade', 'n/a')
    print(f"  upgrade: {_UPGRADE}")
    print(f"  status_code: {hdrs.get('status_code', 'n/a')}")

    print()
    print("=== /api/ws/device (no Authorization) ===")
    hdrs, _ = handshake("/api/ws/device", {})
    print(f"  upgrade: {hdrs.get('upgrade', 'n/a')}")

    print()
    print("=== /api/ws/browser (Origin=https://loom.smirel.com) ===")
    hdrs, _ = handshake("/api/ws/browser", {"Origin": "https://loom.smirel.com"})
    print(f"  upgrade: {hdrs.get('upgrade', 'n/a')}")

    print()
    print("=== /api/ws/device (with bogus bearer) ===")
    hdrs, _ = handshake("/api/ws/device", {"Authorization": "Bearer xxxxxxxxxxxx"})
    print(f"  upgrade: {hdrs.get('upgrade', 'n/a')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())