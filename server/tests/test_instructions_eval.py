import time

import pytest

from astrodeck.sequence.models import Condition, Instruction, Predicate
from astrodeck.sequence.instructions import (
    TriggerContext, FireRecord, evaluate_instructions, parse_hhmm,
)


def _fire(instrs, ctx, state=None):
    return evaluate_instructions(instrs, ctx, state or {})


def test_empty_is_noop():
    fired, st = _fire([], TriggerContext(now_ts=1000.0))
    assert fired == [] and st == {}


def test_hfr_above_is_edge_triggered_not_every_frame():
    i = Instruction(id="a", trigger="on_hfr_above", threshold=3.0, action="refocus")
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=1.0, frame_hfr=4.0), st)   # cross up
    f2, st = _fire([i], TriggerContext(now_ts=2.0, frame_hfr=4.5), st)   # stays high
    f3, st = _fire([i], TriggerContext(now_ts=3.0, frame_hfr=2.0), st)   # drop -> re-arm
    f4, st = _fire([i], TriggerContext(now_ts=4.0, frame_hfr=5.0), st)   # cross up again
    assert [len(f) for f in (f1, f2, f3, f4)] == [1, 0, 0, 1]
    assert f1[0].action == "refocus"


def test_once_fires_at_most_once():
    i = Instruction(id="b", trigger="on_frame_rejected", action="notify", once=True)
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True), st)
    f2, st = _fire([i], TriggerContext(now_ts=2.0, frame_rejected=True), st)
    assert len(f1) == 1 and len(f2) == 0


def test_cooldown_throttles():
    i = Instruction(id="c", trigger="on_frame_rejected", action="dither", cooldown_s=100)
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=1000.0, frame_rejected=True), st)
    f2, st = _fire([i], TriggerContext(now_ts=1050.0, frame_rejected=True), st)  # within
    f3, st = _fire([i], TriggerContext(now_ts=1200.0, frame_rejected=True), st)  # past
    assert [len(f) for f in (f1, f2, f3)] == [1, 0, 1]


def test_only_target_gate():
    i = Instruction(id="d", trigger="on_frame_rejected", action="pause", only_target="M31")
    f_off, _ = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True, active_target="M42"))
    f_on, _ = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True, active_target="M31"))
    assert len(f_off) == 0 and len(f_on) == 1


def test_at_time_fires_once_after_time():
    now = time.time()
    hhmm = time.strftime("%H:%M", time.localtime(now - 120))  # 2 min ago
    i = Instruction(id="e", trigger="at_time", at_time=hhmm, action="notify")
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=now), st)
    f2, st = _fire([i], TriggerContext(now_ts=now + 60), st)
    assert len(f1) == 1 and len(f2) == 0


def test_order_is_list_order():
    a = Instruction(id="n", trigger="on_frame_rejected", action="notify")
    b = Instruction(id="z", trigger="on_frame_rejected", action="abort")
    fired, _ = _fire([a, b], TriggerContext(now_ts=1.0, frame_rejected=True))
    assert [f.action for f in fired] == ["notify", "abort"]


def test_disabled_never_fires():
    i = Instruction(id="x", enabled=False, trigger="on_frame_rejected", action="pause")
    fired, _ = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True))
    assert fired == []


def test_none_metric_never_fires():
    i = Instruction(id="g", trigger="on_guide_rms_above", threshold=1.0, action="pause")
    fired, _ = _fire([i], TriggerContext(now_ts=1.0, guide_rms=None))
    assert fired == []


# ------------------------------------------------- compound AND/OR grammar
# The whole 1-level expression is edge-triggered as ONE level. Every case below
# leaves the FLAT trigger (on_frame_rejected) unsatisfied (frame_rejected stays
# False), so any fire proves `when` OVERRIDES the flat path.
_HFR = Predicate(kind="hfr_above", threshold=3.0)
_RMS = Predicate(kind="guide_rms_above", threshold=1.0)
_DONE = Predicate(kind="target_complete")


