"""goto_and_center: a correction slew that changes nothing must stop the loop.

2026-08-06, on the sky: centering attempt 1 read 33.7' off target, attempt 2
read 33.7' off target — bit-identical, because the mount was not executing the
correction slews. The loop burned its remaining attempts, synced the same solve
into the mount each time, and returned a generic "not centered" that looked
like clouds. A TPPA run on the same mount was wrecked the same way.

The guard: a correction commands the FULL remaining error and solve noise is
arcseconds, so two consecutive residuals within CENTERING_STUCK_ARCMIN of each
other while still far off target (CENTERING_STUCK_MIN_ERR_FACTOR tolerances)
mean the mount is inert — stop, say so, and flag the result.
"""
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

_TARGET_RA = 5.0
_TARGET_DEC = 10.0


def _fixed_solver(dec_offset_deg: float):
    """A solve_and_sync that always reports the same place — what an honest
    solver on an inert mount produces."""
    async def solve_and_sync(exposure_s):
        return {"ra_hours": _TARGET_RA, "dec_deg": _TARGET_DEC + dec_offset_deg}
    return solve_and_sync


@pytest.mark.asyncio
async def test_an_inert_mount_stops_the_loop_and_names_itself(sim_hub, monkeypatch):
    """Two identical 30' residuals: the loop must stop at attempt 2 with a
    distinct verdict, not run out attempt 3 pretending to work."""
    monkeypatch.setattr(sim_hub, "solve_and_sync", _fixed_solver(0.5))  # 30'

    result = await sim_hub.goto_and_center(_TARGET_RA, _TARGET_DEC)

    assert result["centered"] is False
    assert result.get("did_not_move") is True, result
    assert result["attempts"] == 2, (
        "the loop kept re-slewing an inert mount instead of stopping")
    assert result["error_arcmin"] == pytest.approx(30.0, abs=1.0)


@pytest.mark.asyncio
async def test_hovering_just_above_tolerance_is_not_accused(sim_hub, monkeypatch):
    """Near the tolerance a mount legitimately hovers — mechanics are ~arcmin.
    Identical 2.4' residuals (tolerance 1.2') must NOT be called an inert
    mount; the loop runs its attempts out and returns the plain verdict."""
    monkeypatch.setattr(sim_hub, "solve_and_sync", _fixed_solver(0.04))  # 2.4'

    result = await sim_hub.goto_and_center(_TARGET_RA, _TARGET_DEC)

    assert result["centered"] is False
    assert "did_not_move" not in result, result
    assert result["attempts"] == 3, result


@pytest.mark.asyncio
async def test_a_converging_center_never_carries_the_flag(sim_hub):
    """The healthy path, untouched: the sim mount executes its slews, the loop
    converges, and the verdict carries no accusation."""
    result = await sim_hub.goto_and_center(_TARGET_RA, _TARGET_DEC)
    assert result["centered"] is True
    assert "did_not_move" not in result, result
