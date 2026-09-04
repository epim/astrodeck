"""Relay runtime configuration (env-driven; no secrets in code).

The relay is deployed by the owner (Docker + Fly.io), so its config comes from
environment variables. Keys:

  RELAY_BIND_HOST          (default 0.0.0.0)
  RELAY_BIND_PORT          (default 8080)
  RELAY_ORIGIN             public host browsers talk to, e.g. relay.example
                           (exact browser Origin validation). default "".
  RELAY_HTTPS              "1" if the public edge is HTTPS (forces cookie
                           Secure). default "1".
  RELAY_PING_INTERVAL_S    keepalive ping period. default 10.
  RELAY_PING_MAX_MISSES    PONG misses before tunnel teardown. default 3.
  RELAY_WS_EGRESS_MAX      per-browser /ws egress buffer bound. default 64.
  RELAY_HTTP_RATE          per-IP HTTP token-bucket refill (tokens/s). default 20.
  RELAY_HTTP_BURST         per-IP HTTP burst capacity. default 40.
  RELAY_WS_RATE            per-IP /ws-open refill (tokens/s). default 1.
  RELAY_WS_BURST           per-IP /ws-open burst capacity. default 5.
  RELAY_MAX_BODY           max tunnelled request body bytes. default 8388608.
  RELAY_SCOPE_RATE         per-IP /scope handshake refill. default 2.
  RELAY_SCOPE_BURST        per-IP /scope handshake burst. default 10.
  RELAY_SCOPE_PENDING_MAX  process-wide incomplete handshakes. default 32.
  RELAY_SCOPE_HELLO_TIMEOUT_S seconds allowed for the first HELLO. default 5.
  RELAY_REQUEST_BODY_TIMEOUT_S max idle time between upload chunks. default 30.
  RELAY_REQUEST_TOTAL_TIMEOUT_S total upload lifetime. default 900.
  RELAY_HTTP_EGRESS_CHUNKS response chunks buffered per browser. default 8.
  RELAY_HTTP_MAX_PER_HOME  concurrent HTTP exchanges per home. default 32.
  RELAY_HTTP_MAX_TOTAL     concurrent HTTP exchanges process-wide. default 128.
  RELAY_WS_MAX_PER_HOME    concurrent browser websockets per home. default 16.
  RELAY_WS_MAX_TOTAL       concurrent browser websockets process-wide. default 64.
  RELAY_UPSTREAM_TIMEOUT_S idle response/write deadline. default 30.
  RELAY_FORWARDED_ALLOW_IPS comma-separated immediate proxy IPs/CIDRs. default "".
  RELAY_UVICORN_ACCESS_LOG enable Uvicorn access logging. default true.

Device tokens + the principal/viewer signing keys are loaded from files/secret
mounts (NEVER baked into the image): see ``load_device_tokens`` and the README.

  RELAY_DEVICE_TOKENS_FILE  path to a JSON object ``{device_token: home_id}``.
  RELAY_OIDC_SEED_FILE      32-byte Ed25519 seed for the OIDC principal key.
  RELAY_VIEWER_SEED_FILE    32-byte Ed25519 seed for the SEPARATE viewer key.
"""
from __future__ import annotations

import json
import ipaddress
import math
import os
import re
from dataclasses import dataclass


def parse_forwarded_allow_ips(raw: str | None) -> str:
    """Return a canonical Uvicorn trusted-proxy allow-list.

    Hostnames, duplicates, wildcards, empty elements, and all-address networks
    are rejected so a deployment cannot accidentally trust arbitrary clients.
    """
    if raw is None or not raw.strip():
        return ""
    values: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value or value == "*":
            raise ValueError("trusted proxy entries must be non-empty IP literals or CIDRs")
        try:
            parsed = (
                ipaddress.ip_network(value, strict=False)
                if "/" in value
                else ipaddress.ip_address(value)
            )
        except ValueError as exc:
            raise ValueError(f"invalid trusted proxy address: {value!r}") from exc
        canonical = str(parsed)
        if canonical in {"0.0.0.0/0", "::/0"}:
            raise ValueError("all-address trusted proxy networks are forbidden")
        if canonical in seen:
            raise ValueError(f"duplicate trusted proxy address: {canonical}")
        seen.add(canonical)
        values.append(canonical)
    return ",".join(values)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass
class RelayConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    origin: str = ""
    https: bool = True
    ping_interval_s: float = 10.0
    ping_max_misses: int = 3
    ws_egress_max: int = 64
    http_rate: float = 20.0
    http_burst: float = 40.0
    ws_rate: float = 1.0
    ws_burst: float = 5.0
    max_body: int = 8 * 1024 * 1024
    scope_rate: float = 2.0
    scope_burst: float = 10.0
    scope_pending_max: int = 32
    scope_hello_timeout_s: float = 5.0
    request_body_timeout_s: float = 30.0
    request_total_timeout_s: float = 900.0
    http_egress_chunks: int = 8
    http_max_per_home: int = 32
    http_max_total: int = 128
    ws_max_per_home: int = 16
    ws_max_total: int = 64
    # How long a proxied request may wait on the home before the relay gives up.
    # NOT a comfort setting: without a bound, a tunnel that dies mid-request
    # parks the handler FOREVER (the exchange it is waiting on is cleared by
    # TunnelMultiplexer.shutdown, so on_head can never fire), holding its fully
    # buffered body on a 512MB machine and spinning the browser with no error.
    upstream_timeout_s: float = 30.0
    forwarded_allow_ips: str = ""
    uvicorn_access_log: bool = True

    def __post_init__(self) -> None:
        """Reject unsafe limit values instead of silently creating an
        unbounded queue or a never-expiring handshake from a bad environment
        variable."""
        positive_ints = {
            "bind_port": self.bind_port,
            "ping_max_misses": self.ping_max_misses,
            "ws_egress_max": self.ws_egress_max,
            "max_body": self.max_body,
            "scope_pending_max": self.scope_pending_max,
            "http_egress_chunks": self.http_egress_chunks,
            "http_max_per_home": self.http_max_per_home,
            "http_max_total": self.http_max_total,
            "ws_max_per_home": self.ws_max_per_home,
            "ws_max_total": self.ws_max_total,
        }
        for name, value in positive_ints.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.bind_port > 65535:
            raise ValueError("bind_port must be at most 65535")
        positive_floats = {
            "ping_interval_s": self.ping_interval_s,
            "http_burst": self.http_burst,
            "ws_burst": self.ws_burst,
            "scope_burst": self.scope_burst,
            "scope_hello_timeout_s": self.scope_hello_timeout_s,
            "request_body_timeout_s": self.request_body_timeout_s,
            "request_total_timeout_s": self.request_total_timeout_s,
            "upstream_timeout_s": self.upstream_timeout_s,
        }
        for name, value in positive_floats.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        for name, value in {
            "http_rate": self.http_rate,
            "ws_rate": self.ws_rate,
            "scope_rate": self.scope_rate,
        }.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and not negative")
        if self.http_max_per_home > self.http_max_total:
            raise ValueError("http_max_per_home must not exceed http_max_total")
        if self.ws_max_per_home > self.ws_max_total:
            raise ValueError("ws_max_per_home must not exceed ws_max_total")
        if (self.bind_host not in {"127.0.0.1", "localhost", "::1"}
                and not (self.origin or "").strip()):
            raise ValueError(
                "RELAY_ORIGIN is required for a non-loopback relay bind")
        self.forwarded_allow_ips = parse_forwarded_allow_ips(
            self.forwarded_allow_ips)

    @classmethod
    def from_env(cls) -> "RelayConfig":
        return cls(
            bind_host=_env("RELAY_BIND_HOST", "0.0.0.0"),
            bind_port=_env_int("RELAY_BIND_PORT", 8080),
            origin=_env("RELAY_ORIGIN", ""),
            https=_env_bool("RELAY_HTTPS", True),
            ping_interval_s=_env_float("RELAY_PING_INTERVAL_S", 10.0),
            ping_max_misses=_env_int("RELAY_PING_MAX_MISSES", 3),
            ws_egress_max=_env_int("RELAY_WS_EGRESS_MAX", 64),
            http_rate=_env_float("RELAY_HTTP_RATE", 20.0),
            http_burst=_env_float("RELAY_HTTP_BURST", 40.0),
            ws_rate=_env_float("RELAY_WS_RATE", 1.0),
            ws_burst=_env_float("RELAY_WS_BURST", 5.0),
            max_body=_env_int("RELAY_MAX_BODY", 8 * 1024 * 1024),
            scope_rate=_env_float("RELAY_SCOPE_RATE", 2.0),
            scope_burst=_env_float("RELAY_SCOPE_BURST", 10.0),
            scope_pending_max=_env_int("RELAY_SCOPE_PENDING_MAX", 32),
            scope_hello_timeout_s=_env_float(
                "RELAY_SCOPE_HELLO_TIMEOUT_S", 5.0),
            request_body_timeout_s=_env_float(
                "RELAY_REQUEST_BODY_TIMEOUT_S", 30.0),
            request_total_timeout_s=_env_float(
                "RELAY_REQUEST_TOTAL_TIMEOUT_S", 900.0),
            http_egress_chunks=_env_int("RELAY_HTTP_EGRESS_CHUNKS", 8),
            http_max_per_home=_env_int("RELAY_HTTP_MAX_PER_HOME", 32),
            http_max_total=_env_int("RELAY_HTTP_MAX_TOTAL", 128),
            ws_max_per_home=_env_int("RELAY_WS_MAX_PER_HOME", 16),
            ws_max_total=_env_int("RELAY_WS_MAX_TOTAL", 64),
            upstream_timeout_s=_env_float("RELAY_UPSTREAM_TIMEOUT_S", 30.0),
            forwarded_allow_ips=_env("RELAY_FORWARDED_ALLOW_IPS", ""),
            uvicorn_access_log=_env_bool("RELAY_UVICORN_ACCESS_LOG", True),
        )


