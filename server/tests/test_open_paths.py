"""S4 (server half) -- which paths ``_path_is_open`` lets through the shared
token gate, and, just as much, which it does not.

There was no unit test on ``_path_is_open`` at all: ``tests/test_auth.py``
exercised ONE SPA deep link through the middleware and the exact-path set was
never asserted. That set is a security surface -- an entry added to it is a
route reachable with no token -- so it gets its own table, and the negative rows
are the point. ``/sw.js`` is open (a service worker is fetched by the browser
from the SW registration, before any session exists, with no way to attach a
token); ``/api/sw.js`` is NOT, because the openness is about that one file at
the root and never about a suffix.
"""
from __future__ import annotations

import pytest

from astrodeck.api.app import _AUTH_OPEN_EXACT, _path_is_open


@pytest.mark.parametrize("path", [
    "/sw.js",                 # S4: the PWA service worker, pre-session
    "/manifest.json",         # S4: already reserved; asserted so it stays
    "/",
    "/index.html",
    "/favicon.ico",
    "/healthz",
    "/assets/index-abc123.js",
    "/assets/nested/style.css",
    "/some/spa/route",        # extension-less deep link -> the index.html shell
])
def test_open_paths(path):
    assert _path_is_open(path) is True, f"{path} should be open"


@pytest.mark.parametrize("path", [
    "/api/sw.js",             # S4: NOT a suffix rule
    "/api/manifest.json",
    "/ws/sw.js",
    "/auth/sw.js",
    "/api/status",
    "/api/config",
    "/ws",
    "/auth/logout",
    "/auth/me",
    "/sw.js.map",             # a different file; only the exact name is open
    "/static/sw.js",
    "/sw.jsx",
])
def test_gated_paths(path):
    assert _path_is_open(path) is False, f"{path} must stay gated"


def test_the_open_exact_set_is_the_whole_story():
    """A change to the open set is a change to what is reachable with no token,
    so it has to be a deliberate edit HERE too, not a quiet line in app.py."""
    assert _AUTH_OPEN_EXACT == {"/", "/index.html", "/favicon.ico",
                                "/manifest.json", "/healthz", "/sw.js"}
