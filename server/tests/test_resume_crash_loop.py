"""A session that keeps crashing gets stowed, not restarted forever.

CONTINUITY IS THE RIGHT DEFAULT and it already worked: `_finalize_report` leaves
a crashed session `dormant`, `start()` arms `auto_resume`, and `ResumeArm.tick`
brings the night back every ten minutes. A run that dies to a transient fault
should come back and finish - that is the whole point of the ladder.

But restarting into the SAME fault is not continuity, it is a loop, and nothing
counted. A driver that crashes the run every time it is touched would have the
tick restart it every ten minutes until dawn - mount tracking, camera cold,
nothing recorded, no alert louder than a warning.

WHAT COUNTS AND WHAT DOES NOT. The counter is incremented only by
`end_reason="error"` and reset by every other ending. A weather veto, a recovery
hold, a refusal to start and a plain dawn cutoff are the ladder WORKING; if they
counted, three cloudy holds would park the mount on a night that was going to
clear.

WHY IT LIVES ON THE SESSION. A crash can take the process with it, and a counter
in memory resets on exactly the restart it is meant to be counting.

AND WHY GIVING UP MUST PARK. Disarming alone ends the loop and leaves the mount
tracking until dawn-park notices at -6 degrees - eight hours, for a fault at
22:00. That is trading a loud failure for a silent one.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import RESUME_GIVE_UP_AFTER, ResumeArm
from astrodeck.sequence.session import Session, session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
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


class TestTheCounterCountsCrashesOnly:
    def _session_after(self, engine, reason: str, start_at: int) -> Session:
        s = Session(name="night", plan=_plan(), status="active")
        s.crash_resumes = start_at
        engine._session = s
        engine._report_finalized = False
        engine.reporter = None
        engine._finalize_report(reason)
        return s

    def test_a_crash_increments(self, sim_hub):
        s = self._session_after(SequenceEngine(sim_hub), "error", 0)
        assert s.crash_resumes == 1

    def test_crashes_accumulate(self, sim_hub):
        s = self._session_after(SequenceEngine(sim_hub), "error", 2)
        assert s.crash_resumes == 3

    @pytest.mark.parametrize("reason", ["complete", "dawn_cutoff", "aborted",
                                        "quality", "unsafe", "cooling_skip"])
    def test_every_other_ending_resets_it(self, sim_hub, reason):
        """The distinction the whole guard rests on. A night that reached dawn,
        or was stopped, or was held off for weather, is not the failing loop -
        and if these counted, a run of bad weather would park the rig."""
        s = self._session_after(SequenceEngine(sim_hub), reason, 2)
        assert s.crash_resumes == 0, (
            f"end_reason={reason!r} was counted as a crash, so the rig would be "
            f"stowed for something that is not a fault")

    def test_a_crash_still_leaves_the_session_resumable(self, sim_hub):
        """Continuity first: the counter must not have turned a crash into a
        dead session. Below the limit, the night still comes back."""
        s = self._session_after(SequenceEngine(sim_hub), "error", 0)
        assert s.status == "dormant", (
            "a crashed session is no longer dormant, so auto-resume can never "
            "pick the night back up")


class TestGivingUpStowsTheRig:
    @pytest.fixture
    def arm(self, sim_hub):
        return ResumeArm(SequenceEngine(sim_hub), sim_hub)

    async def test_it_parks_and_warms(self, arm, monkeypatch):
        calls: list[dict] = []

        async def _wind(park, warm, close_dome=False):
            calls.append({"park": park, "warm": warm, "close_dome": close_dome})

        monkeypatch.setattr(arm.engine, "_wind_down", _wind)
        monkeypatch.setattr(session_store, "save", lambda s: None)

        s = Session(name="night", plan=_plan(), status="dormant")
        s.auto_resume = True
        s.crash_resumes = RESUME_GIVE_UP_AFTER
        await arm._give_up_and_stow(s)

        assert calls, (
            "giving up left the mount tracking - the crash loop stopped but the "
            "telescope is still following the sky with nothing recording it")
        assert calls[0]["park"] is True and calls[0]["warm"] is True

    async def test_it_disarms_so_the_next_tick_does_not_re_enter(
            self, arm, monkeypatch):
        saved: list[Session] = []

        async def _wind(park, warm, close_dome=False):
            return None

        monkeypatch.setattr(arm.engine, "_wind_down", _wind)
        monkeypatch.setattr(session_store, "save", lambda s: saved.append(s))

        s = Session(name="night", plan=_plan(), status="dormant")
        s.auto_resume = True
        s.crash_resumes = RESUME_GIVE_UP_AFTER
        await arm._give_up_and_stow(s)

        assert s.auto_resume is False, "the crash loop is still armed"
        assert saved, "the disarm was never written to disk, so a restart re-arms it"

    async def test_it_stows_once_not_once_a_minute(self, arm, monkeypatch):
        """`tick` runs every 60s. Without a latch, a session whose disarm failed
        to save would park the mount again on every tick until dawn."""
        calls: list[int] = []

        async def _wind(park, warm, close_dome=False):
            calls.append(1)

        monkeypatch.setattr(arm.engine, "_wind_down", _wind)
        monkeypatch.setattr(session_store, "save", lambda s: None)

        s = Session(name="night", plan=_plan(), status="dormant")
        s.auto_resume = True
        s.crash_resumes = RESUME_GIVE_UP_AFTER
        await arm._give_up_and_stow(s)
        await arm._give_up_and_stow(s)
        await arm._give_up_and_stow(s)
        assert len(calls) == 1, f"stowed {len(calls)} times"

    async def test_a_failed_stow_says_the_mount_may_be_tracking(
            self, arm, monkeypatch):
        """The one thing worse than not parking is not parking quietly."""
        lines: list[tuple[str, str]] = []
        import astrodeck.sequence.resume_arm as ra
        monkeypatch.setattr(ra.bus, "log",
                            lambda lvl, msg, src=None: lines.append((lvl, msg)))

        async def _boom(park, warm, close_dome=False):
            raise RuntimeError("mount link is down")

        monkeypatch.setattr(arm.engine, "_wind_down", _boom)
        monkeypatch.setattr(session_store, "save", lambda s: None)

        s = Session(name="night", plan=_plan(), status="dormant")
        s.auto_resume = True
        s.crash_resumes = RESUME_GIVE_UP_AFTER
        await arm._give_up_and_stow(s)

        hits = [(lvl, m) for lvl, m in lines if "MAY STILL BE TRACKING" in m]
        assert hits, f"a failed stow said nothing about the mount: {lines}"
        assert hits[0][0] == "error", "it will not reach the alert sinks"


class TestTheLimitIsNotReachedEarly:
    async def test_below_the_limit_the_tick_does_not_stow(self, sim_hub,
                                                          monkeypatch):
        """The guard against the guard. One crash must not end the night - the
        whole design is that a transient fault is recovered from."""
        arm = ResumeArm(SequenceEngine(sim_hub), sim_hub)
        stowed: list[int] = []

        async def _stow(session):
            stowed.append(1)

        monkeypatch.setattr(arm, "_give_up_and_stow", _stow)

        s = Session(name="night", plan=_plan(), status="dormant")
        s.auto_resume = True
        s.crash_resumes = RESUME_GIVE_UP_AFTER - 1
        monkeypatch.setattr(session_store, "armed", lambda: s)
        monkeypatch.setattr(arm, "_window_open", lambda sess, now: True)
        monkeypatch.setattr(arm, "_devices_ready", lambda: False)  # stop here

        await arm.tick()
        assert not stowed, (
            f"{RESUME_GIVE_UP_AFTER - 1} crashes already ended the night - the "
            f"rig gets fewer recovery attempts than the constant promises")

    async def test_at_the_limit_the_tick_stows(self, sim_hub, monkeypatch):
        arm = ResumeArm(SequenceEngine(sim_hub), sim_hub)
        stowed: list[int] = []

        async def _stow(session):
            stowed.append(1)

        monkeypatch.setattr(arm, "_give_up_and_stow", _stow)

        s = Session(name="night", plan=_plan(), status="dormant")
        s.auto_resume = True
        s.crash_resumes = RESUME_GIVE_UP_AFTER
        monkeypatch.setattr(session_store, "armed", lambda: s)
        monkeypatch.setattr(arm, "_window_open", lambda sess, now: True)
        monkeypatch.setattr(arm, "_devices_ready", lambda: True)

        await arm.tick()
        assert stowed, "the crash loop ran past its own limit"
