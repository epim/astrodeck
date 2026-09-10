"""OPEN-012: the CSP no longer allows inline scripts.

The one pre-paint inline script moved to public/bootstrap.js (served 'self'), so
script-src drops 'unsafe-inline'. An injected inline <script> is now refused by
the browser. style-src keeps 'unsafe-inline' as a documented exception (React/Vite
runtime styles).

Also the Permissions-Policy this middleware sets beside the CSP, which is the
same kind of promise made in the same place: what this origin's own pages are
allowed to ask the browser for.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from test_auth import _make_client


def _directive(csp: str, name: str) -> str:
    for part in csp.split(";"):
        part = part.strip()
        if part.startswith(name + " ") or part == name:
            return part
    return ""


def test_script_src_has_no_unsafe_inline(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        csp = c.get("/api/status").headers["content-security-policy"]
    script = _directive(csp, "script-src")
    assert script, f"no script-src directive in CSP: {csp}"
    assert "'self'" in script
    assert "'unsafe-inline'" not in script, script


def test_style_src_keeps_documented_unsafe_inline(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        csp = c.get("/api/status").headers["content-security-policy"]
    style = _directive(csp, "style-src")
    assert "'unsafe-inline'" in style, style


def _feature(policy: str, name: str) -> str:
    """The allowlist for one feature, e.g. 'camera=(self)' -> '(self)'."""
    for part in policy.split(","):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return ""


def test_permissions_policy_admits_our_own_origin_to_camera_and_geolocation(
    tmp_path, monkeypatch
):
    """`()` denies a feature to THIS origin too, not just to embedded frames.

    Three shipped features ask the browser for these: the finder's AR camera
    overlay (`hubs/sky/finder/camera.ts`), the photosphere capture, and the
    Sites sheet's "fill from the phone". An empty allowlist made all three
    permanently impossible against this server, with the refusal coming from
    the browser rather than from anything a user could change.
    """
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        policy = c.get("/api/status").headers["permissions-policy"]

    assert policy == (
        "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()"
    ), policy
    # Spelled out, so a reordering of the header does not read as a regression
    # and a widening of it does.
    assert _feature(policy, "camera") == "(self)", policy
    assert _feature(policy, "geolocation") == "(self)", policy
    assert "*" not in policy, f"a feature was opened to every origin: {policy}"


def test_permissions_policy_still_denies_what_nothing_here_asks_for(tmp_path, monkeypatch):
    """The other three are not collateral of the widening above."""
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        policy = c.get("/api/status").headers["permissions-policy"]
    for feature in ("microphone", "payment", "usb"):
        assert _feature(policy, feature) == "()", f"{feature} is no longer denied: {policy}"


def test_embedding_is_still_refused_outright(tmp_path, monkeypatch):
    """`(self)` is only safe because there is no frame to inherit it."""
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        headers = c.get("/api/status").headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
