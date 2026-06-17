"""Registry guard: every role a backend ADVERTISES is actually serviceable.

W1.9 / W1.2 drift guard. A backend that lists a role in ``Backend.roles`` it
cannot actually fill (e.g. the over-broad ``NativeBackend.roles = ROLES`` that
advertised ``guider`` while ``get_device('guider')`` KeyErrors) would have the
W1.C picker offer a role that can never connect. This test pins, per backend,
that each advertised role maps to a real served capability -- WITHOUT any network
I/O (it inspects the static role->served maps, not live sessions).
"""
from astrodeck.devices.backend import ROLES, get_backend
from astrodeck.devices.backends import (  # noqa: F401  (self-registers all four)
    native_backend,
    nina_backend,
    phd2_backend,
    sim_backend,
)
from astrodeck.devices.backends.native_backend import _ROLE_TO_DEV_TYPE
from astrodeck.devices.nina import _ROLE_CLASSES as NINA_ROLE_CLASSES
from astrodeck.devices.sim import build_sim_rig


def test_native_roles_are_all_served_and_exclude_guider():
    b = get_backend("native")
    # every advertised native role maps to an Alpaca device type (serviceable).
    for role in b.roles:
        assert role in _ROLE_TO_DEV_TYPE, f"native advertises unserviceable {role!r}"
    # guider is NOT advertised (Alpaca has no guider device).
    assert "guider" not in b.roles
    assert "guider" not in _ROLE_TO_DEV_TYPE


def test_nina_roles_are_all_served_including_switch_excluding_safety():
    b = get_backend("nina")
    # guider is served via the rig's "guider" key, not _ROLE_CLASSES; every other
    # advertised role must be a real NINA role class (serviceable).
    for role in b.roles:
        if role == "guider":
            continue
        assert role in NINA_ROLE_CLASSES, f"nina advertises unserviceable {role!r}"
    # switch is fillable; safety is correctly omitted (no NINA SafetyMonitor).
    assert "switch" in b.roles
    assert "safety" not in b.roles
    assert "safety" not in NINA_ROLE_CLASSES


def test_sim_roles_are_all_served():
    b = get_backend("sim")
    rig = build_sim_rig()
    for role in b.roles:
        if role == "guider":
            # the sim guider is served via native_guider() (a SimGuider), NOT a
            # rig-dict device, exactly like the orchestrator resolves it.
            continue
        assert role in rig, f"sim advertises unserviceable {role!r}"


def test_phd2_roles_are_guider_only():
    b = get_backend("phd2")
    assert b.roles == ("guider",)


def test_no_backend_advertises_a_role_outside_canonical_ROLES():
    for name in ("sim", "nina", "native", "phd2"):
        b = get_backend(name)
        for role in b.roles:
            assert role in ROLES, f"{name} advertises non-canonical role {role!r}"
