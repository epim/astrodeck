"""GN-07: a successful `goto_and_center` must record the SOLVE's own result
into `Hub._solved_pointing`, not the goto's target and not the mount's report
-- that record is what lets the capture-metadata builder write a header's
OBJCTRA/OBJCTDEC from a plate solve instead of the mount, which can drift
tens of arcmin off a held field (GN-10).

Pattern mirrors tests/test_goto_center_stuck.py: `solve_and_sync` is
monkeypatched directly on the hub so the test drives the REAL centering loop
in `Hub.goto_and_center` without needing a real solver.
"""
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

_TARGET_RA = 5.0
_TARGET_DEC = 10.0
# Close enough to the target to converge on attempt 1 (default tolerance is
# 0.02 deg = 1.2'), but NOT equal to it -- so a bug that recorded the target
# instead of the solve is caught, and NOT equal to whatever the sim mount's
# own report ends up being after its slew -- so a bug that recorded the
# mount's report instead of the solve is caught too.
_SOLVE_RA = _TARGET_RA + 0.0002       # ~0.2' of RA
_SOLVE_DEC = _TARGET_DEC + 0.005      # 0.3' of Dec


def _fixed_solver():
    async def solve_and_sync(exposure_s):
        return {"ra_hours": _SOLVE_RA, "dec_deg": _SOLVE_DEC}
    return solve_and_sync


@pytest.mark.asyncio
async def test_goto_and_center_records_the_solve_result(sim_hub, monkeypatch):
    monkeypatch.setattr(sim_hub, "solve_and_sync", _fixed_solver())
    assert sim_hub._solved_pointing is None

    result = await sim_hub.goto_and_center(_TARGET_RA, _TARGET_DEC)

    assert result["centered"] is True
    assert sim_hub._pointing_verified is True
    assert sim_hub._solved_pointing is not None
    got_ra, got_dec, _at = sim_hub._solved_pointing
    assert got_ra == pytest.approx(_SOLVE_RA)
    assert got_dec == pytest.approx(_SOLVE_DEC)
    # not the target
    assert got_ra != pytest.approx(_TARGET_RA, abs=1e-6)
    assert got_dec != pytest.approx(_TARGET_DEC, abs=1e-6)
    # not the mount's own (post-slew) report
    tel = sim_hub.devices["telescope"]
    mount_ra, mount_dec = await tel.get_position()
    assert (got_ra, got_dec) != pytest.approx((mount_ra, mount_dec), abs=1e-9)


@pytest.mark.asyncio
async def test_a_failed_centre_clears_any_previously_solved_pointing(sim_hub, monkeypatch):
    """A stale solved pointing must not survive a centering failure -- GN-07's
    capture builder trusts `_solved_pointing` only while `_pointing_verified`
    is also True, but note_pointing_verified(False, ...) clears the record
    outright so nothing downstream can read it by mistake."""
    import time
    sim_hub._pointing_verified = True
    sim_hub._solved_pointing = (1.23, 4.56, time.time())

    async def no_stars(*a, **k):
        raise RuntimeError("plate solve failed: Not enough stars.")
    monkeypatch.setattr(sim_hub, "solve_current_frame", no_stars, raising=False)
    monkeypatch.setattr(sim_hub, "solve_and_sync", no_stars)

    result = await sim_hub.goto_and_center(_TARGET_RA, _TARGET_DEC, max_attempts=1)

    assert result["centered"] is False
    assert sim_hub._pointing_verified is False
    assert sim_hub._solved_pointing is None
