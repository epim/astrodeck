"""The sky and rig triggers: clouds, safety, panel readiness.

Every test here is really about ONE question — what happens when nobody knows?

A cloud rule has three possible readings, not two: cloudy, clear, and no usable
reading at all. The third is the one that decides whether this feature is safe.
If an absent reading defaults to "clear", a rig that has stopped judging its
frames looks exactly like a clear night, and a weather hold resumes into an
overcast on the strength of a measurement nobody took. If it defaults to
"cloudy", a rig with a broken detector holds until dawn and the night is lost
to a bug.

So `None` means neither: it fires nothing and it re-arms nothing. That is the
property most of this file exists to pin, because it is invisible in any test
that only ever supplies a definite sky.
"""
from __future__ import annotations

from astrodeck.sequence.instructions import (
    TriggerContext, evaluate_instructions,
)
from astrodeck.sequence.models import Instruction


def _rule(trigger: str, action: str = "pause", **kw) -> Instruction:
    return Instruction(id=f"r-{trigger}-{action}", trigger=trigger,
                       action=action, **kw)


def _run(rules, *contexts):
    """Feed a sequence of contexts through one fire_state, as the engine does.
    Returns the list of actions fired at each step."""
    state: dict = {}
    out = []
    for ctx in contexts:
        fired, state = evaluate_instructions(rules, ctx, state)
        out.append([f.action for f in fired])
    return out


def _ctx(t: float = 1000.0, **kw) -> TriggerContext:
    return TriggerContext(now_ts=t, **kw)


class TestCloudsAreEdgeTriggered:
    def test_it_fires_once_on_the_way_in_not_every_frame(self):
        """A cloud sitting overhead is a STATE. A rule that fired on every frame
        while it sat there would hold a run that is already held, once per
        sub, all night."""
        r = [_rule("on_clouds_in")]
        assert _run(r, _ctx(cloudy=True), _ctx(cloudy=True), _ctx(cloudy=True)) \
            == [["pause"], [], []]

    def test_it_re_arms_when_the_sky_clears_and_fires_again_next_time(self):
        r = [_rule("on_clouds_in")]
        assert _run(r, _ctx(cloudy=True), _ctx(cloudy=False), _ctx(cloudy=True)) \
            == [["pause"], [], ["pause"]]

    def test_clear_fires_on_the_way_out(self):
        r = [_rule("on_clouds_clear", action="hold_for_clear")]
        assert _run(r, _ctx(cloudy=True), _ctx(cloudy=False), _ctx(cloudy=False)) \
            == [[], ["hold_for_clear"], []]

    def test_the_two_rules_never_fire_together(self):
        """They read ONE metric — clear is derived by negating cloudy rather
        than given its own input. Two inputs could disagree, and a hold and its
        own resume firing on the same frame is a run that stutters instead of
        stopping."""
        r = [_rule("on_clouds_in"), _rule("on_clouds_clear", action="hold_for_clear")]
        for step in _run(r, _ctx(cloudy=True), _ctx(cloudy=False),
                         _ctx(cloudy=True), _ctx(cloudy=None)):
            assert len(step) <= 1, f"both fired at once: {step}"


