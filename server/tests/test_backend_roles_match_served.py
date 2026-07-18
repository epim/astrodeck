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


def test_native_serves_rotator():
    from astrodeck.devices.backends.native_backend import _ROLE_TO_DEV_TYPE
    b = get_backend("native")
    assert "rotator" in b.roles
    assert _ROLE_TO_DEV_TYPE["rotator"] == "rotator"


def test_sim_serves_rotator():
    from astrodeck.devices.sim import build_sim_rig
    b = get_backend("sim")
    assert "rotator" in b.roles          # SimBackend.roles = ROLES → automatic
    assert "rotator" in build_sim_rig()


def test_nina_serves_rotator():
    from astrodeck.devices.nina import _ROLE_CLASSES
    b = get_backend("nina")
    assert "rotator" in b.roles
    assert "rotator" in _ROLE_CLASSES


# --------------------------- guide_camera role (P2-T3 fix round, D6) ---------

def test_native_serves_guide_camera():
    """The dedicated guide camera is a first-class native role: advertised AND
    mapped to a real Alpaca camera device (a second camera by dev_num), so the
    assignment UI can put a guide camera on a real Alpaca rig."""
    b = get_backend("native")
    assert "guide_camera" in b.roles
    assert _ROLE_TO_DEV_TYPE["guide_camera"] == "camera"


def test_sim_serves_guide_camera():
    b = get_backend("sim")
    assert "guide_camera" in b.roles     # SimBackend.roles = ROLES → automatic
    assert "guide_camera" in build_sim_rig()


def test_nina_and_phd2_do_not_advertise_guide_camera():
    """D5: a NINA rig lets NINA own guiding (its guide camera lives inside
    NINA/PHD2, never assigned by AstroDeck); PHD2 stays guider-only."""
    assert "guide_camera" not in get_backend("nina").roles
    assert get_backend("phd2").roles == ("guider",)
