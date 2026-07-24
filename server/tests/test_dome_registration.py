"""PRO-4 Task 3 — the ``dome`` role is registered in the canonical role
vocabulary (``backend.ROLES``), served by the sim rig, AND (once the real
``AlpacaDome`` client landed) routed by the Alpaca/ASCOM discovery probe maps so
a discovered real Dome/CoverCalibrator is OFFERED for assignment, not skipped.

History: v1 was sim-only and the probe maps were deliberately left untouched
(spec D5, deferred). The real Alpaca clients (``AlpacaDome`` /
``AlpacaCoverCalibrator``) now exist, so the probe maps route both types — this
file pins the ROLES-union, the sim-served invariant, AND the probe-map wiring.
"""
from astrodeck.devices.backend import ROLES
from astrodeck.devices.sim import build_sim_rig


def test_dome_is_in_canonical_roles():
    assert "dome" in ROLES


def test_dome_union_preserves_covercalibrator():
    # PRO-4 lands ADDITIVELY on top of PRO-5's covercalibrator — union, never
    # replace. Both must be present.
    assert "covercalibrator" in ROLES
    assert "dome" in ROLES


def test_sim_rig_serves_dome():
    # With "dome" in ROLES and a SimDome in build_sim_rig, every sim rig now
    # connects hub.devices["dome"].
    assert "dome" in build_sim_rig()


def test_probe_maps_route_dome_and_covercalibrator():
    # Real AlpacaDome/AlpacaCoverCalibrator exist, so a discovered Dome or
    # CoverCalibrator maps to its role (offered, not skipped) on both the Alpaca
    # and the COM-host (ASCOM) discovery paths.
    from astrodeck.drivers import _DEV_TYPE_TO_ROLE as alpaca_map
    from astrodeck.devices.ascom_registry import _DEV_TYPE_TO_ROLE as ascom_map
    from astrodeck.devices.backends.native_backend import _ROLE_TO_DEV_TYPE
    for m in (alpaca_map, ascom_map):
        assert m["dome"] == "dome"
        assert m["covercalibrator"] == "covercalibrator"
    # reverse map: a role-based connect resolves the dev_type.
    assert _ROLE_TO_DEV_TYPE["dome"] == "dome"
    assert _ROLE_TO_DEV_TYPE["covercalibrator"] == "covercalibrator"