class TestAnUnreadableSkyFiresNothing:
    def test_none_does_not_fire_either_rule(self):
        r = [_rule("on_clouds_in"), _rule("on_clouds_clear", action="hold_for_clear")]
        assert _run(r, _ctx(cloudy=None), _ctx(cloudy=None)) == [[], []]

    def test_none_does_not_RE_ARM_a_rule_that_already_fired(self):
        """The dangerous one. If an unreadable frame counted as "not cloudy" it
        would re-arm the hold rule, and the next genuine cloud reading would
        hold a run that is already held."""
        r = [_rule("on_clouds_in")]
        assert _run(r, _ctx(cloudy=True), _ctx(cloudy=None), _ctx(cloudy=True)) \
            == [["pause"], [], []]

    def test_a_reading_going_stale_mid_hold_does_not_resume_the_run(self):
        """THE FAILURE THIS WHOLE DESIGN IS AGAINST.

        The sky clouds over, the run holds, and then the detector stops
        producing verdicts — the camera is idle during a hold, so no new frame
        means no new judgement. If "no reading" read as "clear", the resume rule
        would fire and the mount would go back to shooting an overcast, and the
        frames would look exactly like the ones before the hold."""
        r = [_rule("on_clouds_in"), _rule("on_clouds_clear", action="hold_for_clear")]
        steps = _run(r, _ctx(cloudy=True), _ctx(cloudy=None), _ctx(cloudy=None),
                     _ctx(cloudy=None))
        assert steps[0] == ["pause"]
        assert steps[1:] == [[], [], []], \
            "a hold must outlive the reading that caused it"

    def test_a_clear_edge_is_still_authorable_even_though_the_hold_self_releases(self):
        """`on_clouds_clear` remains a real trigger - a flow may want to NOTIFY
        on clearing, or start something. What it must never be relied on for is
        releasing a hold: see ActionKind's note on why a paused loop has no
        frame boundaries to evaluate it at."""
        r = [_rule("on_clouds_in"), _rule("on_clouds_clear", action="hold_for_clear")]
        steps = _run(r, _ctx(cloudy=True), _ctx(cloudy=None), _ctx(cloudy=False))
        assert steps == [["pause"], [], ["hold_for_clear"]]


class TestSafetyAndPanel:
    def test_unsafe_is_edge_triggered_too(self):
        r = [_rule("on_unsafe", action="abort")]
        assert _run(r, _ctx(unsafe=True), _ctx(unsafe=True)) == [["abort"], []]

    def test_an_unknown_safety_reading_does_not_abort(self):
        """A monitor that has gone quiet is not a monitor saying "unsafe". The
        stale-safety decision belongs to the safety subsystem, which already has
        its own fail-closed rule; a duplicate one here would abort a good night
        on a dropped poll."""
        r = [_rule("on_unsafe", action="abort")]
        assert _run(r, _ctx(unsafe=None), _ctx(unsafe=None)) == [[], []]

    def test_panel_ready_fires_once_when_the_panel_settles(self):
        r = [_rule("on_panel_ready", action="notify")]
        assert _run(r, _ctx(panel_ready=False), _ctx(panel_ready=True),
                    _ctx(panel_ready=True)) == [[], ["notify"], []]


class TestTheOldTriggersAreUntouched:
    def test_an_hfr_rule_still_behaves_exactly_as_before(self):
        r = [_rule("on_hfr_above", action="refocus", threshold=3.0)]
        assert _run(r, _ctx(frame_hfr=2.0), _ctx(frame_hfr=4.0),
                    _ctx(frame_hfr=4.5), _ctx(frame_hfr=2.0),
                    _ctx(frame_hfr=4.0)) \
            == [[], ["refocus"], [], [], ["refocus"]]

    def test_a_plan_with_no_sky_readings_fires_no_sky_rules(self):
        """Every existing plan runs with cloudy/unsafe/panel_ready at their
        defaults. Those defaults are None, so nothing new can fire on a plan
        that never asked for it."""
        r = [_rule("on_clouds_in"), _rule("on_unsafe", action="abort"),
             _rule("on_panel_ready", action="notify")]
        assert _run(r, _ctx(), _ctx(), _ctx()) == [[], [], []]


class TestOnceAndCooldownStillApply:
    def test_once_holds_across_a_re_arm(self):
        r = [_rule("on_clouds_in", once=True)]
        assert _run(r, _ctx(cloudy=True), _ctx(cloudy=False), _ctx(cloudy=True)) \
            == [["pause"], [], []]

    def test_cooldown_suppresses_a_rapid_second_edge(self):
        """Broken cloud can cross the threshold repeatedly in minutes. Without a
        cooldown the run would hold and resume on every gap."""
        r = [_rule("on_clouds_in", cooldown_s=600)]
        steps = _run(r, _ctx(t=1000, cloudy=True), _ctx(t=1010, cloudy=False),
                     _ctx(t=1020, cloudy=True), _ctx(t=2000, cloudy=False),
                     _ctx(t=2010, cloudy=True))
        assert steps == [["pause"], [], [], [], ["pause"]]
