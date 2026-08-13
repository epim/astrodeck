"""A cloud hold stays visible as a hold, even while it is shooting darks.

OBSERVED LIVE, 2026-08-12 23:15, under solid overcast — a frame from that
minute is featureless grey with not one star in it. The run reported:

    state: running   |   cloud-hold darks 180s g125: Dark 180s [2/20]
    sky:   {"cloudy": true, "holding": true, "age_s": 786}

`_hold_for_clear` had published state="holding". Then the calibration frame
loop's own per-frame publish set state="running" again, and every client keys
off `state`. The night looked like it was proceeding normally while the tube sat
under cloud — and the watchdog watching `state` for a hold could never fire,
because the field it watched had been overwritten by the feature it was watching.

`sky.holding` was the only field telling the truth, and it exists only because
it was added the same evening for an unrelated reason. That is luck, not design.
"""
from __future__ import annotations

from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import SequencePlan


class _Hub:
    def __init__(self):
        self.devices = {}
        self.guider = None
        self.site = {}
        self.master_library = None


def _engine():
    eng = SequenceEngine(_Hub())
    eng.plan = SequencePlan(name="n")
    eng.published = []
    eng._publish = lambda: eng.published.append(dict(eng.state))
    return eng


class TestTheHoldSurvivesACaptureLoopPublish:
    def test_a_routine_running_publish_cannot_end_a_hold(self):
        eng = _engine()
        eng._holding_for_clear = True
        # exactly what _run_calibration publishes for each hold dark
        eng._set_state(state="running", detail="Dark 180s [2/20]")
        assert eng.state["state"] == "holding", (
            "the hold went invisible while it was still holding")
        assert eng.state["hold"] == "clouds"
        assert eng.state["detail"] == "Dark 180s [2/20]", (
            "the detail must still say what it is doing")

    def test_the_detail_still_reaches_the_operator(self):
        """Downgrading the state must not swallow the progress text — "holding"
        with no explanation is its own kind of silence."""
        eng = _engine()
        eng._holding_for_clear = True
        eng._set_state(state="running", detail="cloud-hold dark 5/13 at 180s")
        assert "5/13" in eng.state["detail"]


class TestTheDeliberateResumeStillGetsThrough:
    def test_naming_hold_none_ends_it(self):
        """`_hold_for_clear`'s own exit publishes state="running", hold=None
        while `_holding_for_clear` is STILL True — it is cleared in a finally.
        If that publish were downgraded too, a hold could never end."""
        eng = _engine()
        eng._holding_for_clear = True
        eng._set_state(state="running", hold=None, detail="resumed after 22 min")
        assert eng.state["state"] == "running"
        assert eng.state["hold"] is None

    def test_a_publish_that_says_nothing_about_the_hold_cannot_end_it(self):
        """The discriminator is deliberate: silence about the hold is not
        consent to end it."""
        eng = _engine()
        eng._holding_for_clear = True
        eng._set_state(state="running")
        assert eng.state["state"] == "holding"


class TestNothingChangesWhenNotHolding:
    def test_an_ordinary_run_publishes_running(self):
        eng = _engine()
        eng._holding_for_clear = False
        eng._set_state(state="running", detail="M31: Ha 180s [3/30]")
        assert eng.state["state"] == "running"
        assert eng.state.get("hold") in (None, "")

    def test_terminal_states_are_never_rewritten(self):
        """A hold that aborts or completes must be able to say so. Only
        "running" is downgraded, because only "running" is the lie."""
        for terminal in ("complete", "aborted", "error", "paused"):
            eng = _engine()
            eng._holding_for_clear = True
            eng._set_state(state=terminal, detail="x")
            assert eng.state["state"] == terminal, (
                f"a hold swallowed a {terminal} state")
