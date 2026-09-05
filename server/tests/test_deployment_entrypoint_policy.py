"""OPEN-009: every shipped service definition launches the GUARDED entrypoint.

`python -m astrodeck run` (and `python -m relay`) carry the non-loopback
interlock that refuses an unauthenticated off-box bind (exit 2). Launching the
ASGI app directly with `uvicorn ... --host 0.0.0.0` would bypass that. This test
pins the invariant so a future edit that reintroduces a raw uvicorn bind fails
CI, not a deployment.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _execstart(service_rel: str) -> str:
    for line in _read(service_rel).splitlines():
        if line.strip().startswith("ExecStart="):
            return line.split("=", 1)[1].strip()
    return ""


def _last_cmd(dockerfile_rel: str) -> str:
    cmd = ""
    for line in _read(dockerfile_rel).splitlines():
        if line.strip().startswith("CMD "):
            cmd = line.strip()
    return cmd


# ------------------------------------------------------------------- systemd

def test_server_systemd_uses_guarded_loopback_entrypoint():
    exec_start = _execstart("deploy/systemd/astrodeck.service")
    assert "-m astrodeck run" in exec_start
    assert "uvicorn" not in exec_start
    # the unit binds loopback; a public deployment adds the reverse proxy.
    assert "127.0.0.1" in exec_start


def test_relay_systemd_uses_guarded_entrypoint():
    exec_start = _execstart("deploy/systemd/astrodeck-relay.service")
    assert "-m relay" in exec_start
    assert "uvicorn" not in exec_start


# -------------------------------------------------------------------- docker

def test_server_dockerfile_uses_guarded_entrypoint():
    cmd = _last_cmd("Dockerfile")
    assert '"astrodeck"' in cmd and '"run"' in cmd
    assert "uvicorn" not in cmd


def test_relay_dockerfile_uses_guarded_entrypoint():
    cmd = _last_cmd("relay/Dockerfile")
    assert '"relay"' in cmd
    assert "uvicorn" not in cmd


# ----------------------------------------------------- no raw uvicorn anywhere

def test_no_shipped_definition_launches_uvicorn_directly():
    shipped = [
        "deploy/systemd/astrodeck.service",
        "deploy/systemd/astrodeck-relay.service",
        "Dockerfile",
        "relay/Dockerfile",
        "docker-compose.yml",
        "docker-compose.usb.yml",
    ]
    offenders = []
    for rel in shipped:
        for raw in _read(rel).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue  # skip blanks and comments (a comment may NAME uvicorn)
            low = line.lower()
            # a real invocation binds a host directly to the ASGI server.
            if "uvicorn" in low and ("--host" in low or "0.0.0.0" in low):
                offenders.append(f"{rel}: {line}")
    assert not offenders, f"raw uvicorn bind in shipped definitions: {offenders}"
