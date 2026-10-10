# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#979 and #964: `SequenceEngine` says what its best-effort guards swallow.

#979. ``start()`` armed its session through ``_arm_exclusively`` inside
``try: ... except Exception: pass`` ("never block a run over bookkeeping"). The
loop disarms every OTHER armed session and saves each one; a save that raised
partway (a full disk, a file another process held open on Windows) left the
sessions after it armed, the new session armed beside them, ``disarmed`` empty,
and not one line anywhere. The singleton the owner relies on (#595, D-04) was
broken without a trace, and two armed sessions can race for the same restart.

The start still goes ahead (a refused start loses the night over a flag), but
it now says so: one warning naming the exception TYPE (never its text, which
quotes a path), the sessions still armed beside the new one, read back from the
store, and the ones disarmed before the loop stopped, which ``start()`` hands
back as ``disarmed`` like the loop's own list. The shared loop still RAISES for
its other two callers (the PATCH route answers an error; the promotion leaves
the session queued and unarmed, which is the safe direction).

#964. The same file held 15 more ``except Exception: pass`` handlers, the shape
#811 found on the dispatcher's timer and #936 on the per-frame deadman. The ones
on a path whose failure matters now hand what they caught to
``SequenceEngine._say_swallowed``: one warning per (step, exception type) per
run, the type's name and never its text. The rest stay a ``pass`` with a reason
beside them, and the last case here is the ratchet that keeps the list honest.

The cases are RED on the code before the fix (each said nothing, or the start
raised nothing and named nothing); the mutants run against it are in
``scratchpad/w21/WP-190-report.md``.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import re
import time
from pathlib import Path

import pytest

import astrodeck.sequence.engine as engine_mod
from _simhub import sim_hub  # noqa: F401  (fixture import)
from astrodeck.devices.base import DeviceError
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, session_store

#: Text no line may carry: what a store error or a device reply could quote.
_TEXT = "SECRET-PATH-C:/private/place.json"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The session store, isolated by moving CAPTURE_DIR (see
    test_resume_arm_on_resume.py's fixture of the same name)."""
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    assert session_store.load_all() == [], "the store is not isolated"
    return session_store


def _plan(name: str = "NGC 6946 Fireworks - LRGB+Ha cycle") -> SequencePlan:
    return SequencePlan(
        name=name, guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="NGC 6946", ra_hours=20.34, dec_deg=60.15,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])


def _armed_session(store, name: str, n: int) -> Session:
    s = Session(name=name, created_ts=time.time() + n, status="dormant",
                plan=_plan(name), auto_resume=True)
    store.save(s)
    return s


def _warnings(bus_lines, *needles: str) -> list[str]:
    return [m for lvl, m, src in bus_lines
            if lvl == "warning" and src == "sequence"
            and all(n in m for n in needles)]


# ----------------------------------------------------------------------- #979

def _three_armed(store):
    return [_armed_session(store, f"night {n}", n) for n in (1, 2, 3)]


def _save_fails_on_the_second_disarm(monkeypatch, store, ids: set[str]):
    """``session_store.save`` raising for the SECOND of the earlier sessions the
    loop saves, whichever order the store lists them in. Returns the ids the
    loop tried, in order."""
    real = store.save
    tried: list[str] = []

    def save(s, *a, **k):
        if s.id in ids:
            tried.append(s.id)
            if len(tried) == 2:
                raise OSError(28, f"disk full at {_TEXT}")
        return real(s, *a, **k)

    monkeypatch.setattr(store, "save", save)
    return tried


async def test_a_failed_disarm_is_said_and_the_run_still_starts(
        store, sim_hub, monkeypatch, bus_lines):
    """THE DEFECT: a save that raised on the second session left the third
    armed and the new session armed beside them, and ``start`` swallowed it.
    Now: the start goes ahead, the return names what WAS disarmed, and one
    warning names the type, the sessions still armed, and the one disarmed."""
    earlier = _three_armed(store)
    ids = {s.id for s in earlier}
    tried = _save_fails_on_the_second_disarm(monkeypatch, store, ids)
    names = {s.id: s.name for s in earlier}

    engine = SequenceEngine(sim_hub)
    disarmed = engine.start(_plan())            # must not raise
    try:
        assert engine.running, "the failed disarm must not refuse the start"
        first, second = tried[0], tried[1]
        assert [d["id"] for d in disarmed] == [first], (
            "the return must name the session the loop did disarm", disarmed)
        said = _warnings(bus_lines, "stopped partway")
        assert len(said) == 1, (
            "a failed disarm must be said once; the log held", bus_lines)
        line = said[0]
        assert "OSError" in line, line
        assert _TEXT not in line and "disk full" not in line, (
            "the line carried the exception's text, not its type", line)
        still = ids - {first}
        for sid in still:
            assert names[sid] in line, (
                f"'{names[sid]}' is still armed and the line does not name "
                f"it: {line}")
        assert "disarmed before it stopped: " + names[first] in line, line

        armed = {s.id for s in store.load_all() if s.auto_resume}
        assert armed == still | {engine._session.id}, (
            "premise: the failed save left the later sessions armed", armed)
    finally:
        await engine.abort()


async def test_a_store_that_cannot_be_read_back_says_so(
        store, sim_hub, monkeypatch, bus_lines):
    """The loop's first read raising names nobody: the line says the store
    could not be read back, instead of claiming nothing is armed."""
    _three_armed(store)
    real = store.load_all
    calls = {"n": 0}

    def load_all():
        # Only the singleton's own two reads fail (the loop's, then the
        # warning's read-back); anything else start does reads normally.
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError(5, f"unreadable {_TEXT}")
        return real()

    monkeypatch.setattr(store, "load_all", load_all)
    engine = SequenceEngine(sim_hub)
    disarmed = engine.start(_plan())
    try:
        assert engine.running
        assert disarmed == []
        said = _warnings(bus_lines, "stopped partway")
        assert len(said) == 1, bus_lines
        assert "OSError" in said[0], said
        assert "could not be read back" in said[0], said
        assert _TEXT not in said[0], said
    finally:
        await engine.abort()


async def test_a_clean_start_says_nothing_about_a_partway_stop(
        store, sim_hub, bus_lines):
    """CONTROL: with every save working, the new line never appears and the
    existing 'disarmed auto-resume for' warning still does."""
    earlier = _three_armed(store)
    engine = SequenceEngine(sim_hub)
    disarmed = engine.start(_plan())
    try:
        assert {d["id"] for d in disarmed} == {s.id for s in earlier}
        assert not _warnings(bus_lines, "stopped partway"), bus_lines
        assert _warnings(bus_lines, "disarmed auto-resume for"), bus_lines
    finally:
        await engine.abort()


def test_the_shared_loop_still_raises_for_its_other_callers(
        store, monkeypatch):
    """The PATCH route answers an error and the promotion leaves the session
    queued and unarmed because the loop RAISES. Swallowing in the loop, instead
    of in ``start``, would turn both into a quiet partial success."""
    earlier = _three_armed(store)
    _save_fails_on_the_second_disarm(monkeypatch, store,
                                     {s.id for s in earlier})
    mine = Session(name="mine", created_ts=time.time() + 9, status="dormant",
                   plan=_plan("mine"))
    with pytest.raises(OSError):
        SequenceEngine._arm_exclusively(mine)


async def test_a_failed_disarm_on_a_resume_does_not_name_the_resumed_session(
        store, sim_hub, monkeypatch, bus_lines):
    """THE 2 AM SHAPE: the session being started is a RESUME, so it is already
    armed and dormant on disk when the loop fails. The read-back of 'still
    armed' must leave it out, or the warning names the session being armed as
    a second armed session beside itself, and an owner told that two sessions
    can race goes and disarms the one that is running."""
    resumed = _armed_session(store, "RESUMED-NIGHT", 1)
    other = _armed_session(store, "other-night", 2)
    real = store.save

    def save(s, *a, **k):
        if s.id == other.id:
            raise OSError(28, f"disk full at {_TEXT}")
        return real(s, *a, **k)

    monkeypatch.setattr(store, "save", save)
    engine = SequenceEngine(sim_hub)
    engine.start(resumed.plan, session=resumed)     # must not raise
    try:
        assert engine.running
        said = _warnings(bus_lines, "stopped partway")
        assert len(said) == 1, bus_lines
        line = said[0]
        m = re.search(r"still armed for: (.*?)(?:; disarmed before|\. Two armed)",
                      line)
        assert m, ("premise: the other session is still armed and named", line)
        assert m.group(1) == "other-night", (
            "the still-armed list must hold the OTHER session only", line)
    finally:
        await engine.abort()


def test_a_bus_that_cannot_take_the_partway_line_does_not_stop_the_start(
        store, monkeypatch, caplog):
    """``_say_singleton_failed`` runs on the way into a run and never raises:
    its closing line falls back to the logger when the bus is what failed, as
    ``_say_swallowed`` does. The old handler was a ``pass`` and could not
    raise; a bus that did would otherwise have turned a bookkeeping failure
    into a refused start."""
    def broken(*a, **k):
        raise RuntimeError("the bus is down")

    monkeypatch.setattr(engine_mod.bus, "log", broken)
    mine = Session(name="mine", created_ts=time.time(), status="dormant",
                   plan=_plan("mine"))
    exc = OSError(28, "disk full")
    exc.disarmed_so_far = [{"id": "x", "name": "night 1"}]
    with caplog.at_level(logging.WARNING, logger=engine_mod.__name__):
        done = SequenceEngine._say_singleton_failed(mine, exc)
    assert done == [{"id": "x", "name": "night 1"}], done
    lines = [r.getMessage() for r in caplog.records
             if "stopped partway" in r.getMessage()]
    assert len(lines) == 1, caplog.records
    assert "OSError" in lines[0] and "disk full" not in lines[0], lines


# ----------------------------------------------------------------------- #964

class _Guider:
    """A connected guider whose calls raise what the case asks for."""

    connected = True

    def __init__(self, *, stop=None, stats=None):
        self._stop, self._stats = stop, stats
        self.stops = 0

    async def stop_guiding(self):
        self.stops += 1
        if self._stop is not None:
            raise self._stop

    async def is_active(self):
        return True

    def stats(self):
        if self._stats is not None:
            raise self._stats
        raise AssertionError("stats read in a case that did not ask for it")


def _boom(label: str = "RuntimeError") -> Exception:
    return {"RuntimeError": RuntimeError, "KeyError": KeyError,
            "TimeoutError": TimeoutError}[label](f"boom {_TEXT}")


class _BrokenReporter:
    id = "night"

    def record_safety(self, reason, action):
        raise RuntimeError(f"report broke {_TEXT}")

    def record_sky_angle(self, **kw):
        raise RuntimeError(f"report broke {_TEXT}")


class _BrokenSunWatch:
    def note_parked(self):
        raise RuntimeError(f"sun watch broke {_TEXT}")


# Each case provokes ONE swallowed failure and returns what the guarded call
# returned. They take the engine, the sim hub and monkeypatch.

async def _record_safety(eng, hub, mp):
    eng.reporter = _BrokenReporter()
    return eng._record_safety("wind", "pause")


async def _record_sky_angle(eng, hub, mp):
    eng.reporter = _BrokenReporter()
    rec = {"exposed_at": 10.0, "pa_deg": 12.0, "pier_side": "east",
           "mechanical_deg": 3.0, "source": "plate solve"}
    return eng._record_sky_angle(Target(name="t", ra_hours=1.0, dec_deg=2.0,
                                        steps=[]), since=0.0,
                                 commanded=None, result=None, rec=rec)


async def _mark_position_unknown(eng, hub, mp):
    def mark(reason):
        raise RuntimeError(f"mark broke {_TEXT}")
    mp.setattr(hub.devices["telescope"], "mark_position_unknown", mark,
               raising=False)
    return eng._mark_rig_position_unknown()


async def _sun_watch_roof_close(eng, hub, mp):
    mp.setattr(hub, "sun_watch", _BrokenSunWatch(), raising=False)
    return await eng._fenced_park()


async def _sun_watch_wind_down(eng, hub, mp):
    mp.setattr(hub, "sun_watch", _BrokenSunWatch(), raising=False)
    return await eng._wind_down_park_and_close(True, False)


async def _stand_down_guider(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stop=_boom()), raising=False)
    return await eng._stand_down_guider()


