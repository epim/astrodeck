"""Nobody can be locked out of their own rig (#205).

WHAT HAPPENED, TWICE. The rig was found with ``methods: ["google"]``, a Google
client ID set, ``google_client_secret`` blank, no local method and no
break-glass token. The login page correctly concluded there was no way to sign
in and said so. Nothing on the box was reachable.

THE MECHANISM. ``POST /api/auth/config`` takes a whole ``AuthConfig``, and the
block the UI holds has every secret scrubbed to "". Its docstring said blank
secrets mean "unchanged" — describing ``_preserve_auth_secrets`` — and the route
never called it. So saving ANY auth setting wrote those blanks over the stored
values, wiping the Google client secret (Google resolves unconfigured) and the
session signing key (every live session dies).

The sibling route ``/api/remote/config`` had the identical bug, was found, and
was fixed; its docstring even records "'Exactly like /api/auth/config' was true
of the sentence and not of the code". The fixer patched the route that had
visibly failed and left the one whose docstring already claimed the fix — the
hardest place to look, because reading it tells you it is handled.

THREE LAYERS HERE, deliberately, because one of them had already failed once:
  1. the route preserves blank secrets (the cause);
  2. ``set_auth`` REFUSES any config that leaves nobody able to sign in (the
     backstop — no caller intended the lockout, so no caller was going to check
     for it, and every auth write funnels through this one setter);
  3. the ``create-admin`` break-glass also enables ``local``, so the account it
     makes is one you can actually use.
"""
from __future__ import annotations

import pytest

from astrodeck.config import (AuthConfig, ConfigStore, usable_login_methods)


def _cfg(**kw) -> AuthConfig:
    base = dict(methods=["google"], google_client_id="id.apps.googleusercontent.com",
                google_client_secret="shh", admin_token="")
    base.update(kw)
    return AuthConfig(**base)


# ------------------------------------------------- what "usable" actually means

def test_a_ticked_method_is_not_the_same_as_a_working_one():
    """The whole of #205 in one assertion. Google was ENABLED and its ID was
    set; only the secret was blank, and that is enough for the login page to
    have no form to draw."""
    assert usable_login_methods(_cfg()) == ["google"]
    assert usable_login_methods(_cfg(google_client_secret="")) == []
    assert usable_login_methods(_cfg(google_client_id="")) == []


def test_local_is_usable_whenever_it_is_enabled():
    """Either an account exists, or the first-run form makes one. Both end with
    somebody signed in, so this needs no store lookup."""
    assert "local" in usable_login_methods(_cfg(methods=["local"]))
    assert "local" in usable_login_methods(_cfg(methods=["google", "local"],
                                                google_client_secret=""))


def test_a_break_glass_token_counts_wherever_it_is_set():
    """It bypasses the provider entirely, which is the entire point of it."""
    assert usable_login_methods(
        _cfg(google_client_secret="", admin_token="t")) == ["admin_token"]


# ------------------------------------------------------------- the backstop

