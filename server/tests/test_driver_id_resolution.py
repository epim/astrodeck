"""drivers.resolve_driver_ids + hub wiring (spec §3.3, §5, cache-honesty §3.2).

The resolver is PURE over config: driver_id → concrete backend/host/port at
connect time. Missing/disabled drivers pre-fail their role (honest RoleResult,
never a 500/silent skip). A failed driver-backed connect drops that driver's
probe-cache row so /api/drivers can't report stale 'reachable'."""
import asyncio

import pytest

from astrodeck import drivers as drv
from astrodeck.config import ConfigStore
from astrodeck.devices.backend import ConnSpec, RigSpec


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = ConfigStore(tmp_path / "astrodeck.json")
    monkeypatch.setattr(drv, "config_store", s)
    drv.invalidate()
    return s


def _spec(role, **kw):
    return RigSpec(primary="none", roles={role: ConnSpec(role=role, **kw)})


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
