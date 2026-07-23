"""PRO-4 Task 3 — the ``dome`` role is registered in the canonical role
vocabulary (``backend.ROLES``) and served by the sim rig.

Scope note: v1 is SIM-ONLY. The Alpaca/ASCOM probe maps
(``drivers._DEV_TYPE_TO_ROLE`` / ``ascom_registry._DEV_TYPE_TO_ROLE``) are
DELIBERATELY left untouched — a real Alpaca/COM ``Dome`` client is a deferred
follow-up (spec D5). So this pins only the ROLES-union + sim-served invariants,
NOT a probe-map entry.
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
