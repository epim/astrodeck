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
        )


def load_device_tokens(path: str = "") -> dict:
    """Load the ``{device_token: home_id}`` provisioning map from a JSON file.

    Returns an empty map if the path is unset/missing (a relay with no tokens
    accepts no homes -- fail-closed). NEVER bake tokens into the image; mount
    them as a secret file."""
    path = path or _env("RELAY_DEVICE_TOKENS_FILE", "")
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("device tokens file must be a JSON object {token: home_id}")
    return {str(k): str(v) for k, v in data.items()}


def load_seed(env_var: str) -> bytes:
    """Load a 32-byte Ed25519 seed from the file named by ``env_var`` (raw or
    hex). Returns b"" if unset (the relay then falls back to the LOUD dev-HMAC
    signer -- acceptable only for local dev)."""
    path = _env(env_var, "")
    if not path or not os.path.exists(path):
        return b""
    raw = open(path, "rb").read().strip()
    if len(raw) == 32:
        return raw
    # Accept hex too (64 chars).
    try:
        decoded = bytes.fromhex(raw.decode("ascii"))
        if len(decoded) == 32:
            return decoded
    except (ValueError, UnicodeDecodeError):
        pass
    raise ValueError(f"{env_var}: seed must be 32 raw bytes or 64 hex chars")
