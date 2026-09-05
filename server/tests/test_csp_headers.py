"""OPEN-012: the CSP no longer allows inline scripts.

The one pre-paint inline script moved to public/bootstrap.js (served 'self'), so
script-src drops 'unsafe-inline'. An injected inline <script> is now refused by
the browser. style-src keeps 'unsafe-inline' as a documented exception (React/Vite
runtime styles).
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
