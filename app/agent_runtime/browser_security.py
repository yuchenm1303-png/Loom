from __future__ import annotations

import ipaddress
import socket
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Sequence
from urllib.parse import unquote, urlsplit

from .browser_session import (
    CLOUD_METADATA_ADDRESSES,
    INFRASTRUCTURE_HOST_NAMES,
    LOCAL_HOST_NAMES,
    BrowserURLPolicyError,
)


DNSResolver = Callable[[str], Sequence[str]]
_UNICODE_DOTS = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
# Rules that describe "somewhere on this machine or network", which the
# private-network switch governs. Every other prohibited rule is an endpoint
# that hands out credentials and is never reachable.
_LOCAL_RULES = frozenset({*LOCAL_HOST_NAMES, "*.localhost"})
_DEFAULT_PROHIBITED = tuple(sorted(_LOCAL_RULES | INFRASTRUCTURE_HOST_NAMES))


def _default_resolver(hostname: str) -> tuple[str, ...]:
    values: list[str] = []
    try:
        rows = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BrowserURLPolicyError(f"browser destination DNS lookup failed: {hostname}") from exc
    for row in rows:
        address = str(row[4][0]).split("%", 1)[0]
        if address not in values:
            values.append(address)
    if not values:
        raise BrowserURLPolicyError(f"browser destination did not resolve: {hostname}")
    return tuple(values)


def _canonical_host(raw_host: str) -> str:
    host = unquote(str(raw_host or ""))
    host = unicodedata.normalize("NFKC", host).translate(_UNICODE_DOTS)
    host = host.strip().casefold().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise BrowserURLPolicyError("browser navigation URL must contain a hostname")
    if any(ch.isspace() for ch in host) or "/" in host or "\\" in host or "@" in host:
        raise BrowserURLPolicyError("browser navigation hostname is malformed")
    try:
        return host.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise BrowserURLPolicyError("browser navigation hostname is invalid") from exc


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    value = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        pass
    if ":" in value:
        return None
    try:
        packed = socket.inet_aton(value)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _normalize_domain_rule(raw: str) -> str:
    value = str(raw or "").strip().casefold().rstrip(".")
    wildcard = value.startswith("*.")
    if wildcard:
        value = value[2:]
    if not value or "://" in value or "/" in value or "@" in value or "*" in value:
        raise ValueError(f"invalid browser domain pattern: {raw!r}")
    canonical = _canonical_host(value)
    return f"*.{canonical}" if wildcard else canonical


def _matches_domain(host: str, rule: str) -> bool:
    if rule.startswith("*."):
        suffix = rule[2:]
        return host.endswith("." + suffix)
    return host == rule


@dataclass(frozen=True, slots=True)
class BrowserSecurityPolicy:
    """Execution-layer URL policy for Loom browser navigation.

    The policy canonicalizes encoded/Unicode hostnames, recognizes alternate IPv4
    spellings accepted by OS resolvers, optionally resolves DNS, blocks non-global
    destinations by default, and enforces per-session allowed-domain rules. The
    backend must still enforce redirects and popups because post-navigation checks
    alone are too late to be a complete security boundary.
    """

    allow_private_networks: bool = False
    resolve_dns: bool = True
    prohibited_domains: tuple[str, ...] = _DEFAULT_PROHIBITED
    resolver: DNSResolver = field(default=_default_resolver, repr=False, compare=False)

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(_normalize_domain_rule(item) for item in self.prohibited_domains))
        object.__setattr__(self, "prohibited_domains", normalized)

    def validate(self, url: str, *, allowed_domains: tuple[str, ...] = ()) -> str:
        value = str(url or "").strip()
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise BrowserURLPolicyError("browser navigation only allows http/https URLs")
        if parsed.username is not None or parsed.password is not None:
            raise BrowserURLPolicyError("browser navigation URLs must not contain userinfo credentials")

        host = _canonical_host(parsed.hostname or "")
        prohibited = tuple(_normalize_domain_rule(item) for item in self.prohibited_domains)
        matched = tuple(rule for rule in prohibited if _matches_domain(host, rule))
        if matched:
            # Reaching local addresses and reaching the endpoints that hand out
            # credentials are different questions. The whole list used to be
            # skipped when private networks were allowed, so enabling one enabled
            # the other; only the local names follow that switch.
            local_only = all(rule in _LOCAL_RULES for rule in matched)
            if not local_only or not self.allow_private_networks:
                raise BrowserURLPolicyError(
                    f"browser navigation to prohibited host is blocked: {host}"
                )

        if allowed_domains:
            allowed = tuple(_normalize_domain_rule(item) for item in allowed_domains)
            if not any(_matches_domain(host, rule) for rule in allowed):
                raise BrowserURLPolicyError(
                    f"browser destination is outside this session's allowed domains: {host}"
                )

        literal = _parse_ip(host)
        if literal is not None:
            self._validate_ip(literal)
        elif self.resolve_dns and not self.allow_private_networks:
            for address in self.resolver(host):
                try:
                    resolved = ipaddress.ip_address(str(address).split("%", 1)[0])
                except ValueError as exc:
                    raise BrowserURLPolicyError("browser DNS resolver returned an invalid address") from exc
                self._validate_ip(resolved)
        return value

    def _validate_ip(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if address in CLOUD_METADATA_ADDRESSES:
            # Link-local is otherwise governed by the switch; this address is the
            # instance metadata service and hands out credentials.
            raise BrowserURLPolicyError(
                f"browser navigation to the cloud metadata service is blocked: {address}"
            )
        if not self.allow_private_networks and not address.is_global:
            raise BrowserURLPolicyError(f"browser navigation to non-public IP is blocked: {address}")


__all__ = ["BrowserSecurityPolicy", "DNSResolver"]
