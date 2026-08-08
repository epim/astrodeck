"""``rotate_to_pa`` must stop turning a camera it is not helping.

Measured on the rig 2026-08-08, slewing to M52: the loop ran all five attempts
with the error GROWING (56.8° -> 76.4°), physically sweeping the camera through
a full turn and about half of another, and reported

    rotator failed to converge after 5 attempts (last error 76.4°)

— one number, at the end, from five solves it had already paid for. Nothing in
the loop ever compared one attempt against the last, so a correction applied
with the wrong sign or the wrong wrap could only ever run to the attempt limit.

Two things are pinned here: the loop abandons the first time an attempt fails
to improve, and BOTH exits carry the per-attempt trail that makes a sign/wrap
bug diagnosable without another night on the rig.
"""
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.devices.base import DeviceError


def _diverging(sim_hub, monkeypatch, errors_deg):
    """Make each successive solve report a PA further from any target.

    Driven through the SOLVER rather than the rotator so the loop under test is
    entirely real: it solves, syncs, computes its own correction and commands
    its own move. Only the sky's answer is scripted.
    """
    from astrodeck.solve.simsolver import SimSolver
    from astrodeck.solve.base import SolveResult
    seen = {"n": 0}

    async def solve(self, fits_path, **kw):
        i = min(seen["n"], len(errors_deg) - 1)
        seen["n"] += 1
        return SolveResult(True, ra_hours=0.0, dec_deg=0.0,
                           rotation_deg=errors_deg[i], message="scripted")

    monkeypatch.setattr(SimSolver, "solve", solve)
    return seen


@pytest.mark.asyncio
async def test_a_rotation_that_is_getting_worse_is_abandoned(sim_hub, monkeypatch):
    # Target 0°: solved PA marches AWAY from it every attempt.
    seen = _diverging(sim_hub, monkeypatch, [40.0, 55.0, 70.0, 85.0, 100.0])
    with pytest.raises(DeviceError) as excinfo:
        await sim_hub.rotate_to_pa(0.0)

    assert "not converging" in str(excinfo.value), str(excinfo.value)
    # The whole point: it stopped EARLY. Five attempts is the old behaviour and
    # each one is a real rotation of a real camera.
    assert seen["n"] < 5, (
        f"it solved {seen['n']} times — the loop ran on past the divergence")


@pytest.mark.asyncio
async def test_the_refusal_carries_every_attempt(sim_hub, monkeypatch):
    _diverging(sim_hub, monkeypatch, [40.0, 55.0, 70.0, 85.0, 100.0])
    with pytest.raises(DeviceError) as excinfo:
        await sim_hub.rotate_to_pa(0.0)
    msg = str(excinfo.value)

    # The numbers a sign/wrap diagnosis needs: what was solved, what was wanted,
    # how far off it was, and what move was commanded to close it.
    assert "solved PA" in msg or "Attempts" in msg, msg
    assert "40.0" in msg and "55.0" in msg, (
        f"the per-attempt trail is missing from the refusal: {msg}")


@pytest.mark.asyncio
async def test_a_slowly_improving_rotation_is_not_abandoned(sim_hub, monkeypatch):
    """The positive control, and the reason the guard has a threshold.

    A real rotator approaches over a few attempts. If shrinking-but-not-yet-in-
    tolerance counted as failure the guard would break every honest rotation,
    and the divergence test above would still pass.
    """
    # Converging on target 0°: 40° -> 20° -> 8° -> 0.2° (inside tolerance).
    _diverging(sim_hub, monkeypatch, [40.0, 20.0, 8.0, 0.2])
    result = await sim_hub.rotate_to_pa(0.0)
    assert result["rotated"] is True
    assert result["attempts"] >= 3, (
        "the approach was cut short — the guard is too eager")


@pytest.mark.asyncio
async def test_noise_sized_wobble_at_the_same_error_still_abandons(sim_hub,
                                                                  monkeypatch):
    """Equal-and-not-better must count as not-better.

    A loop that is stuck reports the SAME error forever rather than a growing
    one when the commanded move is a no-op — the shape a mechanical-vs-sky frame
    mix-up produces. ``>=`` in the guard is what covers it; ``>`` would let this
    run to the attempt limit.
    """
    _diverging(sim_hub, monkeypatch, [40.0, 40.0, 40.0, 40.0, 40.0])
    with pytest.raises(DeviceError) as excinfo:
        await sim_hub.rotate_to_pa(0.0)
    assert "not converging" in str(excinfo.value)


# ------------------------------------------------------- the live view (#175)
# "why would loop and the CAA fight? worst case the looped images are all
# swirly" — the rotate takes the camera off the live loop to solve, which is
# right, but it then left the panel dead and the user re-pressing Live. A
# rotate is the one yield-class caller whose frames afterwards are the SAME
# field: the tube has not moved, only the camera angle.

@pytest.mark.asyncio
async def test_a_rotate_gives_the_live_view_back(sim_hub, monkeypatch):
    _diverging(sim_hub, monkeypatch, [40.0, 20.0, 8.0, 0.2])
    await sim_hub.start_loop(1.0, 100, 30, binning=2)
    assert sim_hub.looping

    await sim_hub.rotate_to_pa(0.0)
    assert sim_hub.looping, "the rotate finished and left Live View stopped"


@pytest.mark.asyncio
async def test_the_resumed_loop_keeps_the_settings_it_had(sim_hub, monkeypatch):
    """Not a guess at settings — the ones the loop was actually started with.

    ``yield_camera_for`` rejected resuming partly because the settings lived in
    a closure and 'resume what was running' would have meant inventing them.
    They live on the hub now, so this asserts the real values come back.
    """
    _diverging(sim_hub, monkeypatch, [40.0, 20.0, 8.0, 0.2])
    await sim_hub.start_loop(2.5, 123, 17, binning=3, frame_type="Light")
    await sim_hub.rotate_to_pa(0.0)
    assert sim_hub._last_loop_settings == {
        "exposure_s": 2.5, "gain": 123, "offset": 17,
        "binning": 3, "frame_type": "Light"}


@pytest.mark.asyncio
async def test_a_failed_rotate_still_gives_the_live_view_back(sim_hub,
                                                              monkeypatch):
    """The failure path is where a dead panel hurts most: the rotate refused,
    and the user is now looking at a stopped Live View AND an error."""
    _diverging(sim_hub, monkeypatch, [40.0, 55.0, 70.0, 85.0, 100.0])
    await sim_hub.start_loop(1.0, 100, 30, binning=2)
    with pytest.raises(DeviceError):
        await sim_hub.rotate_to_pa(0.0)
    assert sim_hub.looping, "a refused rotate left Live View stopped"


@pytest.mark.asyncio
async def test_a_rotate_with_no_live_view_does_not_start_one(sim_hub,
                                                             monkeypatch):
    """The positive control for the other direction. Resuming is 'give back
    what was there', never 'switch the camera on because a rotate happened' —
    which would take the sensor mid-sequence for a panel nobody opened."""
    _diverging(sim_hub, monkeypatch, [40.0, 20.0, 8.0, 0.2])
    assert not sim_hub.looping
    await sim_hub.rotate_to_pa(0.0)
    assert not sim_hub.looping, "the rotate started a loop that never existed"
