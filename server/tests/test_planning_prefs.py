"""D-FU-1: the rig-level planning block (``/api/planning``) and the active
location id.

WHAT THESE TESTS ARE GRADING, and what they are not. ``astrodeck/planning.py``
owns its own ``APIRouter``, so the router UNDER TEST HERE IS THE REAL ONE: it is
included into a throwaway ``FastAPI`` app (the fixture idiom from
``tests/test_power_protect.py``, minus that file's need to re-declare the routes
by hand, because a router can simply be mounted). Every route body, every
``dependencies=[Depends(require(...))]`` and every ``@declare(...)`` graded here
is the shipping one.

The one thing this file CANNOT grade is that ``api/app.py`` mounts it, because
``api/app.py`` belongs to task S7L and is being edited by another agent in this
same wave. ``tests/test_rbac_boot_assertion.py`` and the boot-time
``assert_route_capabilities`` grade the real surface once that
``include_router`` line lands.

The relay test is honest about the same seam: the fence lives in the middleware
inside ``create_app``, which the throwaway app has none of, so the assertion
here is the POSITIVE (the router itself refuses nothing over a tunnelled scope)
plus a check against the REAL prefix tuple that ``/api/planning`` is not on it.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import astrodeck.config as config_mod
import astrodeck.planning as planning_mod
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import MAX_POOL, AppConfig, ConfigStore


# --------------------------------------------------------------------- fixtures

class FakeAuthProvider:
    """A fixed principal for every request (mirrors tests/test_rbac_enforcement
    and tests/test_power_protect)."""

    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A per-test config store, repointed in BOTH places.

    ``astrodeck/planning.py`` binds the singleton by name at import
    (``from .config import ... config_store``), exactly like ``api/app.py``, so
    a test that repoints only ``astrodeck.config.config_store`` would grade the
    developer's real config file. ``conftest.py`` keeps that from being
    destructive; it does not keep it from being wrong.
    """
    s = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "config_store", s)
    monkeypatch.setattr(planning_mod, "config_store", s)
    return s


def _app() -> FastAPI:
    """The REAL router on a throwaway app - see the module docstring."""
    app = FastAPI()
    app.include_router(planning_mod.router)
    return app


@pytest.fixture
def client(store):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def as_role():
    """Run the next request as a shipped role (viewer/operator/syncer/admin)."""
    def _set(role: str):
        set_active_provider(FakeAuthProvider(principal_for_role(role)))
    return _set


def _get(client) -> dict:
    r = client.get("/api/planning")
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, body: dict):
    return client.put("/api/planning", json=body)


# ------------------------------------------------------------------ round trip

def test_a_put_is_readable_back_by_a_get(client):
    """The whole block survives a write and a read, field for field."""
    body = {
        "quick": {"hours": 3.5, "dawn": True, "on": {"Ha": True, "Oiii": False},
                  "exp": {"Ha": 300.0, "Oiii": 180.0},
                  "extras": {"af": True, "dither": False},
                  "dither_n": 5, "learned": True},
        "pool": ["ngc7331", "m31"],
    }
    r = _put(client, body)
    assert r.status_code == 200, r.text
    # The PUT answers with the whole block, not only the fields that moved.
    assert r.json() == _get(client)
    got = _get(client)
    assert got["quick"]["hours"] == 3.5
    assert got["quick"]["dawn"] is True
    assert got["quick"]["exp"] == {"Ha": 300.0, "Oiii": 180.0}
    assert got["quick"]["dither_n"] == 5
    assert got["pool"] == ["ngc7331", "m31"]


def test_the_wire_name_is_dither_n_and_the_browser_key_is_not_accepted(client):
    """ONE RENAME, and it is enforced rather than tolerated.

    ``prefs.ts`` stores ``ditherN``; the server stores ``dither_n`` like every
    other field in ``astrodeck.json``. Accepting both would mean a client could
    send the camelCase name, get a 200, and have the value silently dropped -
    the same "answered 200 and discarded it" shape this branch keeps finding.
    ``extra="forbid"`` makes the old name a 422 the client cannot miss.
    """
    assert _put(client, {"quick": {"ditherN": 7}}).status_code == 422
    assert _get(client)["quick"]["dither_n"] == 3


# -------------------------------------------------- absent means unchanged x2

def test_a_pool_write_does_not_touch_the_quick_defaults(client):
    """LEVEL ONE: a block the body never mentions is left alone."""
    _put(client, {"quick": {"hours": 4.0, "exp": {"L": 120.0}, "learned": True}})
    r = _put(client, {"pool": ["m42"]})
    assert r.status_code == 200, r.text
    got = _get(client)
    assert got["pool"] == ["m42"]
    assert got["quick"]["hours"] == 4.0
    assert got["quick"]["exp"] == {"L": 120.0}
    assert got["quick"]["learned"] is True


