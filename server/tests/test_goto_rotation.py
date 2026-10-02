# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
    deletes ``ASTRODECK_FAST_TEST`` wholesale). Since #207 the fast path fakes
    the sim MOUNT's slew dwell and the rotator's move dwell as well (before it,
    this paragraph claimed so while every goto here paid the slew in real
    time). Un-faking everything costs this test about 5.7 s more (9.0-9.5 s
    against 3.3-3.7 s, measured 2026-09-24), 4.9 s of it the slew: the goto
    from the sim's default pointing plus one re-slew after the sync. It would
    anchor nothing this fixture is about: the slew and rotator dwell keep
    their own real-dwell anchor in ``test_sim_pacing.py``, as the guide
    camera's does in ``test_native_guider_e2e.py``. This fixture anchors
    exactly the solver's pacing, and nothing else."""
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
    """With no rotator the angle is still advisory: the centring runs and
    nothing turns. It is no longer SILENT (#160, mosaic S1): the result now
    carries ``rotation_unavailable`` and one warning is logged. That half is
    pinned, with its mutants, in test_rotation_unavailable.py; this test keeps
    only the part that did not change."""
    sim_hub.devices.pop("rotator", None)
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result["centered"] is True
    assert result.get("rotation") is None      # advisory: nothing turned


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
    """_setup_target's centered branch forwards the angle the acquisition
    commands, which is ``target.rotation_deg`` whenever one is planned.

    UPDATED FOR S2 (owner ruling 9, spec 5.6), which changed the spelling on
    purpose: the goto used to pass ``rotation_deg=target.rotation_deg`` and
    now passes ``_commanded_rotation(target)``, the planned angle or else the
    angle an unframed target locked on its first shot. So the pin reads the
    new spelling, and the planned half is graded on the method itself; the
    planned, locked and unlocked acquisitions on the simulator are graded by
    test_locked_angle.py. Before the update the old pin failed on the new
    spelling:
        AssertionError: assert 'rotation_deg=target.rotation_deg' in ...

    MUTATION "the goto forgets the angle" (``rotation_deg=rotation,`` in
    _setup_target's goto_and_center call made ``rotation_deg=None,``).
    Observed:
        AssertionError: _setup_target's goto_and_center no longer passes the
        commanded angle
    MUTATION "the lock outranks the plan" (``_commanded_rotation``'s
    ``if planned is not None: return planned`` removed, so a planned angle
    is commanded only through a lock). Observed:
        AssertionError: assert None == 45.0
         +  where None = _commanded_rotation(namespace(rotation_deg=45.0))
    """
    import inspect
    import re
    from types import SimpleNamespace
    from astrodeck.sequence import engine
    src = inspect.getsource(engine.SequenceEngine._setup_target)
    assert "rotation = self._commanded_rotation(target)" in src
    assert re.search(r"goto_and_center\(\s*target\.ra_hours,\s*"
                     r"target\.dec_deg,\s*rotation_deg=rotation,", src), (
        "_setup_target's goto_and_center no longer passes the commanded angle")
    eng = engine.SequenceEngine.__new__(engine.SequenceEngine)
    eng._lock_in_force = lambda target: None
    eng._rotator_connected = lambda: False
    assert eng._commanded_rotation(SimpleNamespace(rotation_deg=45.0)) == 45.0
    assert eng._commanded_rotation(SimpleNamespace(rotation_deg=0.0)) == 0.0
