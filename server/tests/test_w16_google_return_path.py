# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A Google step-up started from People returns to People (#733, WP-145).

THE DEFECT. The Google callback redirected to ``_post_login_path`` (the app
root, or the relay mount's root), so a person who pressed SIGN IN WITH GOOGLE
in Settings > People landed on the home screen and had to find People again
before repeating the change the rig had refused. The page now sends
``GET /auth/login?return=<hash route>``; the rig carries that through the
signed pre-auth cookie and the callback appends it to the base it already chose.

WHAT IS WORTH ASSERTING

  THE RETURN LANDS. ``return=#/settings/users/users`` is where the callback
  redirects to, appended to the post-login base: ``/`` on the LAN and
  ``/h/<home>/`` through the relay (the base ``test_post_login_redirect.py``
  already pins), so the relay case lands on the relay's own People, not the
  relay root (which 404s).

  NEVER AN OPEN REDIRECT. The hint is a bare in-app hash route: ``#/`` and then
  letters, digits, ``/``, ``_`` and ``-``, at most 200 characters. An absolute
  URL, a scheme-relative one (``//host``), a ``javascript:`` URL, a backslash
  form, a query, a path with ``..`` or control characters, a hash that is itself
  a ``//host``, a non-string and a missing value are ALL refused, and a refused
  hint is the old behaviour (the base alone), never an error: a sign-in must not
  fail because of a hint.

  THE COOKIE IS NOT TRUSTED EITHER. The pre-auth cookie is signed, so only this
  rig can have written it, but the callback checks the hint again where it is
  USED: a validator that ran only at /auth/login would leave the callback as
  strong as the one line that skips it.

  NO HINT, NO CHANGE. A login with no ``return`` redirects where it always did.

MUTANTS RUN (each from a byte backup of ``auth/routes.py``, restored
byte-identically with sha256 compared, the mutant text grepped out; re-run on
the integrated tree at the wave 16 integration, where the diff was applied).

  R1 "the validator accepts anything" (``return value if _RETURN_RE.fullmatch(
     value) else ""`` made ``return value``). Observed:
       AssertionError: assert 'https://evil.example/' == ''   (on
       test_anything_else_is_refused_and_falls_back_to_nothing[https://evil.example/])
  R2 "/auth/login drops the return" (the ``rt`` entry left out of the pre-auth
     payload). Observed:
       AssertionError: assert '/' == '/#/settings/users/users'   (on
       test_the_return_lands_after_the_callback_on_the_lan)
  R3 "the callback does not check the hint again" (``_safe_return_fragment(
     pre.get("rt"))`` made ``pre.get("rt") or ""``). Observed:
       AssertionError: the callback followed a hint its own validator refuses
       assert '/https://evil.example/' == '/'
  R4 "the callback ignores the hint" (the fragment appended made ``""``).
     Observed: the same as R2.
"""
from __future__ import annotations

from urllib.parse import parse_qs, quote, urlparse

import pytest
from fastapi.testclient import TestClient

from astrodeck.auth import google as g
from astrodeck.auth import routes as auth_routes
from astrodeck.auth.routes import _safe_return_fragment

from test_google_oidc import _client_with_google

PEOPLE = "#/settings/users/users"


@pytest.fixture(autouse=True)
def _reset_provider():
    """Restore the open-default auth provider after each test (the helper installs
    a google/session provider that must not leak into another test)."""
    yield
    from astrodeck.auth.deps import reset_active_provider
    reset_active_provider()


# ---------------------------------------------------------------- the validator

@pytest.mark.parametrize("value", [
    PEOPLE,
    "#/rig/devices/rotator",
    "#/settings/users",
    "#/classic/settings",
    "#/a-b_c/d9/",
])
def test_a_bare_in_app_route_is_accepted(value):
    assert _safe_return_fragment(value) == value


@pytest.mark.parametrize("value", [
    "https://evil.example/",                  # an absolute URL
    "http://evil.example",
    "//evil.example",                         # scheme-relative
    "///evil.example",
    "/\\evil.example",                        # a backslash form some browsers read as //
    "\\\\evil.example",
    "javascript:alert(1)",
    "data:text/html,<script>1</script>",
    "/h/other-home/",                         # a PATH: the base is the rig's to choose
    "/settings/users",
    "settings/users/users",                   # no hash
    "%23/settings/users/users",               # still encoded: not a route
    "#",
    "#/",                                     # no segment: nothing to return to
    "#//evil.example",                        # a hash that is itself a //host
    "#/settings//users",
    "#/../etc",
    "#/a/../b",
    "#/a b",
    "#/a\nLocation: https://evil.example",    # header injection
    "#/a\r\nSet-Cookie: x=1",
    "#/a?next=https://evil.example",          # a query
    "#/a#b",
    "#/a;b",
    "#/‮gnp.exe",                        # a bidi override
    "#/" + "a" * 300,                         # over-long
    "",
    None,
    123,
    ["#/settings/users/users"],
    b"#/settings/users/users",
])
def test_anything_else_is_refused_and_falls_back_to_nothing(value):
    assert _safe_return_fragment(value) == ""


# ------------------------------------------------------------------ the dance

def _login_then_callback(c, *, query: str = "", extra_preauth: dict | None = None):
    """Drive /auth/login (with ``query``) then /auth/google/callback through the
    fake Google, echoing the state the way Google would. ``extra_preauth``
    replaces the pre-auth cookie with a FORGED-but-correctly-signed one (the
    cookie is the rig's own, so only the rig's key can make it)."""
    r = c.get("/auth/login" + query, follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    if extra_preauth is not None:
        forged = auth_routes._sign_preauth(
            {"state": state, "nonce": "NONCE", "cv": "verif", **extra_preauth})
        c.cookies.set(auth_routes.PREAUTH_COOKIE, forged)
    return c.get(f"/auth/google/callback?code=abc&state={state}",
                 follow_redirects=False)


def _app(tmp_path, monkeypatch, *, redirect_uri=None):
    app, store, _fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={"a@x.com": "admin"})
    monkeypatch.setattr(g, "new_nonce", lambda: "NONCE")
    monkeypatch.setattr(auth_routes, "new_nonce", lambda: "NONCE")
    if redirect_uri is not None:
        store.cfg().auth.google_redirect_uri = redirect_uri
    return app


def test_the_return_lands_after_the_callback_on_the_lan(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = _login_then_callback(c, query=f"?return={quote(PEOPLE, safe='')}")
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/" + PEOPLE
        assert auth_routes.SESSION_COOKIE in r.cookies, "the sign-in itself did not land"


def test_the_return_lands_on_the_relays_own_people(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, redirect_uri=(
        "https://astrodeck-relay.fly.dev/h/home-1/auth/google/callback"))
    with TestClient(app) as c:
        r = _login_then_callback(c, query=f"?return={quote(PEOPLE, safe='')}")
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/h/home-1/" + PEOPLE


@pytest.mark.parametrize("hint", [
    "https://evil.example/",
    "//evil.example",
    "javascript:alert(1)",
    "/\\evil.example",
    "#//evil.example",
    "#/a?next=https://evil.example",
    "",
])
def test_a_refused_hint_is_the_old_behaviour_not_an_error(tmp_path, monkeypatch, hint):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = _login_then_callback(c, query=f"?return={quote(hint, safe='')}")
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/", (
            f"a refused hint {hint!r} changed where the callback sends the browser")
        assert auth_routes.SESSION_COOKIE in r.cookies, "a bad hint failed the sign-in"


def test_no_hint_redirects_where_it_always_did(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = _login_then_callback(c)
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/"


def test_the_callback_checks_the_hint_again_where_it_is_used(tmp_path, monkeypatch):
    """A signed cookie that carries a hint the validator would refuse (a hint the
    rig itself would never have written) is still not followed."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = _login_then_callback(
            c, extra_preauth={"rt": "https://evil.example/"})
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/", (
            "the callback followed a hint its own validator refuses")
        assert "evil" not in r.headers["location"]


def test_the_callback_follows_a_valid_hint_from_the_cookie(tmp_path, monkeypatch):
    """The pair of the case above: the same forged-but-signed route with a VALID
    hint is followed, so the refusal above is the validator and not a callback
    that has stopped reading the cookie."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = _login_then_callback(c, extra_preauth={"rt": PEOPLE})
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/" + PEOPLE