def test_a_quick_edit_does_not_erase_the_learned_exposures(client):
    """LEVEL TWO, and this is the one that costs a night.

    The sheet that edits "hours" has a narrower type than the block it is
    editing. Merge the ``quick`` block WHOLESALE and every learned per-filter
    exposure is erased by somebody nudging an unrelated number at 21:00 - the
    guide-drawer shape this branch has already fixed once. Only the fields in
    the sub-model's ``model_fields_set`` may be written.
    """
    _put(client, {"quick": {"exp": {"Ha": 300.0, "L": 60.0},
                            "on": {"Ha": True}, "dither_n": 9,
                            "learned": True}})
    r = _put(client, {"quick": {"hours": 3.0}})
    assert r.status_code == 200, r.text
    got = _get(client)["quick"]
    assert got["hours"] == 3.0
    assert got["exp"] == {"Ha": 300.0, "L": 60.0}, \
        "a quick edit erased the learned exposures"
    assert got["on"] == {"Ha": True}
    assert got["dither_n"] == 9
    assert got["learned"] is True


def test_an_explicit_empty_pool_still_clears_it(client):
    """The other half of "absent means unchanged": ``[]`` is a real request and
    must not be read as "unchanged" just because it is falsy."""
    _put(client, {"pool": ["m31", "m42"]})
    assert _put(client, {"pool": []}).status_code == 200
    assert _get(client)["pool"] == []


# ---------------------------------------------------------------- learned flag

def test_learned_is_false_on_a_fresh_config_and_true_once_written(client):
    """``learned`` tells "nothing was ever learned here" from "everything was
    deliberately unchecked", so it must start FALSE on a rig that has never had
    a quick plan built on it, and it must be writable."""
    assert _get(client)["quick"]["learned"] is False
    # A quick write that does NOT mention the flag leaves it alone: the flag is
    # the client's to set, never a side effect of touching the block.
    _put(client, {"quick": {"hours": 2.5}})
    assert _get(client)["quick"]["learned"] is False
    _put(client, {"quick": {"exp": {"L": 90.0}, "learned": True}})
    assert _get(client)["quick"]["learned"] is True


# ----------------------------------------------------------------------- pool

def test_the_pool_dedupes_and_keeps_first_seen_order(client):
    """The order IS the shortlist's running order. De-duplication is silent (a
    double-tap on "add" is not an error) but it must never re-sort."""
    r = _put(client, {"pool": ["ngc7331", "m31", "ngc7331", "m42", "m31"]})
    assert r.status_code == 200, r.text
    assert r.json()["pool"] == ["ngc7331", "m31", "m42"]
    assert _get(client)["pool"] == ["ngc7331", "m31", "m42"]


def test_an_over_long_pool_is_refused_and_nothing_is_written(client):
    """MAX_POOL is a cap on the config FILE, so it is enforced server-side. A
    300-entry pool 422s and the stored pool is untouched."""
    _put(client, {"pool": ["keeper"]})
    r = _put(client, {"pool": [f"t{i}" for i in range(300)]})
    assert r.status_code == 422, r.text
    assert len(_get(client)["pool"]) == 1
    assert _get(client)["pool"] == ["keeper"]
    assert MAX_POOL == 200


def test_an_over_long_target_id_is_refused(client):
    """An id no lookup can resolve is a client bug, and swallowing it would put
    a target in the pool that nothing can ever resolve."""
    r = _put(client, {"pool": ["x" * 100]})
    assert r.status_code == 422, r.text
    assert _get(client)["pool"] == []


# ------------------------------------------------------------- extra="forbid"

def test_an_unknown_key_in_quick_is_a_422_and_writes_nothing(client):
    """A block nobody modelled is rejected at BINDING, before the route body
    runs, so a typo cannot be half-applied."""
    _put(client, {"quick": {"hours": 4.0}})
    r = _put(client, {"quick": {"hours": 6.0, "hourz": 9}})
    assert r.status_code == 422, r.text
    assert _get(client)["quick"]["hours"] == 4.0, \
        "a rejected body was partly written"


def test_an_unknown_top_level_block_is_a_422(client):
    r = _put(client, {"pool": ["m31"], "site": {"latitude": 34.0}})
    assert r.status_code == 422, r.text
    assert _get(client)["pool"] == []


# ------------------------------------------------------------------------ RBAC

def test_a_viewer_may_read_the_plan_but_not_change_it(client, as_role):
    """READ at view.status: the pool is catalogue ids and the quick defaults are
    exposure times - no site, no media, no driver endpoints. WRITE at
    control.capture: these settings decide what tonight shoots."""
    as_role("viewer")
    assert client.get("/api/planning").status_code == 200
    assert _put(client, {"pool": ["m31"]}).status_code == 403


def test_an_operator_may_change_it(client, as_role):
    """The point of NOT choosing a ``config.*`` capability: the shipped operator
    role holds none of them (auth/capabilities.py), and an operator who cannot
    set up their own night is an operator who cannot run the rig."""
    as_role("operator")
    r = _put(client, {"pool": ["m31"], "quick": {"hours": 5.0}})
    assert r.status_code == 200, r.text
    assert r.json()["pool"] == ["m31"]


