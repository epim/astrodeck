"""driver_id + primary:"none" plumbing (equipment-drivers spec §3.3 / Phase 2).

driver_id is an ADDITIVE reference field on ConnSpec: old serialized specs
(no key) parse unchanged, and a spec that carries one round-trips it.
primary:"none" is the explicit-only rig mode: the orchestrator requests ONLY
the roles with explicit overrides — no primary-derived fill (so an Equipment
assignment surface can connect exactly what the user assigned, nothing more).
"""
import pytest

# importing the backends package self-registers "sim" (registration is lazy,
# normally triggered by hub/app connect routes) so _requested_roles(primary=
# "sim") below can resolve it when this file runs standalone.
from astrodeck.devices.backends import sim_backend  # noqa: F401  (side-effect import)
from astrodeck.devices.backend import ConnSpec, RigSpec
from astrodeck.devices.orchestrator import _requested_roles
from astrodeck.profiles import Profile, ProfileDevice


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
