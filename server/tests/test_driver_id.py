"""driver_id resolution + primary:"none" plumbing (equipment-drivers spec §3.3,
§5, cache-honesty §3.2 / Phase 2).

Two layers, formerly split across test_driver_id_resolution.py and
test_driver_id_plumbing.py (merged 2026-07-18 — same driver-id concern, no
shared fixtures to reconcile):

  * RESOLVER + HUB WIRING: ``drivers.resolve_driver_ids`` is PURE over config --
    driver_id -> concrete backend/host/port at connect time. Missing/disabled
    drivers pre-fail their role (honest RoleResult, never a 500/silent skip). A
    failed driver-backed connect drops that driver's probe-cache row so
    /api/drivers can't report stale 'reachable'.
  * DATA MODEL / PLUMBING: driver_id is an ADDITIVE reference field on ConnSpec
    (old serialized specs parse unchanged; one that carries it round-trips).
    primary:"none" is the explicit-only rig mode -- the orchestrator requests
    ONLY the roles with explicit overrides (no primary-derived fill), so an
    Equipment assignment surface connects exactly what the user assigned.
"""
import asyncio

import pytest

from astrodeck import drivers as drv
from astrodeck.config import ConfigStore
from astrodeck.devices.backend import ConnSpec, RigSpec
# importing the backends package self-registers "sim" (registration is lazy,
# normally triggered by hub/app connect routes) so _requested_roles(primary=
# "sim") below can resolve it when this file runs standalone.
from astrodeck.devices.backends import sim_backend  # noqa: F401  (side-effect import)
from astrodeck.devices.orchestrator import _requested_roles
from astrodeck.profiles import Profile, ProfileDevice


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(drv, "config_store", s)
    drv.invalidate()
    return s


def _spec(role, **kw):
    return RigSpec(primary="none", roles={role: ConnSpec(role=role, **kw)})


# ============================================================ resolver: driver_id -> concrete spec

def test_resolves_backend_host_port_and_merges_extra(store):
    d = store.add_driver("alpaca", "192.168.1.50", 11111)
    store.update_driver(d.id, {"extra": {"from_driver": 1, "shared": "driver"}})
    spec = _spec("camera", backend="native", driver_id=d.id,
                 dev_type="camera", dev_num=2,
                 extra={"shared": "spec", "name": "ASI2600MM"})
    resolved, role_map, prefailed = drv.resolve_driver_ids(spec)
    assert prefailed == []
    assert role_map == {"camera": d.id}
    c = resolved.roles["camera"]
    assert (c.backend, c.host, c.port) == ("native", "192.168.1.50", 11111)
    assert (c.dev_type, c.dev_num) == ("camera", 2)
    # spec extra wins on collision; driver extra fills the rest
    assert c.extra == {"from_driver": 1, "shared": "spec", "name": "ASI2600MM"}


def test_driver_type_backend_mapping(store):
    for dtype, backend in (("nina", "nina"), ("alpaca", "native"), ("phd2", "phd2")):
        d = store.add_driver(dtype, "h")
        resolved, _, pre = drv.resolve_driver_ids(
            _spec("guider" if dtype == "phd2" else "camera",
                  backend="x", driver_id=d.id))
        assert pre == []
        role = "guider" if dtype == "phd2" else "camera"
        assert resolved.roles[role].backend == backend


def test_missing_and_disabled_drivers_prefail(store):
    resolved, role_map, pre = drv.resolve_driver_ids(
        _spec("camera", backend="native", driver_id="alpaca-gone"))
    assert resolved.roles == {} and role_map == {}
    assert pre == [("camera", "driver removed: alpaca-gone")]

    d = store.add_driver("nina", "h", label="My NINA")
    store.update_driver(d.id, {"enabled": False})
    _, _, pre = drv.resolve_driver_ids(_spec("camera", backend="nina", driver_id=d.id))
    assert pre == [("camera", "driver disabled: My NINA")]


def test_raw_specs_pass_through_untouched(store):
    spec = _spec("camera", backend="native", host="h", port=1, dev_type="camera")
    resolved, role_map, pre = drv.resolve_driver_ids(spec)
    assert pre == [] and role_map == {}
    assert resolved.roles["camera"] is spec.roles["camera"]


# ---------------------------------------------------------------- hub wiring

