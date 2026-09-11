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

import pytest
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


# ------------------------------------------------ the whole header, pinned
#
# The directive-level tests above each grade ONE promise, which is what let the
# Permissions-Policy ship denying this origin its own camera for months: no
# assertion held the string as a whole, so an edit anywhere else in it was
# invisible. ``test_open_paths.py`` pins ``_AUTH_OPEN_EXACT`` exactly for the
# same reason -- a security surface stated as a literal is guarded by pinning
# the literal.
#
# THE HEADER IS THE CONTRACT, so a deliberate change updates this string in the
# same commit. Two traps worth knowing while you do: the CSP source keyword is
# quoted (`'self'`) and the Permissions-Policy allowlist is NOT (`(self)`) --
# writing CSP's `self` bare, or the policy's `'self'` quoted, fails silently in
# the browser and looks correct in a diff.

_EXPECTED_CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; connect-src 'self' ws: wss:"
)

_EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
    "permissions-policy": (
        "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()"),
    "content-security-policy": _EXPECTED_CSP,
}


@pytest.mark.parametrize("name, value", sorted(_EXPECTED_HEADERS.items()))
def test_every_security_header_is_pinned(tmp_path, monkeypatch, name, value):
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        got = c.get("/api/status").headers.get(name)
    assert got == value, f"{name} changed:\n  was: {value}\n  now: {got}"


def test_the_same_headers_ride_the_ui_shell_not_just_the_api(tmp_path,
                                                             monkeypatch):
    """The middleware is global on purpose -- the CSP that matters is the one
    on the document the browser parses, not the one on the JSON."""
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app) as c:
        headers = c.get("/healthz").headers
    for name, value in _EXPECTED_HEADERS.items():
        assert headers.get(name) == value, name


def test_hsts_only_on_https(tmp_path, monkeypatch):
    """Sent over TLS and NOT over the LAN's plain HTTP: an HSTS header on
    ``http://astrotown:8800`` would pin that host to a scheme the rig does not
    serve, and the next boot would be unreachable in that browser."""
    app = _make_client(tmp_path, monkeypatch, token=None)
    with TestClient(app, base_url="https://rig.test") as c:
        assert c.get("/healthz").headers["strict-transport-security"] \
            == "max-age=31536000"
    with TestClient(app, base_url="http://rig.test") as c:
        assert "strict-transport-security" not in c.get("/healthz").headers
