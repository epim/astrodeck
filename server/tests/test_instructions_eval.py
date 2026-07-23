import time

from astrodeck.sequence.models import Instruction
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