def test_hub_prefail_lands_in_results_and_cache_invalidated(store, monkeypatch):
    """End-to-end through hub.connect_rigspec: a sim camera + a removed-driver
    guider → camera connects, guider is a failed RoleResult with the honest
    error, and it appears in backend_links. A driver-backed role that FAILS
    invalidates that driver's probe cache row."""
    import astrodeck.hub as hub_mod
    h = hub_mod.hub

    async def run():
        spec = RigSpec(primary="none", roles={
            "camera": ConnSpec(backend="sim", role="camera"),
            "guider": ConnSpec(backend="phd2", role="guider",
                                driver_id="phd2-gone"),
        })
        return await h.connect_rigspec(spec)

    # hub reads the drivers module at call time — point ITS view of config at
    # the temp store too (hub calls drivers_mod.resolve_driver_ids which reads
    # drv.config_store, already monkeypatched by the fixture).
    out = asyncio.run(run())
    by_role = {r["role"]: r for r in out["results"]}
    assert by_role["camera"]["ok"] is True
    g = by_role["guider"]
    assert g["ok"] is False and g["attempted"] is True
    assert g["error"] == "driver removed: phd2-gone"
    link_roles = {l["role"] for l in out["backend_links"]}
    assert "guider" in link_roles
    # cleanup so later tests see no live rig
    asyncio.run(h.disconnect_all())


def test_failed_driver_backed_role_invalidates_cache(store):
    d = store.add_driver("phd2", "127.0.0.1", 4400)
    # seed a fake 'reachable' cache row, then fail a connect through that driver
    drv._CACHE[d.id] = (999999999.0, {"reachable": True, "error": None,
                                       "detail": None, "probed_at": 0.0,
                                       "offers": {"devices": [], "tasks": []}})
    import astrodeck.hub as hub_mod
    h = hub_mod.hub

    async def run():
        spec = RigSpec(primary="none", roles={
            "guider": ConnSpec(backend="phd2", role="guider", driver_id=d.id),
        })
        return await h.connect_rigspec(spec)

    out = asyncio.run(run())
    by_role = {r["role"]: r for r in out["results"]}
    assert by_role["guider"]["ok"] is False      # nothing listens on 4400 here
    assert d.id not in drv._CACHE                # cache-honesty: row dropped
    asyncio.run(h.disconnect_all())


# ================================================= driver_id / primary:"none" plumbing

def test_connspec_driver_id_roundtrip_and_tolerant_parse():
    spec = ConnSpec(backend="native", driver_id="alpaca-ab12", role="camera")
    d = spec.to_dict()
    assert d["driver_id"] == "alpaca-ab12"
    again = ConnSpec.from_dict(d)
    assert again.driver_id == "alpaca-ab12"
    # old dict without the key parses with driver_id=None (additive back-compat)
    legacy = ConnSpec.from_dict({"backend": "sim"})
    assert legacy.driver_id is None


def test_primary_none_requests_only_explicit_roles():
    spec = RigSpec(primary="none", roles={
        "camera": ConnSpec(backend="native", role="camera"),
        "guider": ConnSpec(backend="phd2", role="guider"),
    })
    assert _requested_roles(spec) == {"camera", "guider"}


def test_primary_sim_still_requests_sim_roles():
    # regression: the old primary-derived fill is untouched for real primaries
    spec = RigSpec(primary="sim")
    assert "camera" in _requested_roles(spec)
    assert "safety" in _requested_roles(spec)


def test_profile_device_driver_id_flows_to_rigspec():
    p = Profile(name="t", primary_backend="none", devices=[
        ProfileDevice(role="camera", backend="native", driver_id="alpaca-ab12",
                      dev_type="camera", dev_num=0, name="ASI2600MM"),
    ])
    rs = p.to_rigspec()
    assert rs.roles["camera"].driver_id == "alpaca-ab12"
    # old profile rows (no driver_id key) still map with driver_id=None
    p2 = Profile(name="t2", devices=[
        ProfileDevice(role="camera", backend="alpaca", host="h", port=11111),
    ])
    assert p2.to_rigspec().roles["camera"].driver_id is None


def test_connect_rig_route_accepts_primary_none_and_driver_id(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        # primary "none" + one driver_id override must NOT 422 at the body/
        # validation layer. The referenced driver doesn't exist, so the role
        # comes back as a failed RoleResult ("driver removed") — Task 2 pins
        # that message; here we only pin "not a 4xx".
        r = c.post("/api/connect/rig", json={
            "primary": "none",
            "roles": {"camera": {"backend": "native", "role": "camera",
                                  "driver_id": "alpaca-gone"}},
        })
        assert r.status_code == 200