async def _park_hold(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stop=_boom()), raising=False)
    return await eng._park_hold()


async def _stop_tracking(eng, hub, mp):
    async def set_tracking(on):
        raise _boom()
    mp.setattr(hub.devices["telescope"], "set_tracking", set_tracking)
    return await eng._stop_tracking_quietly()


async def _guide_rms(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stats=_boom()), raising=False)
    return eng._guide_rms()


async def _guide_rms_floor(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stats=_boom()), raising=False)
    mp.setattr(eng, "_guide_rms", lambda: None)
    return eng._guide_rms_judged()


async def _ledger_guide_rms(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stats=_boom()), raising=False)
    plan = _plan()
    eng._plan = plan
    eng._session = Session(name="s", created_ts=time.time(), plan=plan)
    target = plan.targets[0]
    return eng._record_session_frame(target, target.steps[0], {},
                                     auto_accepted=True)


async def _limit_recovery_guider(eng, hub, mp):
    g = _Guider(stop=_boom())
    mp.setattr(hub, "guider", g, raising=False)
    tel = hub.devices["telescope"]

    async def park():
        raise DeviceError("stop the case here")     # past the guider's stop
    mp.setattr(tel, "park", park)
    target = Target(name="t", ra_hours=1.0, dec_deg=2.0, steps=[])
    try:
        await eng._do_tracking_recovery(tel, target, report_centring=False)
    except DeviceError:
        pass


