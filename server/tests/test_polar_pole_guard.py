"""TPPA must refuse near a celestial pole rather than report a wrong number.

Observed on the rig 2026-08-03: a run at Dec +85 -- 5 degrees from the pole --
reported 580 arcmin (9.7 degrees) of polar error and told the operator to
"adjust the mount", on a rig that had just produced 16 unguided 10 s subs at a
steady HFR of 4.2. Acting on it would have destroyed a working alignment.

Three-point alignment fits a circle through three solved positions as the mount
turns in RA. That circle's radius IS the distance from the pole, so this close
the fit cannot resolve an axis. The engine had already raised
position_angle_spread_large and initial_error_large and reported anyway.
"""
from __future__ import annotations

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.polar.native import (MIN_POLE_DISTANCE_DEG, _mount_dec,
                                    _refuse_near_pole)


class _Tel:
    def __init__(self, dec):
        self._dec = dec

    async def get_position(self):
        return 5.0, self._dec


class _MuteTel:
    async def get_position(self):
        raise RuntimeError("this mount will not say")


@pytest.mark.parametrize("dec", [89.99, 85.0, -85.0, -89.99, 71.0, -71.0])
async def test_refuses_near_either_pole(dec):
    """Both poles, including the parked position a mount sits at by default."""
    with pytest.raises(DeviceError) as e:
        await _refuse_near_pole(dec, "the mount reports")
    msg = str(e.value)
    assert "pole" in msg.lower()
    # It must name a reachable fix, not just the fault: the operator did nothing
    # wrong, and "parked" IS this state.
    assert "equator" in msg.lower(), f"no way forward offered: {msg}"


@pytest.mark.parametrize("dec", [0.0, 45.0, -45.0, 70.0, -70.0, 69.9])
async def test_allows_a_workable_lever_arm(dec):
    """At 20 degrees out the circle has enough radius to fit."""
    await _refuse_near_pole(dec, "the mount reports")   # must not raise


async def test_the_boundary_is_the_documented_constant():
    """Guards the threshold itself: a silent change to 5 degrees would restore
    the exact failure this exists to prevent."""
    assert MIN_POLE_DISTANCE_DEG == 20.0
    await _refuse_near_pole(90.0 - MIN_POLE_DISTANCE_DEG, "x")      # allowed
    with pytest.raises(DeviceError):
        await _refuse_near_pole(90.0 - MIN_POLE_DISTANCE_DEG + 0.1, "x")


async def test_a_mount_that_will_not_report_is_not_refused():
    """Refusing on a missing reading would break rigs whose mounts are quiet.
    The solved-position check downstream is the real gate."""
    assert await _mount_dec(_MuteTel()) is None
    await _refuse_near_pole(None, "the mount reports")   # must not raise


async def test_the_message_carries_the_numbers():
    """A bare refusal leaves the user guessing how far off they are."""
    with pytest.raises(DeviceError) as e:
        await _refuse_near_pole(85.0, "the plate solve puts you")
    msg = str(e.value)
    assert "85.0" in msg and "5.0" in msg, msg
    assert "the plate solve puts you" in msg, "must say WHICH source it used"


async def test_drive_refuses_before_touching_the_mount(monkeypatch):
    """The guard must be ON the path, not merely correct in isolation.

    A right answer wired into a function nobody calls is the #112 autofocus bug
    (a working metric on a code path the rig does not run), so this drives the
    real entry point and asserts no slew was attempted."""
    from astrodeck.polar import native as nat

    slews: list = []

    class _Cam:
        name = "cam"

    class _ParkedTel:
        async def get_position(self):
            return 5.0, 89.99            # parked at the pole

        async def slew(self, ra, dec):
            slews.append((ra, dec))

    class _Hub:
        _motion_epoch = 0

        def require(self, role):
            return _ParkedTel() if role == "telescope" else _Cam()

    class _Session:
        def __init__(self):
            self.published: list = []

        def _publish(self, **kw):
            self.published.append(kw)

    monkeypatch.setattr(nat, "_site_dict", lambda hub: {})
    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: object())

    s = _Session()
    # The Rust wheel is imported guarded, and `run_native` checks NATIVE_AVAILABLE
    # BEFORE reaching `_drive`, where the pole guard lives. On a host without the
    # wheel — which is every CI runner in the `server` job, since only the
    # `native` job builds it — this test therefore got "native engine not
    # installed" and never exercised the guard at all. Faking the flag is the
    # same substitution the hub, telescope and session already get, and it is
    # what keeps a SAFETY guard under test on machines that cannot build Rust.
    monkeypatch.setattr(nat, "NATIVE_AVAILABLE", True)
    await nat.run_native(s, _Hub())          # run_native maps DeviceError -> terminal

    assert slews == [], "must not move a mount it has already decided not to measure"
    errs = [p for p in s.published if p.get("state") == "error"]
    assert errs, f"expected a terminal error, got {s.published}"
    assert "pole" in str(errs[-1].get("message", "")).lower()