def test_set_auth_refuses_a_config_nobody_can_sign_in_through(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    with pytest.raises(ValueError) as err:
        store.set_auth(_cfg(google_client_secret=""))
    msg = str(err.value)
    # The person reading this is looking at a screen that will not let them in,
    # so it has to name the ways out rather than merely refuse.
    assert "nobody able to sign in" in msg
    assert "local" in msg and "admin_token" in msg
    assert "google_client_secret" in msg


def test_turning_authentication_OFF_is_not_a_lockout(tmp_path):
    """The shipped default for a LAN rig is no methods at all — every caller
    resolves to the open-default admin. Refusing that would refuse the default
    config, and 'you may not turn auth off' is not a decision this guard gets
    to make. Being unable to sign in after turning it ON is the only failure."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.set_auth(_cfg(methods=[], google_client_secret=""))
    assert cfg.auth.methods_effective() == []


def test_a_good_config_still_saves(tmp_path):
    """The positive control. A guard that refused everything would also pass
    every assertion above."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    assert store.set_auth(_cfg()).auth.google_client_secret == "shh"
    assert store.set_auth(_cfg(methods=["local"])).auth.methods_effective() == ["local"]


# ------------------------------------------------------- the break-glass CLI

def _make_app(tmp_path, monkeypatch, *, auth: AuthConfig):
    """Isolated app: temp config store + temp user store (the
    ``test_auth_epoch`` fixture shape)."""
    import astrodeck.api.app as app_module
    import astrodeck.auth.local_routes as local_routes
    import astrodeck.auth.routes as auth_routes
    import astrodeck.auth.users as users_mod
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.auth.users import UserStore

    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    temp_store.cfg().auth = auth
    for mod in (config_mod, hub_mod, app_module, local_routes, auth_routes):
        monkeypatch.setattr(mod, "config_store", temp_store, raising=False)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(users_mod, "user_store", UserStore(path=tmp_path / "users.json"))
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    return app_module.create_app(), temp_store


def test_saving_the_auth_panel_does_not_wipe_the_google_secret(
        tmp_path, monkeypatch):
    """THE ROOT CAUSE, end to end over the API, exactly as the UI does it.

    The client can only ever read the REDACTED config — every secret comes back
    as "". Saving any auth setting means echoing that block back. Before the
    fix this route wrote those blanks straight over the stored values, so one
    save of an unrelated toggle silently destroyed the Google client secret and
    the session signing key, and the rig had no sign-in method left.
    """
    from fastapi.testclient import TestClient
    from astrodeck.auth import reset_active_provider

    reset_active_provider()
    try:
        auth = AuthConfig(methods=[], google_client_id="id.apps.googleusercontent.com",
                          google_client_secret="the-real-secret",
                          session_private_key="the-real-key")
        app, store = _make_app(tmp_path, monkeypatch, auth=auth)
        with TestClient(app) as c:
            body = c.get("/api/config").json()["auth"]
            assert body["google_client_secret"] == "", (
                "precondition: the client must only ever see a redacted secret")
            # An ORDINARY save of an unrelated setting.
            body["session_ttl_s"] = 3600
            assert c.post("/api/auth/config", json=body).status_code == 200

        assert store.cfg().auth.google_client_secret == "the-real-secret", (
            "saving the auth panel wiped the Google client secret — this is "
            "the write that locked the rig out")
        assert store.cfg().auth.session_private_key == "the-real-key", (
            "saving the auth panel wiped the session signing key, which kills "
            "every live session")
        assert store.cfg().auth.session_ttl_s == 3600, (
            "the setting the user actually changed did not take effect")
    finally:
        reset_active_provider()


def test_create_admin_also_makes_the_account_reachable(tmp_path, monkeypatch):
    """A local admin created while ``local`` is not an enabled method is a key
    to a door the break-glass does not unlock — and is exactly the state the rig
    was found in: one local admin in the store, methods ["google"], Google
    unconfigured, login page saying no method was available."""
    import astrodeck.config as configmod
    from astrodeck.__main__ import create_admin

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(configmod, "config_store", store)
    monkeypatch.setattr(configmod, "USERS_FILE", tmp_path / "users.json",
                        raising=False)
    store.set_auth(_cfg())                      # google-only, and it works
    assert "local" not in store.cfg().auth.methods_effective()

    import astrodeck.auth.users as usersmod
    monkeypatch.setattr(usersmod.user_store, "_path", tmp_path / "users.json",
                        raising=False)
    monkeypatch.setattr(usersmod.user_store, "_users", {}, raising=False)

    create_admin("breakglass", "a-long-enough-password")
    assert "local" in store.cfg().auth.methods_effective(), (
        "the break-glass made an account nobody can sign in as")
    # …and it took nothing else away.
    assert "google" in store.cfg().auth.methods_effective()


# --------------------------------- the break-glass has to reach a LIVE server

def test_a_user_written_by_another_process_is_seen_without_a_restart(tmp_path):
    """The second half of #205, found while probing the rig.

    `python -m astrodeck create-admin` runs in a SEPARATE process. It rewrote
    users.json correctly and printed "admin user 'x' ready", while the running
    server kept serving a cache from before the account existed — so signing in
    as it returned "invalid username or password", with nothing anywhere
    explaining why. The documented recovery path for a locked-out operator
    appeared to work and did nothing.

    `reload()` existed the whole time and was called from NOWHERE; its own
    docstring said "(tests)". A capability nothing invokes is not a capability.
    """
    from astrodeck.auth.users import UserStore

    path = tmp_path / "users.json"
    server = UserStore(path=path)          # the long-lived process
    server.create(username="existing", password="a-long-enough-password",
                  role="admin", enabled=True, require_email=False)
    assert server.get_by_username("existing") is not None

    # A DIFFERENT process — the CLI break-glass — adds an account.
    cli = UserStore(path=path)
    cli.create(username="breakglass", password="another-long-password",
               role="admin", enabled=True, require_email=False)

    got = server.get_by_username("breakglass")
    assert got is not None, (
        "the running server cannot see the account the break-glass just "
        "created — signing in as it fails with 'invalid username or password'")
    assert got.role == "admin"
    # …and the account it already had is still there (a reload must not lose
    # state, which is the obvious way to get this wrong).
    assert server.get_by_username("existing") is not None


def test_a_password_reset_in_another_process_takes_effect(tmp_path):
    """The same-second, same-length case — which is exactly what a password
    reset looks like on disk, and what a stamp keyed on mtime alone would miss
    on a filesystem with one-second granularity."""
    from astrodeck.auth.users import UserStore

    path = tmp_path / "users.json"
    server = UserStore(path=path)
    u = server.create(username="op", password="original-password-here",
                      role="admin", enabled=True, require_email=False)
    assert server.verify("op", "original-password-here") is not None

    cli = UserStore(path=path)
    cli.set_password(u.id, "the-replacement-password")

    assert server.verify("op", "the-replacement-password") is not None, (
        "the reset did not reach the running server")
    assert server.verify("op", "original-password-here") is None, (
        "the OLD password still works — the server is serving a stale cache")
