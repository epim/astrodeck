"""The crash that takes the process with it is the one that was never counted.

`Session.crash_resumes` and the give-up-and-stow ladder were built for a run
that dies and leaves the server standing: `_finalize_report` sees
``end_reason="error"``, increments, and at three consecutive crashes
`ResumeArm` parks the rig instead of restarting it a fourth time.

That covers the SMALLER half. `_finalize_report` only runs if the process lives
long enough to reach it. When the server itself dies mid-run - an unhandled
crash, an OOM, a power blip, a kill - nothing finalizes anything:

    supervisor relaunches the same version (capped 30 s backoff, forever)
      -> boot_sweep flips the orphaned `active` session to `dormant`
      -> it is still `auto_resume`, so `armed()` finds it
      -> ResumeArm re-cools, re-solves, re-centres, restarts the run
      -> it dies in the same place
      -> round again, all night

`crash_resumes` stayed at 0 for every lap of that loop, so the guard that
exists to stop it could never fire. The counter was measuring the failure mode
the supervisor already handles and missing the one the supervisor causes.

BEING `active` AT BOOT *IS* THE EVIDENCE. The engine is never running at boot -
that is the premise `boot_sweep` was already written on - so a session found
`active` on disk can only mean the process died while a run was live. That is a
crash, it is already detected, and it was being thrown away.

WHAT STILL MUST NOT COUNT. Everything the other half already excludes: a clean
end of any kind resets the counter, so one power blip a night never accumulates
toward a stow. Only deaths with no clean ending between them do.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import RESUME_GIVE_UP_AFTER, ResumeArm
from astrodeck.sequence.session import Session, session_store


@pytest.fixture(autouse=True)
def capture_dir(tmp_path, monkeypatch):
    """Every test here writes real session files; without this they land in the
    developer's own captures dir and race the other xdist workers."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
async def sim_hub(monkeypatch):
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan() -> SequencePlan:
    return SequencePlan(targets=[
        Target(name="M31", ra_hours=0.712, dec_deg=41.27, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=2)])])


def _died_mid_run(crash_resumes: int = 0, auto_resume: bool = True) -> Session:
    """A session exactly as a hard process death leaves it on disk: still
    ``active``, because nothing ran to write anything else."""
    s = Session(name="night", plan=_plan(), status="active")
    s.crash_resumes = crash_resumes
    s.auto_resume = auto_resume
    session_store.save(s)
    return s


class TestBootSweepCountsTheDeath:
    def test_a_session_active_at_boot_counts_as_a_crash(self):
        s = _died_mid_run()
        session_store.boot_sweep()
        assert session_store.load(s.id).crash_resumes == 1, (
            "the process died mid-run and nothing counted it, so the crash-loop "
            "guard can never fire for the crashes that kill the server")

    def test_it_accumulates_across_restarts(self):
        """The loop this exists to break: three deaths, no clean ending."""
        s = _died_mid_run(crash_resumes=0)
        for _ in range(3):
            session_store.boot_sweep()
            # the supervisor relaunches and the run goes active again
            back = session_store.load(s.id)
            back.status = "active"
            session_store.save(back)
        assert session_store.load(s.id).crash_resumes == 3

    def test_it_is_persisted_not_just_returned(self):
        """A counter that only exists in the returned object is a counter that
        resets on the next restart, which is the bug being fixed."""
        s = _died_mid_run()
        session_store.boot_sweep()
        assert session_store.load(s.id).crash_resumes == 1

    @pytest.mark.parametrize("status", ["dormant", "complete", "abandoned"])
    def test_a_session_that_was_not_running_is_not_counted(self, status):
        """Only ``active`` is evidence of a death mid-run. A dormant session was
        put down cleanly; counting it would stow the rig for a tidy shutdown."""
        s = Session(name="night", plan=_plan(), status=status)
        s.crash_resumes = 0
        session_store.save(s)
        session_store.boot_sweep()
        assert session_store.load(s.id).crash_resumes == 0

    def test_the_sweep_still_reports_how_many_it_moved(self):
        """The existing contract (app.py logs this count) must survive."""
        _died_mid_run()
        clean = Session(name="other", plan=_plan(), status="complete")
        session_store.save(clean)
        assert session_store.boot_sweep() == 1

    def test_the_session_is_still_dormant_and_still_armed(self):
        """Continuity first, exactly as the in-process half. Below the limit a
        death must still come back and finish the night - counting it must not
        have quietly made it unresumable."""
        s = _died_mid_run()
        session_store.boot_sweep()
        back = session_store.load(s.id)
        assert back.status == "dormant" and back.auto_resume is True


class TestOneBlipANightNeverStows:
    def test_a_clean_ending_resets_what_the_sweep_counted(self, sim_hub):
        """The guard against the fix becoming a nuisance. A night that dies once
        and then reaches dawn is not a crash loop, and the next night starts from
        zero - so a rig on flaky mains is never parked for it."""
        s = _died_mid_run()
        session_store.boot_sweep()
        assert session_store.load(s.id).crash_resumes == 1

        eng = SequenceEngine(sim_hub)
        eng._session = session_store.load(s.id)
        eng._report_finalized = False
        eng.reporter = None
        eng._finalize_report("dawn_cutoff")
        assert session_store.load(s.id).crash_resumes == 0, (
            "a night that got as far as dawn still carries a crash against it, "
            "so two unlucky nights would park a rig that is working")


class TestItReachesTheSameLadder:
    """The point of counting: the existing give-up path must now be reachable
    from a process death, not only from an in-process error."""

    async def test_three_process_deaths_stow_the_rig(self, sim_hub, monkeypatch):
        calls: list[dict] = []

        async def _wind(park, warm, close_dome=False, day_darks=False):
            calls.append({"park": park, "warm": warm})

        arm = ResumeArm(SequenceEngine(sim_hub), sim_hub)
        monkeypatch.setattr(arm.engine, "_wind_down", _wind)

        s = _died_mid_run()
        for _ in range(RESUME_GIVE_UP_AFTER):
            session_store.boot_sweep()
            back = session_store.load(s.id)
            back.status = "active"
            session_store.save(back)

        armed = session_store.load(s.id)
        armed.status = "dormant"
        session_store.save(armed)
        assert armed.crash_resumes >= RESUME_GIVE_UP_AFTER

        await arm._give_up_and_stow(armed)
        assert calls and calls[0]["park"] is True and calls[0]["warm"] is True, (
            "three process deaths in a row did not put the rig away, so the "
            "mount tracks until dawn-park notices at -6 degrees")
        assert session_store.load(s.id).auto_resume is False, (
            "the session is still armed after giving up, so the next tick "
            "restarts the same crash loop")
