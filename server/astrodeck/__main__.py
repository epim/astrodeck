"""Run the AstroDeck server: python -m astrodeck [--host H] [--port 8800]

Security (P0-4): authentication is OPTIONAL and OFF by default so LAN access
keeps working unchanged. Set the ``ASTRODECK_TOKEN`` env var to require a shared
token on every REST call (``X-Auth-Token`` header / ``Authorization: Bearer`` /
``?token=``) and on the WebSocket (``?token=``). For a LOCAL-ONLY rig, bind the
loopback interface with ``--host 127.0.0.1`` so nothing off the box can reach it.
For a remote/untrusted-network deployment, set a token AND front it with a TLS
reverse proxy — see docs/SECURITY.md.
"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from .api import create_app
from .api.app import AUTH_ENV_VAR, auth_enabled

app = create_app()


def _security_banner(host: str, port: int) -> None:
    """Log a one-line auth posture, and a LOUD warning when the server is bound
    to a non-loopback interface (reachable off-box) with no token configured.
    ASCII only — the live console is cp1252."""
    log = logging.getLogger("astrodeck")
    exposed = host not in ("127.0.0.1", "localhost", "::1")
    if auth_enabled():
        log.info("auth: ENABLED (shared token from %s) on %s:%d",
                 AUTH_ENV_VAR, host, port)
        return
    if exposed:
        log.warning("=" * 70)
        log.warning("SECURITY WARNING: AstroDeck is bound to %s:%d with NO auth.",
                    host, port)
        log.warning("Anyone on this network can slew the mount and command the")
        log.warning("rig. To require a shared token, set the %s env var.",
                    AUTH_ENV_VAR)
        log.warning("For a local-only rig, bind loopback: --host 127.0.0.1")
        log.warning("See docs/SECURITY.md before any remote deployment.")
        log.warning("=" * 70)
    else:
        log.info("auth: disabled; bound to loopback %s:%d (local-only).",
                 host, port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="astrodeck")
    parser.add_argument("--host", default="0.0.0.0",
                        help="bind interface; use 127.0.0.1 for local-only")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()
    _security_banner(args.host, args.port)
    uvicorn.run("astrodeck.__main__:app", host=args.host, port=args.port,
                log_level="info")


if __name__ == "__main__":
    main()
