"""goto_and_center + rotation: rotate-before-center order, degrade-on-failure,
meridian-flip mod-180 no-op, sequence/route threading."""
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)


@pytest.fixture
def _real_solve_dwell(monkeypatch):
    """Timing-realism anchor for the GOTO lane. ``SimSolver.solve``'s 1.2 s
    "pretend to work" pause now routes through ``devices.sim._sim_delay``, so
    the suite-wide fast-path (conftest's ``_fast_sim_delays``) collapses it to
    0 — a centering loop that used to pay several real seconds per attempt now
    pays none. THIS test opts that ONE pause back in (by restoring the
    identity delay on the ``simsolver`` module's imported reference), so the
    plain goto→solve→sync→re-slew centering loop — the lane's core contract —
    is still proven to converge at a realistic per-attempt cadence and the
    collapsed-pacing runs are never the only evidence.

    Deliberately NARROWER than the ``_real_dwell`` idiom used elsewhere (which
    deletes ``ASTRODECK_FAST_TEST`` wholesale): un-faking the sim MOUNT's slew
    dwell too costs ~8 s here and anchors nothing this change touched — Phase 1
    already keeps its own real-dwell device anchor in
    ``test_native_guider_e2e.py``. This fixture anchors exactly the pacing this
    phase faked, and nothing else."""
    from astrodeck.solve import simsolver
    monkeypatch.setattr(simsolver, "_sim_delay", lambda seconds: seconds)
    yield


@pytest.mark.asyncio
async def test_goto_and_center_rotates_before_centering(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 20.0
    rig.rotator_mech_deg = 0.0
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True
    assert result["rotation"]["rotated"] is True
    truth = (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0
    assert abs((truth - 90.0 + 90.0) % 180.0 - 90.0) <= 1.0


@pytest.mark.asyncio
async def test_goto_without_rotation_unchanged(sim_hub, _real_solve_dwell):
    result = await sim_hub.goto_and_center(5.0, 10.0)
    assert result["centered"] is True
    assert result.get("rotation") is None
    assert "rotation_skipped" not in result


@pytest.mark.asyncio
async def test_rotation_failure_degrades_not_aborts(sim_hub, monkeypatch):
    async def boom(self, *a, **k):
        from astrodeck.devices.base import DeviceError
        raise DeviceError("clouds over the rotate solve")
    monkeypatch.setattr(type(sim_hub), "rotate_to_pa", boom)
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True          # centering still ran
    assert result["rotation_skipped"] is True


@pytest.mark.asyncio
async def test_no_rotator_means_advisory_only(sim_hub):
    sim_hub.devices.pop("rotator", None)
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True
    assert result.get("rotation") is None      # silently advisory, as today


@pytest.mark.asyncio
async def test_already_at_pa_mod180_noops(sim_hub):
    """Meridian-flip contract: a frame 180° from target is EQUAL — the loop
    must converge with zero physical moves."""
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 270.0               # camera PA 270 == target 90 mod 180
    before = rig.rotator_mech_deg
    result = await sim_hub.rotate_to_pa(90.0)
    assert result["rotated"] is True
    assert result["attempts"] == 1
    assert rig.rotator_mech_deg == pytest.approx(before)


def test_goto_body_carries_rotation():
    from astrodeck.api.app import GotoBody
    b = GotoBody(ra_hours=1.0, dec_deg=2.0, rotation_deg=133.0)
    assert b.rotation_deg == 133.0
    assert GotoBody(ra_hours=1.0, dec_deg=2.0).rotation_deg is None


def test_goto_body_rejects_nan_rotation():
    """NaN bounds (post-review hardening): a NaN/inf rotation_deg must 422 at
    the model, never sail into rotate_to_pa's mod-360 math."""
    from pydantic import ValidationError

    from astrodeck.api.app import GotoBody
    with pytest.raises(ValidationError):
        GotoBody(ra_hours=1.0, dec_deg=2.0, rotation_deg=float("nan"))
    with pytest.raises(ValidationError):
        GotoBody(ra_hours=1.0, dec_deg=2.0, rotation_deg=float("inf"))


def test_sequence_passes_rotation():
    """_setup_target's centered branch forwards target.rotation_deg."""
    import inspect
    from astrodeck.sequence import engine
    src = inspect.getsource(engine.SequenceEngine._setup_target)
    assert "rotation_deg=target.rotation_deg" in src