def test_a_syncer_may_not_change_it(client, as_role):
    """A headless data mover holds view.status + view.media and nothing else. It
    can see the plan; it must not be able to re-point what the rig shoots."""
    as_role("syncer")
    assert client.get("/api/planning").status_code == 200
    assert _put(client, {"pool": ["m31"]}).status_code == 403


def test_an_admin_may_change_it(client, as_role):
    """0 is a real value (dither never), not an absent one, so it has to survive
    the merge rather than being read as "nothing sent"."""
    as_role("admin")
    assert _put(client, {"quick": {"dither_n": 0}}).status_code == 200
    assert _get(client)["quick"]["dither_n"] == 0


# ----------------------------------------------------------------- relay fence

def _tunneled(app):
    """Stamp every http scope exactly as the relay client does (ASGI scope
    STATE, never a header), so the app sees a relay-tunnelled request. Copied
    from tests/test_auth.py."""
    async def _wrapped(scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            scope.setdefault("state", {})["astrodeck_remote"] = True
        await app(scope, receive, send)
    return _wrapped


def _fence_would_refuse(path: str, prefixes) -> bool:
    """The prefix half of the ``create_app`` middleware expression, mirrored.

    ``(unsafe_method and any(path == prefix or path.startswith(prefix + "/")
    for prefix in _REMOTE_LOCAL_ONLY_MUTATION_PREFIXES))`` - api/app.py.
    """
    return any(path == prefix or path.startswith(prefix + "/")
               for prefix in prefixes)


def test_planning_is_not_on_the_lan_only_fence(store):
    """A DECISION, NOT AN OVERSIGHT. The fence is for credentials, identity
    roots, driver/serial and outbound-fetch routes, and the doors into
    ``config.site`` (which is why ``/api/locations`` is on it). Planning
    preferences are none of those, and the path is ``/api/planning`` rather than
    ``/api/config/planning`` precisely so it cannot inherit the ``/api/config``
    prefix by accident.

    Imported inside the test so a colleague's in-flight edit to ``api/app.py``
    (task S7L) fails THIS assertion only, and not the whole file.
    """
    import astrodeck.api.app as app_module
    prefixes = app_module._REMOTE_LOCAL_ONLY_MUTATION_PREFIXES
    # The mirrored predicate really bites, or the negative below proves nothing.
    assert _fence_would_refuse("/api/config", prefixes) is True
    assert _fence_would_refuse("/api/locations/abc", prefixes) is True
    assert _fence_would_refuse("/api/planning", prefixes) is False
    assert "/api/planning" not in app_module._REMOTE_LOCAL_ONLY_EXACT


def test_the_planning_write_answers_over_the_relay(store, as_role):
    """The positive, at route level: nothing in the router itself refuses a
    tunnelled scope. The full fence behaviour is proven once S7L mounts the
    router inside ``create_app`` (which is where the middleware lives).

    AN AUTHENTICATED PRINCIPAL IS STILL REQUIRED, and that is the W3 interlock
    doing its job rather than a fence: a remote-flagged scope under the open
    ``none`` provider hard-denies, so the LAN default can never leak over a
    relay. Both halves are asserted, because the 200 alone would not say which
    of the two rules produced it.
    """
    with TestClient(_tunneled(_app())) as c:
        denied = c.put("/api/planning", json={"pool": ["m31"]})
        assert denied.status_code == 401, denied.text
    as_role("operator")
    with TestClient(_tunneled(_app())) as c:
        r = c.put("/api/planning", json={"pool": ["m31"], "quick": {"hours": 6.0}})
        assert r.status_code == 200, r.text
        assert r.json()["pool"] == ["m31"]
        assert c.get("/api/planning").json()["quick"]["hours"] == 6.0


# ---------------------------------------------------------- active_location_id

def test_the_active_location_id_round_trips_through_the_config_file(store,
                                                                    tmp_path):
    """The pure half of the ``astrodeck-next-sky-site`` key.

    It sits on ``AppConfig`` beside ``active_profile_id`` and NOT inside
    ``PlanningConfig``: a block a settings panel replaces wholesale cannot hold
    a pointer the panel has never heard of. ``locations.py`` has no active or
    selected field of its own - by design, so precise coordinates stay in that
    module's own file.
    """
    assert store.cfg().active_location_id is None
    store.cfg().active_location_id = "loc-abc"
    store.bump_and_save()
    reread = ConfigStore(path=tmp_path / "astrodeck.json")
    assert reread.cfg().active_location_id == "loc-abc"
    raw = json.loads((tmp_path / "astrodeck.json").read_text(encoding="utf-8"))
    assert raw["active_location_id"] == "loc-abc"


def test_a_config_written_before_the_field_existed_loads_with_none(tmp_path):
    """Additive, so an old ``astrodeck.json`` loads unchanged rather than
    refusing to parse - the same rule every appended block keeps."""
    old = {"schema_version": 1, "version": 3,
           "site": {"name": "Home", "latitude": 34.0, "longitude": -118.0}}
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    cfg = ConfigStore(path=path).cfg()
    assert cfg.active_location_id is None
    assert cfg.planning.pool == []
    assert cfg.planning.quick.learned is False
    assert isinstance(cfg, AppConfig)