def load_device_tokens(path: str = "") -> dict:
    """Load the ``{device_token: home_id}`` provisioning map.

    Source precedence: the mounted JSON file named by ``RELAY_DEVICE_TOKENS_FILE``,
    else the inline ``RELAY_DEVICE_TOKENS`` env var (Fly/most-PaaS secrets-as-env),
    else an empty map (a relay with no tokens accepts no homes -- fail-closed).
    NEVER bake tokens into the image."""
    path = path or _env("RELAY_DEVICE_TOKENS_FILE", "")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        inline = _env("RELAY_DEVICE_TOKENS", "").strip()
        if not inline:
            return {}
        data = json.loads(inline)
    if not isinstance(data, dict):
        raise ValueError("device tokens must be a JSON object {token: home_id}")
    tokens = {str(k): str(v) for k, v in data.items()}
    if any(not token or not home_id for token, home_id in tokens.items()):
        raise ValueError("device tokens and home ids must not be blank")
    for token in tokens:
        if (not 32 <= len(token) <= 256
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
            raise ValueError(
                "device tokens must be 32-256 printable ASCII characters; "
                "generate one with secrets.token_urlsafe(32)")
    for home_id in tokens.values():
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", home_id) is None:
            raise ValueError(
                "home ids must be 1-64 URL-safe characters "
                "(letters, digits, dot, underscore, or hyphen)")
    homes = set(tokens.values())
    if len(homes) > 1:
        raise ValueError(
            "this relay build supports exactly one home security origin; "
            "multiple home ids on /h/{home_id} would share cookies and browser "
            "origin. Deploy one relay hostname/instance per home until "
            "per-home subdomain isolation is implemented"
        )
    return tokens


def _decode_seed(raw: bytes, name: str) -> bytes:
    """Decode a 32-byte Ed25519 seed from raw bytes, 64 hex chars, or base64."""
    if len(raw) == 32:
        return raw
    s = raw.decode("ascii", "ignore").strip()
    try:
        d = bytes.fromhex(s)
        if len(d) == 32:
            return d
    except ValueError:
        pass
    try:
        import base64
        d = base64.b64decode(s, validate=True)
        if len(d) == 32:
            return d
    except Exception:
        pass
    raise ValueError(f"{name}: seed must be 32 raw bytes, 64 hex chars, or base64-32")


def load_seed(env_var: str) -> bytes:
    """Load a 32-byte Ed25519 seed.

    Source precedence: the mounted file named by ``env_var`` (e.g.
    ``RELAY_OIDC_SEED_FILE``), else the inline env var with the ``_FILE`` suffix
    stripped (``RELAY_OIDC_SEED``, for secrets-as-env). Returns b"" if neither is
    set; the network server then constructs no signer and cannot mint tokens."""
    path = _env(env_var, "")
    if path and os.path.exists(path):
        return _decode_seed(open(path, "rb").read().strip(), env_var)
    inline_var = env_var[:-5] if env_var.endswith("_FILE") else env_var + "_INLINE"
    inline = _env(inline_var, "").strip()
    if inline:
        return _decode_seed(inline.encode("ascii"), inline_var)
    return b""