async def _focus_temperature(eng, hub, mp):
    foc = hub.devices["focuser"]

    async def get_temperature():
        raise _boom()
    mp.setattr(foc, "get_temperature", get_temperature)
    return await eng._capture_focus_temp()


async def _safe_stop_camera(eng, hub, mp):
    cam = hub.devices["camera"]

    async def abort_exposure():
        raise _boom()
    mp.setattr(cam, "abort_exposure", abort_exposure)
    return await eng._safe_stop()


async def _safe_stop_guider(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stop=_boom()), raising=False)
    return await eng._safe_stop()


async def _wind_down_guider(eng, hub, mp):
    mp.setattr(hub, "guider", _Guider(stop=_boom()), raising=False)
    return await eng._stop_guiding_quietly()


async def _quiet_stop_read_back(eng, hub, mp):
    async def confirm(who="The run"):
        raise _boom()
    mp.setattr(eng, "_confirm_quiet_stop", confirm)
    return await eng._quiet_stop_for_unknown_position()


#: (id, case, words the line must carry, exception type it must name)
_SITES = [
    ("record_safety", _record_safety,
     ("night report could not take a safety event",), "RuntimeError"),
    ("sky_angle_row", _record_sky_angle,
     ("sky-angle row could not be added",), "RuntimeError"),
    ("position_unknown_marker", _mark_position_unknown,
     ("could not be marked position-unknown",), "RuntimeError"),
    ("sun_watch_roof_close", _sun_watch_roof_close,
     ("sun watch could not be told the roof close parked",), "RuntimeError"),
    ("sun_watch_wind_down", _sun_watch_wind_down,
     ("sun watch could not be told the wind-down parked",), "RuntimeError"),
    ("stand_down_guider", _stand_down_guider,
     ("guider could not be stood down",), "RuntimeError"),
    ("park_hold_guider", _park_hold,
     ("guider would not stop before the tracking stop",), "RuntimeError"),
    ("stop_tracking", _stop_tracking,
     ("command to stop the mount tracking did not go through",),
     "RuntimeError"),
    ("guide_rms", _guide_rms,
     ("guide RMS could not be read, so frames go ungated",), "RuntimeError"),
    ("guide_rms_floor", _guide_rms_floor,
     ("guide RMS floor could not be read",), "RuntimeError"),
    ("ledger_guide_rms", _ledger_guide_rms,
     ("guide RMS could not be read for a frame's ledger entry",),
     "RuntimeError"),
    ("limit_recovery_guider", _limit_recovery_guider,
     ("guider would not stop for the limit recovery",), "RuntimeError"),
    ("focus_temperature", _focus_temperature,
     ("focus temperature could not be re-anchored",), "RuntimeError"),
    ("safe_stop_camera", _safe_stop_camera,
     ("camera would not abort its exposure",), "RuntimeError"),
    ("safe_stop_guider", _safe_stop_guider,
     ("guider would not stop after an abort or error",), "RuntimeError"),
    ("wind_down_guider", _wind_down_guider,
     ("guider would not stop for the wind-down or the roof close",),
     "RuntimeError"),
    ("quiet_stop_read_back", _quiet_stop_read_back,
     ("read-back of the tracking stop failed",), "RuntimeError"),
]


