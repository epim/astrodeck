"""One account list, whatever a person signs in with.

User management used to be two stores that never met: a local username+password
store, and an email->role allowlist in AuthConfig for Google. Creating a
Google-only user meant editing an allowlist rather than creating a user, so
there was nowhere to give them a role beside everyone else, nowhere to disable
them, and nothing stopping the same person existing twice with two different
roles.

The identity is now the email address, and a password is optional: an empty
password_hash means "Google sign-in only".

The security-critical property is the empty hash. It must never be matchable,
and it must not be distinguishable by timing from a wrong password.
"""
from __future__ import annotations

import pytest

from astrodeck.auth.users import (InvalidEmailError, UserStore,
                                  normalize_email)


@pytest.fixture
def store(tmp_path):
    return UserStore(path=tmp_path / "users.json")


# ------------------------------------------------------------ email identity

@pytest.mark.parametrize("raw,expected", [
    ("  James@Example.COM ", "james@example.com"),
    ("a.b+tag@sub.domain.org", "a.b+tag@sub.domain.org"),
])
def test_email_is_normalised(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("bad", ["bob", "", "   ", "a@b", "@example.com",
                                 "nope@", "a b@c.com", "two@@at.com"])
def test_a_non_email_identity_is_refused(bad):
    """Sign-in matches on the address Google reports, so an entry that can never
    match is an account somebody believes they created."""
    with pytest.raises(InvalidEmailError):
        normalize_email(bad)


def test_the_api_policy_requires_an_email(store):
    """`require_email` is the PRODUCT policy and the route passes it. Everything
    a person can create through the UI goes through this path."""
    with pytest.raises(InvalidEmailError):
        store.create(username="bob", password="hunter22-long", role="admin",
                     require_email=True)


def test_the_store_itself_still_holds_a_bare_username(store):
    """It has to. The CLI break-glass admin creates one on a rig with no Google
    and nothing but a console, and records predating unification carry one — a
    store that refused what it must still be able to hold would be lying about
    its own data."""
    u = store.create(username="bob", password="hunter22-long", role="admin")
    assert u.username == "bob"


# --------------------------------------------- the empty hash is not a password

def test_a_google_only_account_cannot_be_logged_into_with_any_password(store):
    """THE test. An empty password_hash must never match — including against
    the empty string, which is what hashing "" would have made matchable."""
    store.create(username="viewer@example.com", role="viewer")
    for attempt in ("", " ", "password", "hunter22", None and ""):
        assert store.verify("viewer@example.com", attempt) is None


def test_an_omitted_password_stores_no_hash_at_all(store):
    u = store.create(username="viewer@example.com", role="viewer")
    assert u.password_hash == ""
    assert u.can_sign_in_locally is False
    assert u.to_public()["has_password"] is False


def test_a_password_account_still_verifies(store):
    store.create(username="admin@example.com", password="hunter22-long", role="admin")
    assert store.verify("admin@example.com", "hunter22-long") is not None
    assert store.verify("admin@example.com", "wrong") is None


def test_clearing_a_password_makes_it_google_only(store):
    u = store.create(username="a@example.com", password="hunter22-long", role="admin")
    assert store.verify("a@example.com", "hunter22-long") is not None
    store.clear_password(u.id)
    assert store.verify("a@example.com", "hunter22-long") is None
    assert store.get(u.id).can_sign_in_locally is False


def test_verify_is_case_insensitive_on_the_address(store):
    store.create(username="Mixed@Example.com", password="hunter22-long", role="viewer")
    assert store.verify("mixed@example.com", "hunter22-long") is not None
    assert store.verify("MIXED@EXAMPLE.COM", "hunter22-long") is not None


# ------------------------------------------------------------ the shared lookup

def test_google_and_password_resolve_the_same_record(store):
    """One person, one role. The whole point of unifying the stores."""
    u = store.create(username="both@example.com", password="hunter22-long",
                     role="operator")
    assert store.get_by_email("both@example.com").id == u.id
    assert store.verify("both@example.com", "hunter22-long").id == u.id


def test_get_by_email_finds_a_password_less_account(store):
    u = store.create(username="oidc@example.com", role="viewer")
    assert store.get_by_email("OIDC@Example.com").id == u.id


def test_get_by_email_on_a_non_email_returns_none_rather_than_raising(store):
    """It is called on whatever an IdP reports; a surprise there must deny, not
    500 the login route."""
    assert store.get_by_email("not-an-email") is None
    assert store.get_by_email("") is None


def test_a_legacy_record_resolves_by_its_email_field(store):
    """Pre-unification records have a bare username and email as metadata.
    They must keep working — an upgrade that locks somebody out of their own
    rig on a clear night is not an acceptable migration."""
    u = store.create(username="oldbob", password="hunter22-long", role="admin",
                     email="bob@example.com", require_email=False)
    assert store.get_by_email("bob@example.com").id == u.id
    assert store.verify("oldbob", "hunter22-long") is not None


# ----------------------------------------------------------------- role lookup

def test_role_for_email_prefers_the_user_store(monkeypatch, store):
    from astrodeck.auth import routes as R
    monkeypatch.setattr("astrodeck.auth.users.user_store", store)
    store.create(username="me@example.com", role="operator")
    cfg = type("C", (), {"role_allowlist": {"me@example.com": "admin"},
                         "default_role": "viewer"})()
    # the store wins over a stale allowlist entry
    assert R._role_for_email(cfg, "me@example.com") == "operator"


def test_a_disabled_account_is_denied_not_demoted(monkeypatch, store):
    """Falling through to the allowlist or the default role would silently turn
    'disabled' into 'still has access, just less of it'."""
    from astrodeck.auth import routes as R
    monkeypatch.setattr("astrodeck.auth.users.user_store", store)
    u = store.create(username="gone@example.com", role="operator")
    store.set_enabled(u.id, False)
    cfg = type("C", (), {"role_allowlist": {"gone@example.com": "admin"},
                         "default_role": "viewer"})()
    assert R._role_for_email(cfg, "gone@example.com") is None


def test_the_legacy_allowlist_still_works_for_an_unmigrated_rig(monkeypatch, store):
    from astrodeck.auth import routes as R
    monkeypatch.setattr("astrodeck.auth.users.user_store", store)
    cfg = type("C", (), {"role_allowlist": {"old@example.com": "operator"},
                         "default_role": None})()
    assert R._role_for_email(cfg, "old@example.com") == "operator"


def test_an_unknown_address_falls_to_the_default_role(monkeypatch, store):
    from astrodeck.auth import routes as R
    monkeypatch.setattr("astrodeck.auth.users.user_store", store)
    deny = type("C", (), {"role_allowlist": {}, "default_role": None})()
    allow = type("C", (), {"role_allowlist": {}, "default_role": "viewer"})()
    assert R._role_for_email(deny, "stranger@example.com") is None
    assert R._role_for_email(allow, "stranger@example.com") == "viewer"


def test_no_email_is_always_denied(monkeypatch, store):
    from astrodeck.auth import routes as R
    monkeypatch.setattr("astrodeck.auth.users.user_store", store)
    cfg = type("C", (), {"role_allowlist": {}, "default_role": "admin"})()
    assert R._role_for_email(cfg, None) is None
    assert R._role_for_email(cfg, "") is None


def test_last_admin_protection_survives(store):
    """Unification must not weaken the anti-lockout guard."""
    u = store.create(username="only@example.com", password="hunter22-long", role="admin")
    with pytest.raises(ValueError, match="last admin"):
        store.set_enabled(u.id, False)
    with pytest.raises(ValueError, match="last admin"):
        store.delete(u.id)
