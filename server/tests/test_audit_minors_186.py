"""The three server-side halves of #186 — audit minors C, D and E.

Each is small, none was the reported bug, and all three are the same species:
a claim the code makes that nothing was holding it to.

* C — ``verified`` is a badge the SERVER issues, earned by a delivery test, and
  a client that never mentioned the field was clearing it.
* D — ``POST /api/polar/pause`` answered 200 {"ok": true} for an action that
  did not happen.
* E — ``GET /api/me`` satisfied RBAC invariant (4) through a hardcoded list of
  paths, so renaming the path silently ungraded it.
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth.rbac import IDENTITY_PATHS, _route_is_identity
from astrodeck.config import AlertSink, ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    with TestClient(app_module.create_app()) as c:
        yield c, store


# ============================================== C. an omitted key is not a false

def _verified_sink(store: ConfigStore) -> None:
    store.set_alerts([AlertSink(id="s1", kind="ntfy", url="https://n.invalid/t",
                                verified=True)])


def _stored(store: ConfigStore, sink_id: str = "s1") -> AlertSink:
    return next(s for s in store.cfg().alerts if s.id == sink_id)


def test_a_save_that_never_mentions_verified_leaves_the_badge_alone(client):
    """THE FINDING. The body below is a rename — it says nothing about
    ``verified`` — and it was clearing a badge that only a successful delivery
    test is allowed to grant."""
    c, store = client
    _verified_sink(store)
    r = c.post("/api/alerts", json={"id": "s1", "kind": "ntfy",
                                    "url": "https://n.invalid/t"})
    assert r.status_code == 200, r.text
    assert _stored(store).verified is True, (
        "a body that never named `verified` cleared it — pydantic's default "
        "filled in False and nothing downstream could tell that apart from a "
        "client asking to clear it")


def test_a_client_that_explicitly_clears_it_is_still_obeyed(client):
    """The other half: this is a real instruction, not an omission, and the
    fix must not turn the field read-only."""
    c, store = client
    _verified_sink(store)
    r = c.post("/api/alerts", json={"id": "s1", "kind": "ntfy",
                                    "url": "https://n.invalid/t",
                                    "verified": False})
    assert r.status_code == 200, r.text
    assert _stored(store).verified is False


def test_re_pointing_the_channel_still_clears_it(client):
    """The rule this route exists for (C1-16) is untouched: a sink pointed
    somewhere new has not been tested THERE, whatever the body claims."""
    c, store = client
    _verified_sink(store)
    r = c.post("/api/alerts", json={"id": "s1", "kind": "ntfy",
                                    "url": "https://elsewhere.invalid/t",
                                    "verified": True})
    assert r.status_code == 200, r.text
    assert _stored(store).verified is False, (
        "the sink now points at a different URL and kept a badge earned "
        "against the old one")


def test_a_brand_new_sink_is_not_verified_by_omission(client):
    """No stored copy to carry forward from, so the model default stands."""
    c, store = client
    r = c.post("/api/alerts", json={"id": "new", "kind": "ntfy",
                                    "url": "https://n.invalid/x"})
    assert r.status_code == 200, r.text
    assert _stored(store, "new").verified is False


# ================================= D. an action that did not happen is not "ok"

def _polar_idle(monkeypatch):
    monkeypatch.setattr(app_module.hub.polar, "_task", None, raising=False)


def _polar_live(monkeypatch):
    monkeypatch.setattr(app_module.hub.polar, "_task",
                        types.SimpleNamespace(done=lambda: False),
                        raising=False)


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_polar_pause_and_resume_refuse_when_nothing_is_running(client, monkeypatch,
                                                               action):
    c, _store = client
    _polar_idle(monkeypatch)
    r = c.post(f"/api/polar/{action}")
    assert r.status_code == 409, (
        f"{action} on an idle session answered {r.status_code} — the route "
        f"reported an action that PolarSession deliberately did not take")
    detail = r.json()["detail"]
    assert detail["code"] == "not_running" and detail["lane"] == "polar"
    assert action in detail["detail"], (
        f"the refusal does not say what was refused: {detail['detail']!r}")


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_polar_pause_and_resume_still_work_while_a_session_runs(client, monkeypatch,
                                                               action):
    """The positive control. A route that 409'd unconditionally would satisfy
    the test above and would have broken the only screen that calls it."""
    c, _store = client
    _polar_live(monkeypatch)
    called: list[str] = []

    async def _noop():
        called.append(action)

    monkeypatch.setattr(app_module.hub.polar, action, _noop, raising=False)
    r = c.post(f"/api/polar/{action}")
    assert r.status_code == 200, r.text
    assert called == [action]


def test_stop_is_deliberately_not_given_the_same_gate(client, monkeypatch):
    """Stop is a never-disabled button whose job is to be pressable when the
    user is unsure whether anything is running. Refusing it would make the one
    control you reach for in doubt the one that argues back."""
    c, _store = client
    _polar_idle(monkeypatch)
    assert c.post("/api/polar/stop").status_code == 200


# ==================================== E. the invariant must survive a rename

def test_api_me_declares_its_own_identity_disclosure():
    """Invariant (4) held for /api/me only because its literal path is in
    ``IDENTITY_PATHS``. Rename the route and it keeps disclosing who you are
    while quietly dropping out of the check. The flag travels with the
    declaration, so it cannot be separated from the route by an edit that never
    looks at rbac.py."""
    app = app_module.create_app()
    route = next(r for r in app.router.routes
                 if getattr(r, "path", "") == "/api/me")
    assert _route_is_identity(route), (
        "/api/me does not declare identity=True — it is graded only by its "
        "path, which is a coupling nothing enforces")
    # The path membership stays as the second belt, deliberately.
    assert "/api/me" in IDENTITY_PATHS