@pytest.mark.parametrize("case,words,kind",
                         [pytest.param(c, w, k, id=i) for i, c, w, k in _SITES])
async def test_a_swallowed_failure_is_said_once_and_the_call_goes_on(
        case, words, kind, sim_hub, store, monkeypatch, bus_lines):
    """Each guarded step, made to raise: the call returns instead of raising
    (the guard still guards), the failure is said ONCE however many times it
    happens, as a warning that names the step and the exception's type and
    carries none of its text."""
    engine = SequenceEngine(sim_hub)
    await case(engine, sim_hub, monkeypatch)       # raising here is the bug
    await case(engine, sim_hub, monkeypatch)
    await case(engine, sim_hub, monkeypatch)
    said = _warnings(bus_lines, *words)
    assert len(said) == 1, (
        f"expected one warning carrying {words}; the log held", bus_lines)
    assert kind in said[0], said[0]
    assert _TEXT not in said[0] and "boom" not in said[0], (
        "the line carried the exception's text, not its type", said[0])


#: The whole line, for the sites whose words the fix round (review of WP-190)
#: had to make true of EVERY caller. Several sit on a terminal or no-run path
#: (an abort or error stop, the position-unknown stop, the wind-down), and the
#: warning reaches the alert sinks, so a line that ended "the run goes on" told
#: an owner reading it during an abort that the run was still going. The line
#: states what failed and its type and promises nothing about the run; an
#: exact match fails on ANY suffix, which a words-and-type check cannot.
_WHOLE_LINES = {
    "safe_stop_camera": ("the camera would not abort its exposure after an "
                         "abort or error (RuntimeError)"),
    "safe_stop_guider": ("the guider would not stop after an abort or error "
                         "(RuntimeError)"),
    "position_unknown_marker": ("the mount could not be marked "
                                "position-unknown after the stop "
                                "(RuntimeError)"),
    "quiet_stop_read_back": ("the read-back of the tracking stop failed, so "
                             "it is not known to have taken (RuntimeError)"),
    "sun_watch_wind_down": ("the sun watch could not be told the wind-down "
                            "parked the mount (RuntimeError)"),
    "wind_down_guider": ("the guider would not stop for the wind-down or the "
                         "roof close (RuntimeError)"),
    # Its callers include the pause, the weather hold and the
    # position-unknown stop, none of which moves the mount.
    "stand_down_guider": "the guider could not be stood down (RuntimeError)",
    "park_hold_guider": ("the guider would not stop before the tracking stop "
                         "(RuntimeError)"),
}


