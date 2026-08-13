"""The sky line must not say "cloudy" and "clear" in the same breath.

SEEN VERBATIM ON THE RIG, 04:06, during a real hold:

    cloudy, from a reading 0s old — clear (12 bright stars, 9x noise)

Both halves were individually true and the pairing was nonsense. `_state` is
debounced — it takes `consecutive` agreeing frames to move — while
`last_reason` is whatever the MOST RECENT frame said. One disagreeing frame
during a settled state puts the two in contradiction.

The debounce is right and stays. What was wrong is the sentence: it presented a
fresh reason as the explanation for a verdict that reason did not support, and
did it at four in the morning to someone who had to decide whether to get up.

The disagreement is the most useful thing on the line — the sky is turning, and
the vote says how close the verdict is to following.
"""
from __future__ import annotations

from astrodeck.sequence.cloudstate import CloudState


def _settled_cloudy(t0=1000.0):
    """Two cloudy frames: enough to settle the state at cloudy."""
    c = CloudState()
    c.observe(True, t0, score=0.8, reason="cloudy: 2 bright stars")
    c.observe(True, t0 + 60, score=0.8, reason="cloudy: 2 bright stars")
    return c


class TestTheContradiction:
    def test_a_disagreeing_frame_is_named_as_a_vote_not_pasted_on(self):
        c = _settled_cloudy()
        c.observe(False, 1120.0, score=0.14, reason="clear (12 bright stars)")
        line = c.describe(1120.0)
        assert c.cloudy(1120.0) is True, "one frame must not flip a settled state"
        assert "but the latest frame says clear" in line
        assert "1 of 2 needed to change it" in line
        # the exact shape of the original defect
        assert not line.startswith("cloudy, from a reading 0s old — clear"), line

    def test_it_reads_as_one_coherent_sentence(self):
        """Asserted VERBATIM, because the first version composed "says clear
        (clear (12 bright stars))" — the reason already opens with its own
        verdict. A near-enough assertion would have shipped that."""
        c = _settled_cloudy()
        c.observe(False, 1120.0, score=0.14, reason="clear (12 bright stars)")
        assert c.describe(1120.0) == (
            "cloudy, from a reading 0s old — but the latest frame says "
            "clear (12 bright stars), 1 of 2 needed to change it")

    def test_a_reason_that_does_NOT_name_its_verdict_still_reads(self):
        """Not every reason opens with the word — a future detector might say
        "transparency fell 40%". That must not lose the verdict."""
        c = _settled_cloudy()
        c.observe(False, 1120.0, reason="transparency recovered")
        line = c.describe(1120.0)
        assert "says clear (transparency recovered)" in line, line

    def test_the_mirror_case_a_clear_sky_going_cloudy(self):
        c = CloudState()
        c.observe(False, 1000.0, score=0.0, reason="clear (200 bright stars)")
        c.observe(False, 1060.0, score=0.0, reason="clear (200 bright stars)")
        c.observe(True, 1120.0, score=0.7, reason="cloudy: 3 bright stars")
        line = c.describe(1120.0)
        assert c.cloudy(1120.0) is False
        assert "but the latest frame says cloudy" in line
        assert "1 of 2 needed" in line


class TestAgreementIsUnchanged:
    def test_a_settled_state_with_an_agreeing_frame_says_it_plainly(self):
        c = _settled_cloudy()
        c.observe(True, 1120.0, score=0.8, reason="cloudy: 2 bright stars")
        assert c.describe(1120.0) == \
            "cloudy, from a reading 0s old — cloudy: 2 bright stars"

    def test_a_clear_run_says_clear(self):
        c = CloudState()
        c.observe(False, 1000.0, score=0.0, reason="clear (200 bright stars)")
        c.observe(False, 1060.0, score=0.0, reason="clear (200 bright stars)")
        assert c.describe(1060.0) == \
            "clear, from a reading 0s old — clear (200 bright stars)"

    def test_the_unknown_and_stale_lines_are_untouched(self):
        """The two cases that were already honest about not knowing."""
        assert CloudState().describe(1000.0) == "no cloud reading yet"
        c = _settled_cloudy()
        stale = 1060.0 + c.max_age_s + 1
        assert "too stale to act on" in c.describe(stale)


class TestTheVoteIsRealNotDecorative:
    def test_the_countdown_actually_counts_down(self):
        c = CloudState(consecutive=3)
        c.observe(True, 1000.0, reason="cloudy")
        c.observe(True, 1060.0, reason="cloudy")
        c.observe(True, 1120.0, reason="cloudy")     # settled cloudy
        c.observe(False, 1180.0, reason="clear")
        assert "1 of 3" in c.describe(1180.0)
        c.observe(False, 1240.0, reason="clear")
        assert "2 of 3" in c.describe(1240.0)
        c.observe(False, 1300.0, reason="clear")     # flips
        assert c.cloudy(1300.0) is False
        assert "but the latest frame" not in c.describe(1300.0)
