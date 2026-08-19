"""Relay runtime configuration (env-driven; no secrets in code).

The relay is deployed by the owner (Docker + Fly.io), so its config comes from
environment variables. Keys:

  RELAY_BIND_HOST          (default 0.0.0.0)
  RELAY_BIND_PORT          (default 8080)
  RELAY_ORIGIN             public host browsers talk to, e.g. relay.example
                           (used to rewrite Set-Cookie Domain). default "".
  RELAY_HTTPS              "1" if the public edge is HTTPS (forces cookie
                           Secure). default "1".
  RELAY_PING_INTERVAL_S    keepalive ping period. default 10.
  RELAY_PING_MAX_MISSES    PONG misses before tunnel teardown. default 3.
  RELAY_WS_EGRESS_MAX      per-browser /ws egress buffer bound. default 200.
  RELAY_HTTP_RATE          per-IP HTTP token-bucket refill (tokens/s). default 20.
  RELAY_HTTP_BURST         per-IP HTTP burst capacity. default 40.
  RELAY_WS_RATE            per-IP /ws-open refill (tokens/s). default 1.
  RELAY_WS_BURST           per-IP /ws-open burst capacity. default 5.
  RELAY_MAX_BODY           max tunnelled request body bytes. default 134217728.

Device tokens + the principal/viewer signing keys are loaded from files/secret
mounts (NEVER baked into the image): see ``load_device_tokens`` and the README.

  RELAY_DEVICE_TOKENS_FILE  path to a JSON object ``{device_token: home_id}``.
  RELAY_OIDC_SEED_FILE      32-byte Ed25519 seed for the OIDC principal key.
  RELAY_VIEWER_SEED_FILE    32-byte Ed25519 seed for the SEPARATE viewer key.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class RelayConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    origin: str = ""
    https: bool = True
    ping_interval_s: float = 10.0
    ping_max_misses: int = 3
    ws_egress_max: int = 200
    http_rate: float = 20.0
    http_burst: float = 40.0
    ws_rate: float = 1.0
    ws_burst: float = 5.0
    max_body: int = 128 * 1024 * 1024
    # How long a proxied request may wait on the home before the relay gives up.
    # NOT a comfort setting: without a bound, a tunnel that dies mid-request
    # parks the handler FOREVER (the exchange it is waiting on is cleared by
    # TunnelMultiplexer.shutdown, so on_head can never fire), holding its fully
    # buffered body on a 512MB machine and spinning the browser with no error.
    upstream_timeout_s: float = 30.0

    @classmethod
    def from_env(cls) -> "RelayConfig":
        return cls(
            bind_host=_env("RELAY_BIND_HOST", "0.0.0.0"),
            bind_port=_env_int("RELAY_BIND_PORT", 8080),
            origin=_env("RELAY_ORIGIN", ""),
            https=_env_bool("RELAY_HTTPS", True),
            ping_interval_s=_env_float("RELAY_PING_INTERVAL_S", 10.0),
            ping_max_misses=_env_int("RELAY_PING_MAX_MISSES", 3),
            ws_egress_max=_env_int("RELAY_WS_EGRESS_MAX", 200),
            http_rate=_env_float("RELAY_HTTP_RATE", 20.0),
            http_burst=_env_float("RELAY_HTTP_BURST", 40.0),
            ws_rate=_env_float("RELAY_WS_RATE", 1.0),
            ws_burst=_env_float("RELAY_WS_BURST", 5.0),
            max_body=_env_int("RELAY_MAX_BODY", 128 * 1024 * 1024),
            upstream_timeout_s=_env_float("RELAY_UPSTREAM_TIMEOUT_S", 30.0),
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
    return {str(k): str(v) for k, v in data.items()}


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
    set (the relay then falls back to the LOUD dev-HMAC signer -- local dev only)."""
    path = _env(env_var, "")
    if path and os.path.exists(path):
        return _decode_seed(open(path, "rb").read().strip(), env_var)
    inline_var = env_var[:-5] if env_var.endswith("_FILE") else env_var + "_INLINE"
    inline = _env(inline_var, "").strip()
    if inline:
        return _decode_seed(inline.encode("ascii"), inline_var)
    return b""