@pytest.mark.parametrize(
    "case,words,line",
    [pytest.param(c, w, _WHOLE_LINES[i], id=i)
     for i, c, w, _k in _SITES if i in _WHOLE_LINES])
async def test_the_line_says_what_failed_and_promises_nothing_about_the_run(
        case, words, line, sim_hub, store, monkeypatch, bus_lines):
    engine = SequenceEngine(sim_hub)
    await case(engine, sim_hub, monkeypatch)
    said = _warnings(bus_lines, *words)
    assert len(said) == 1, bus_lines
    assert said[0] == line, said[0]


def test_the_whole_line_table_names_real_sites():
    """The table above names real sites: a renamed id would drop its case
    from the parametrisation above without a failure."""
    assert set(_WHOLE_LINES) <= {i for i, _c, _w, _k in _SITES}


async def test_the_status_without_a_finish_time_is_said_once(
        sim_hub, store, monkeypatch, bus_lines):
    """The progress payload's ETA: ``compute_eta`` raising used to leave a live
    run's status with no finish time and no line saying why."""
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    try:
        def boom():
            raise ZeroDivisionError(f"eta broke {_TEXT}")
        monkeypatch.setattr(engine, "compute_eta", boom)
        for _ in range(3):
            engine._set_state()
        said = _warnings(bus_lines, "finish-time estimate")
        assert len(said) == 1, bus_lines
        assert "ZeroDivisionError" in said[0] and _TEXT not in said[0], said
    finally:
        await engine.abort()