@pytest.mark.parametrize("op,terms,frames,expect", [
    # AND: fires only on the rising edge of the WHOLE expression, re-arms when
    # the expression goes decisively false.
    ("all", [_HFR, _RMS],
     [dict(frame_hfr=4.0, guide_rms=0.5), dict(frame_hfr=4.0, guide_rms=2.0),
      dict(frame_hfr=4.0, guide_rms=2.0), dict(frame_hfr=1.0, guide_rms=2.0),
      dict(frame_hfr=4.0, guide_rms=2.0)],
     [0, 1, 0, 0, 1]),
    # OR: either term suffices; stuck-true never re-fires.
    ("any", [_HFR, _RMS],
     [dict(frame_hfr=1.0, guide_rms=0.5), dict(frame_hfr=4.0, guide_rms=0.5),
      dict(frame_hfr=4.0, guide_rms=2.0), dict(frame_hfr=1.0, guide_rms=0.5),
      dict(frame_hfr=1.0, guide_rms=2.0)],
     [0, 1, 0, 0, 1]),
    # AND + unreadable metric = INDETERMINATE: no fire, armed left untouched, so
    # the very next decisive-true frame still fires the rising edge.
    ("all", [_HFR, _RMS],
     [dict(frame_hfr=4.0, guide_rms=None), dict(frame_hfr=4.0, guide_rms=2.0)],
     [0, 1]),
    # AND short-circuits on a decisive False even with a None sibling (re-arms).
    ("all", [_HFR, _RMS],
     [dict(frame_hfr=4.0, guide_rms=2.0), dict(frame_hfr=1.0, guide_rms=None),
      dict(frame_hfr=4.0, guide_rms=2.0)],
     [1, 0, 1]),
    # OR short-circuits on a decisive True even with a None sibling.
    ("any", [_HFR, _RMS],
     [dict(frame_hfr=4.0, guide_rms=None)],
     [1]),
    # a momentary predicate inside a compound fires once, then re-arms.
    ("all", [_DONE, _HFR],
     [dict(target_complete=True, frame_hfr=4.0),
      dict(target_complete=False, frame_hfr=4.0),
      dict(target_complete=True, frame_hfr=4.0)],
     [1, 0, 1]),
])
def test_compound_condition_is_edge_triggered(op, terms, frames, expect):
    i = Instruction(id="cmp", trigger="on_frame_rejected", action="notify",
                    message="x", when=Condition(op=op, terms=terms))
    st: dict = {}
    got = []
    for n, kw in enumerate(frames):
        fired, st = _fire([i], TriggerContext(now_ts=100.0 + n, **kw), st)
        got.append(len(fired))
    assert got == expect


# ---------------------------------------------------- at_time across midnight
#
# `parse_hhmm` resolved HH:MM against the current CALENDAR day, which splits an
# observing night down the middle. An evening run starting at 21:00 resolved a
# "03:00" rule to 03:00 THAT MORNING — eighteen hours in the past — so the rule
# was true on the very first sub. "At 03:00 -> stop the session", rendered in
# the UI as exactly that, ended the night at 21:00. The mirror case is quieter
# and just as wrong: "23:00" in a run that started at 00:30 resolved to 23:00
# tomorrow and never fired.

def _local(y, mo, d, hh, mm):
    return time.mktime((y, mo, d, hh, mm, 0, 0, 0, -1))


def test_a_post_midnight_rule_does_not_fire_in_the_evening():
    evening = _local(2026, 8, 4, 21, 0)          # 21:00, run starts
    ts = parse_hhmm("03:00", evening)
    assert ts is not None
    assert ts > evening, (
        "03:00 must resolve to the COMING 03:00, not this morning's — "
        f"got {time.ctime(ts)} from a 21:00 start")
    assert ts - evening == pytest.approx(6 * 3600, abs=3600)


def test_an_evening_rule_seen_after_midnight_resolves_to_the_night_just_gone():
    after_midnight = _local(2026, 8, 5, 0, 30)   # 00:30, run in progress
    ts = parse_hhmm("23:00", after_midnight)
    assert ts is not None
    assert ts < after_midnight, (
        "23:00 seen at 00:30 belongs to the night in progress, not +23h "
        f"tomorrow — got {time.ctime(ts)}")


def test_a_time_close_to_now_still_resolves_to_now():
    t = _local(2026, 8, 4, 22, 0)
    assert parse_hhmm("22:05", t) == pytest.approx(t + 300, abs=1)
    assert parse_hhmm("21:55", t) == pytest.approx(t - 300, abs=1)


def test_malformed_input_is_still_refused():
    t = _local(2026, 8, 4, 22, 0)
    for bad in ("", None, "25:00", "nope", "22", "22:61"):
        assert parse_hhmm(bad, t) is None, f"{bad!r} must not parse"
