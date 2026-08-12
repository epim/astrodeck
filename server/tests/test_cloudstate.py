"""The debounced cloud verdict — and the three ways a naive one would lie.

This is the state a running telescope is steered by, so each test below names
the night it would cost.
"""
from __future__ import annotations

from astrodeck.sequence.cloudstate import (
    CloudState, DEFAULT_CONSECUTIVE, verdict_from_info,
)


class TestOneFrameIsNotWeather:
    def test_a_single_cloudy_frame_does_not_flip_the_state(self):
        """A satellite, a branch, one thin patch. Holding the night for a bird
        is the failure mode of acting on a single frame."""
        s = CloudState()
        assert s.observe(True, 100.0) is None
        assert s.cloudy(100.0) is None

    def test_two_agreeing_frames_do(self):
        s = CloudState()
        s.observe(True, 100.0)
        s.observe(True, 110.0)
        assert s.cloudy(110.0) is True

    def test_a_disagreeing_frame_resets_the_vote(self):
        """Two cloudy frames either side of a clear one are not two consecutive
        cloudy frames — the clear one is evidence too."""
        s = CloudState()
        s.observe(True, 100.0)
        s.observe(False, 110.0)
        s.observe(True, 120.0)
        assert s.cloudy(120.0) is None, "the vote restarted, nothing is settled"
        s.observe(True, 130.0)
        assert s.cloudy(130.0) is True

    def test_the_default_is_two_not_one_and_not_five(self):
        # One flips on a satellite. Five, at 180s a sub, is fifteen minutes of
        # shooting through cloud before anything reacts.
        assert DEFAULT_CONSECUTIVE == 2

    def test_a_lone_contrary_frame_during_a_settled_state_changes_nothing(self):
        s = CloudState()
        s.observe(False, 100.0)
        s.observe(False, 110.0)
        assert s.cloudy(110.0) is False
        s.observe(True, 120.0)
        assert s.cloudy(120.0) is False, "one frame cannot unseat a settled state"


class TestTheAnswerDecays:
    def test_a_fresh_verdict_is_readable(self):
        s = CloudState(max_age_s=900.0)
        s.observe(True, 100.0)
        s.observe(True, 110.0)
        assert s.cloudy(200.0) is True

    def test_a_stale_verdict_becomes_UNKNOWN_never_clear(self):
        """THE ONE THAT MATTERS. During a hold no science frames are taken, so
        the newest verdict ages. Past the budget it is a memory, not evidence —
        and a memory must not read as a clear sky."""
        s = CloudState(max_age_s=900.0)
        s.observe(True, 100.0)
        s.observe(True, 110.0)
        assert s.cloudy(110.0 + 901.0) is None
        assert s.cloudy(110.0 + 901.0) is not False, \
            "a stale reading must never be mistaken for 'clear'"

    def test_age_is_measured_from_the_last_LOOK_not_the_last_change(self):
        """How long ago somebody looked, not how long ago the sky moved. A
        stable sky watched every minute is not a stale reading."""
        s = CloudState(max_age_s=900.0)
        s.observe(False, 100.0)
        s.observe(False, 110.0)
        for t in range(200, 2000, 100):
            s.observe(False, float(t))
        assert s.cloudy(1900.0) is False

    def test_never_observed_reads_unknown(self):
        assert CloudState().cloudy(500.0) is None
        assert CloudState().age_s(500.0) is None


class TestForget:
    def test_it_drops_the_state_so_a_new_filter_starts_a_fresh_vote(self):
        """A narrowband sub and an L sub do not score alike. Carrying a verdict
        across a filter change would read an optical difference as weather."""
        s = CloudState()
        s.observe(True, 100.0)
        s.observe(True, 110.0)
        assert s.cloudy(110.0) is True
        s.forget()
        assert s.cloudy(110.0) is None
        s.observe(True, 120.0)
        assert s.cloudy(120.0) is None, "and it really is a fresh vote"


class TestDescribeIsHonest:
    def test_it_says_when_it_has_never_looked(self):
        assert "no cloud reading yet" in CloudState().describe(100.0)

    def test_it_says_stale_rather_than_guessing(self):
        s = CloudState(max_age_s=100.0)
        s.observe(True, 0.0)
        s.observe(True, 10.0)
        assert "too stale to act on" in s.describe(500.0)

    def test_it_reports_the_reason_the_detector_gave(self):
        s = CloudState()
        s.observe(True, 100.0, score=0.8, reason="3 bright stars, contrast 2.1x")
        s.observe(True, 110.0, score=0.8, reason="3 bright stars, contrast 2.1x")
        assert "3 bright stars" in s.describe(110.0)


class TestReadingAFrame:
    def test_it_pulls_the_verdict_out_of_a_capture_payload(self):
        got = verdict_from_info({"cloud": {"cloudy": True, "score": 0.77,
                                           "reason": "sparse"}})
        assert got == (True, 0.77, "sparse")

    def test_a_frame_with_NO_verdict_returns_None_not_a_clear_sky(self):
        """A stretched frame carries no cloud key — the contrast metric needs
        unstretched pixels. Defaulting that to 'clear' would let a rig whose
        preview path changed shoot happily through an overcast."""
        assert verdict_from_info({"stats": {}}) is None
        assert verdict_from_info({}) is None
        assert verdict_from_info({"cloud": {}}) is None

    def test_a_malformed_score_does_not_raise(self):
        got = verdict_from_info({"cloud": {"cloudy": False, "score": None}})
        assert got == (False, None, "")