async def test_a_different_exception_type_is_news(sim_hub, bus_lines):
    """The key is (step, type): the same step raising something else says
    itself again, and the same type again does not."""
    engine = SequenceEngine(sim_hub)
    for exc in (RuntimeError("a"), RuntimeError("b"), KeyError("c"),
                KeyError("d")):
        engine._say_swallowed("a step failed", exc)
    said = _warnings(bus_lines, "a step failed")
    assert len(said) == 2, said
    assert "RuntimeError" in said[0] and "KeyError" in said[1], said


async def test_a_new_run_says_it_afresh(sim_hub, store, monkeypatch,
                                        bus_lines):
    """Once per RUN, not once per process: a failure that comes back on
    tomorrow's night is news again, so ``start`` forgets what was said."""
    engine = SequenceEngine(sim_hub)
    engine.reporter = _BrokenReporter()
    engine._record_safety("wind", "pause")
    engine._record_safety("wind", "pause")
    assert len(_warnings(bus_lines, "safety event")) == 1, bus_lines
    engine.start(_plan())
    await engine.abort()
    engine.reporter = _BrokenReporter()
    engine._record_safety("wind", "pause")
    assert len(_warnings(bus_lines, "safety event")) == 2, bus_lines


def test_a_bus_that_cannot_take_the_line_falls_back_to_the_logger(
        monkeypatch, caplog):
    """Saying it can neither raise into the path it reports on nor repeat
    itself: the key is stamped before the line is published, and the logger
    takes the line the bus could not. A bare engine (no hub, no __init__): the
    helper reads nothing else, and a double built that way still works."""
    def broken(*a, **k):
        raise RuntimeError("the bus is down")

    monkeypatch.setattr(engine_mod.bus, "log", broken)
    engine = SequenceEngine.__new__(SequenceEngine)
    with caplog.at_level(logging.WARNING, logger=engine_mod.__name__):
        engine._say_swallowed("a step failed", ValueError("x"))
        engine._say_swallowed("a step failed", ValueError("y"))
    lines = [r.getMessage() for r in caplog.records
             if "a step failed" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "ValueError" in lines[0], lines


# ---------------------------------------------------------------- the ratchet

#: Every handler in engine.py whose whole body is ``pass``, by the function it
#: sits in: (function, exception type as written) -> (how many, why it stays
#: silent). A new one fails the ratchet below until it is said
#: (``_say_swallowed``) or listed here with a reason. Not a behaviour test: it
#: grades a property of the whole file that no single case can, which is that
#: the next best-effort guard does not go back to a bare ``pass``.
_ALLOWED_SILENT_PASS = {
    ("_unlink_saved", "OSError"): (
        1, "narrowed to what unlink can raise; the rejected frame is already "
           "recorded as rejected, and the file left behind is the whole cost"),
    ("_enforce_flip_owed", "Exception"): (
        1, "'not skippable' is the safe read, and the hold it falls to is "
           "announced at error level"),
    ("_flip_bounded", "asyncio.TimeoutError"): (
        2, "control flow: a first expiry of a shielded wait is expected, and "
           "the code after it decides (and raises _timeout_abort when it "
           "must)"),
}


def _silent_handlers(tree: ast.AST) -> dict[tuple[str, str], int]:
    """``{(function, type text): count}`` for each ``except`` whose body is
    only ``pass``."""
    out: dict[tuple[str, str], int] = {}

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            f = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func)
            if (isinstance(child, ast.ExceptHandler) and len(child.body) == 1
                    and isinstance(child.body[0], ast.Pass)):
                key = (func, ast.unparse(child.type) if child.type else "bare")
                out[key] = out.get(key, 0) + 1
            walk(child, f)

    walk(tree, "<module>")
    return out


def test_no_unexplained_silent_pass_handlers_in_the_engine():
    src = Path(engine_mod.__file__).read_text(encoding="utf-8")
    found = _silent_handlers(ast.parse(src))
    allowed = {k: n for k, (n, _why) in _ALLOWED_SILENT_PASS.items()}
    stray = {k: n - allowed.get(k, 0) for k, n in found.items()
             if n > allowed.get(k, 0)}
    assert not stray, (
        "an except handler whose whole body is `pass` in engine.py: say what "
        "it caught with self._say_swallowed(...) or list it with a reason in "
        f"_ALLOWED_SILENT_PASS: {stray}")
    gone = {k: n for k, n in allowed.items() if found.get(k, 0) < n}
    assert not gone, (
        f"listed as silent but no longer a bare pass; drop or lower the "
        f"entry: {gone}")
