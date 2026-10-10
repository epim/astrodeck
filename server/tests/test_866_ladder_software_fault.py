# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#866: a fault in this program's code during the resume ladder's blind
solve is not a failed plate solve.

Step 2 of the ladder (the blind solve and sync after a restart) ended in a
catch-all that worded every exception it did not name as "blind plate solve
failed after restart (...)". On the #850 branch a stub hub missing the new
``refusal_level`` keyword raised ``TypeError`` and 54 tests held in those
words; the UI's humanizer then rewrote any line holding "plate" and "solve"
into "Plate-solve failed - check focus/exposure", so an operator would have
been sent to the optics for a code bug (#792 narrowed that rule to a bare
failed-solve line).

Now ``TypeError``, ``AttributeError``, ``NameError`` (with
``UnboundLocalError``) and ``KeyError`` out of the blind solve hold in fixed
words of their own (``SOLVE_SOFTWARE_FAULT_WORDS``), and a warning beside the
hold names the type and where it was raised, as ``basename:function:line``
frames with no absolute path and no message text, said once a night per
session; the traceback goes to stderr through the module logger. Solver,
camera and device failures keep the failed-solve words. A humanizer key that
can still pair in a frame or type name ("nina", "camera") is broken by a
hyphen, so the UI never swaps the warning for its own sentence; "plate" and
"guid" are no longer keys (#961) and a frame in plate.py or guider.py is named
whole.

Each test names the mutant of resume_arm.py it was shown red under; the
mutants were applied to a byte copy of resume_arm.py and the file was
restored from that copy (sha256 checked).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_850_resume_sync_refused import (  # noqa: F401 (fixtures too)
    _CUT, _Hub, _arm, _held_line, _humanizer_rewrites, _isolated_sessions,
    _session, fp)
from test_resume_arm_no_light_backoff import (  # tick-level harness
    T0, _Rig, _held)
from test_resume_arm_no_light_backoff import _arm as _arm_session
from test_resume_arm_nothing_tonight_latch import T0 as _LATCH_T0
from test_resume_arm_nothing_tonight_latch import _armed as _latch_armed
from test_resume_arm_nothing_tonight_latch import night  # noqa: F401

import astrodeck.sequence.resume_arm as ra
from astrodeck.devices.base import DeviceError
from astrodeck.events import night_key
from astrodeck.sequence.session import session_store

_LOGGER = "astrodeck.sequence.resume_arm"


def _no_absolute_path(text: str) -> None:
    """No drive-letter path, no working directory, no server root."""
    assert not re.search(r"[A-Za-z]:[\\/]", text), text
    assert os.getcwd() not in text, text
    assert str(Path(ra.__file__).resolve().parents[2]) not in text, text


class _HubWithoutTheKeyword(_Hub):
    """The #850 incident exactly: a hub whose ``solve_and_sync`` predates
    ``refusal_level``. Python raises the TypeError at the call, before the
    body runs."""

    async def solve_and_sync(self, exposure_s: float = 3.0):
        self.calls.append("solve")      # pragma: no cover - never entered
        return {"ok": True}


def _fault_warnings(bus_lines) -> list[str]:
    return [m for lvl, m, _ in bus_lines
            if lvl == "warning" and "software fault in the blind solve (" in m]


async def test_a_hub_missing_the_keyword_is_a_software_fault(
        monkeypatch, bus_lines, caplog):
    """A REAL TypeError raised by Python (an unexpected keyword argument),
    not a hand-built one: held in the software-fault words, not the
    failed-solve words; nothing slews; light is not judged; the warning names
    the type and the call line in ``_recover`` (the innermost frame, since
    the callee was never entered), by basename only; the traceback goes to
    the module logger at ERROR, never to the bus.

    NAMED MUTANT M866a "no software-fault arm" (the ``except
    _SOFTWARE_FAULTS`` arm deleted): RED, the reason starts "blind plate
    solve failed after restart".
    NAMED MUTANT M866b "no traceback to stderr" (the ``_log.error(...)``
    line deleted): RED on the caplog assertion.
    NAMED MUTANT M866h "frame summary dropped" (``: {_fault_site(e)}``
    removed from the warning): RED on "resume_arm.py:_recover:".
    NAMED MUTANT M866i "absolute filename" (``os.path.basename(f.filename)``
    read as ``f.filename`` in ``_fault_site``): RED on the slash and path
    assertions.
    """
    caplog.set_level(logging.ERROR, logger=_LOGGER)
    hub = _HubWithoutTheKeyword(centring={"centered": True,
                                          "error_arcmin": 0.3, "attempts": 1})
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())

    assert reason == ra.SOLVE_SOFTWARE_FAULT_WORDS, reason
    assert "plate" not in reason
    assert hub.calls == [], "nothing may move, and the callee never ran"
    assert arm._recentred is None
    assert arm._ladder_light is None, "a fault is no evidence of light"
    said = arm._ladder_software_fault
    assert said is not None
    assert "(TypeError)" in said, said
    assert "resume_arm.py:_recover:" in said, said
    assert "/" not in said and "\\" not in said, said
    _no_absolute_path(said)
    for _, m, _ in bus_lines:
        _no_absolute_path(m)
        assert "Traceback" not in m, m
    records = [r for r in caplog.records
               if r.name == _LOGGER and r.levelno == logging.ERROR]
    assert len(records) == 1, caplog.records
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is TypeError


@pytest.mark.parametrize("cls", [AttributeError, NameError, KeyError,
                                 UnboundLocalError],
                         ids=lambda c: c.__name__)
async def test_each_software_fault_type_holds_in_the_same_words(
        monkeypatch, bus_lines, cls):
    """Every type in the set holds in the same words; the warning names the
    type, and its innermost frame is the double's raise, which proves the
    frames reach past the catcher into the code that raised.

    NAMED MUTANT M866d "the set cut to TypeError" (``_SOFTWARE_FAULTS =
    (TypeError,)``): RED on all four.
    """
    hub = _Hub(solve_raises=cls("x"),
               centring={"centered": True, "error_arcmin": 0.3, "attempts": 1})
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.SOLVE_SOFTWARE_FAULT_WORDS, reason
    assert hub.calls == ["solve"]
    said = arm._ladder_software_fault
    assert said is not None
    assert f"({cls.__name__})" in said, said
    assert "test_850_resume_sync_refused.py:solve_and_sync:" in said, said
    _no_absolute_path(said)


@pytest.mark.parametrize("exc", [
    RuntimeError("Not enough stars"),
    DeviceError("the exposure did not complete"),
], ids=["runtime", "device"])
async def test_a_solver_or_device_failure_keeps_the_failed_solve_words(
        monkeypatch, bus_lines, exc):
    """Controls: a solver's or a camera's failure is still a failed solve,
    in today's words, with no software-fault warning.

    NAMED MUTANT M866e "every exception is a software fault"
    (``_SOFTWARE_FAULTS = (Exception,)``): RED on both.
    """
    hub = _Hub(solve_raises=exc,
               centring={"centered": True, "error_arcmin": 0.3, "attempts": 1})
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason.startswith("blind plate solve failed after restart"), reason
    assert arm._ladder_software_fault is None
    assert hub.calls == ["solve"]
    assert not any("software fault" in m for _, m, _ in bus_lines), bus_lines


async def test_the_software_fault_hold_is_fixed_words(monkeypatch, bus_lines):
    """No digit; the UI shows our words, on their own and inside the
    "auto-resume held: ..." line; the cause and the action sit inside the
    137-char cut; and two different fault types give the identical hold,
    so the hold keeps its ``since`` across retries.

    NAMED MUTANT M866f "plate words" (the hold reworded "blind plate solve
    hit a software fault ..."): RED on the humanizer assertion.
    NAMED MUTANT M866g "type name in the hold" (the arm returns
    ``f"{SOLVE_SOFTWARE_FAULT_WORDS} ({type(e).__name__})"``): RED on the
    identical-hold assertion.
    """
    words = ra.SOLVE_SOFTWARE_FAULT_WORDS
    assert not re.search(r"\d", words), words
    for text in (words, _held_line(words), "auto-resume held: " + words):
        assert not _humanizer_rewrites(text), text
    head = _held_line(words)[:_CUT]
    for part in ("software fault", "not slewing", "report it as a bug"):
        assert part in head, (part, head)

    reasons = []
    for exc in (TypeError("x"), KeyError("x")):
        hub = _Hub(solve_raises=exc, centring={"centered": True,
                                               "error_arcmin": 0.3,
                                               "attempts": 1})
        reasons.append(await _arm(hub, monkeypatch)._recover(_session()))
    assert reasons[0] == reasons[1] == words, reasons


async def test_a_software_fault_is_said_once_a_night_and_retried_quietly(
        sim_hub, monkeypatch, bus_lines):
    """Through ``tick``: three ladders, each hitting the same KeyError. One
    fault warning and one held line for the night, the retries silent but
    still made every ten minutes, and the hold standing with its ``since``.

    NAMED MUTANT M866j "said every retry" (``if self._software_fault_said
    != said:`` made ``if True:``): RED, three of each warning.
    """
    _arm_session()
    rig = _Rig(sim_hub, monkeypatch, [KeyError(f"x{i}") for i in range(3)])
    await rig.tick_at(T0)
    first = dict(rig.arm.hold)
    assert first["reason"] == ra.SOLVE_SOFTWARE_FAULT_WORDS, first
    await rig.tick_at(T0 + ra.RETRY_INTERVAL_S)
    assert rig.arm.hold["reason"] == ra.SOLVE_SOFTWARE_FAULT_WORDS
    await rig.tick_at(T0 + 2 * ra.RETRY_INTERVAL_S)
    third = rig.arm.hold
    assert rig.solves == 3
    assert third["reason"] == ra.SOLVE_SOFTWARE_FAULT_WORDS, third
    assert third["since"] == first["since"], (first, third)
    assert rig.arm._retry_at == T0 + 3 * ra.RETRY_INTERVAL_S

    faults = _fault_warnings(bus_lines)
    assert len(faults) == 1, faults
    assert "(KeyError)" in faults[0], faults
    assert "test_resume_arm_no_light_backoff.py:solve:" in faults[0], faults
    _no_absolute_path(faults[0])
    held = _held(bus_lines)
    assert len(held) == 1, held
    assert held[0].endswith("without a word, until the night ends"), held
    assert "report it as a bug" in held[0][:_CUT], held


async def test_the_software_fault_is_said_again_on_a_new_night_and_after_a_rearm(
        sim_hub, monkeypatch, bus_lines):
    """The once-a-night latch is keyed on the session AND the night, and a
    disarm clears it: tomorrow's reader and a re-arming operator each hear
    the fault.

    NAMED MUTANT M866k "latch not keyed on the night" (``said = armed.id``):
    RED on the night half.
    NAMED MUTANT M866l "disarm does not clear it" (the new
    ``self._software_fault_said = None`` in ``tick``'s nothing-armed branch
    deleted): RED on the re-arm half.
    """
    # The night half.
    s = _arm_session()
    rig = _Rig(sim_hub, monkeypatch, [KeyError(f"x{i}") for i in range(4)])
    tomorrow = T0 + 24 * 3600
    assert night_key(T0) != night_key(tomorrow), "premise: two nights"
    await rig.tick_at(T0)
    rig.window["open"] = False
    await rig.tick_at(T0 + 8 * 3600)
    rig.window["open"] = True
    await rig.tick_at(tomorrow)
    assert rig.solves == 2
    assert len(_fault_warnings(bus_lines)) == 2, _fault_warnings(bus_lines)

    # The re-arm half, on the same night as the second fault.
    s.auto_resume = False
    session_store.save(s)
    await rig.tick_at(tomorrow + 60)
    s.auto_resume = True
    session_store.save(s)
    await rig.tick_at(tomorrow + 120)
    assert rig.solves == 3, "premise: the disarm cleared the backoff"
    assert len(_fault_warnings(bus_lines)) == 3, _fault_warnings(bus_lines)


async def test_a_later_hold_for_another_cause_is_said_in_its_own_words(
        sim_hub, monkeypatch, bus_lines):
    """Through ``tick``: a software fault, then on the next retry a failed
    solve (the sky this time). The second hold is the failed-solve hold, said
    in its own "auto-resume held: ... retrying in 10 min" line: the first
    ladder's fault does not ride into the second ladder, where the
    once-a-night latch would silence it.

    NAMED MUTANT M866m "the per-ladder reset gone" (``tick``'s new
    ``self._ladder_software_fault = None`` before each ladder read as
    ``pass``): RED, the second hold is silent (one held line, not two).
    """
    _arm_session()
    rig = _Rig(sim_hub, monkeypatch,
               [KeyError("x"), RuntimeError("Not enough stars")])
    await rig.tick_at(T0)
    assert rig.arm.hold["reason"] == ra.SOLVE_SOFTWARE_FAULT_WORDS
    await rig.tick_at(T0 + ra.RETRY_INTERVAL_S)
    assert rig.solves == 2
    second = rig.arm.hold["reason"]
    assert second.startswith("blind plate solve failed after restart"), second
    held = _held(bus_lines)
    assert len(held) == 2, held
    assert held[1].startswith("auto-resume held: blind plate solve failed"), (
        held)
    assert held[1].endswith(
        f"retrying in {int(ra.RETRY_INTERVAL_S / 60)} min"), held
    assert len(_fault_warnings(bus_lines)) == 1, _fault_warnings(bus_lines)


async def test_a_start_between_two_faults_makes_the_second_news(
        night, sim_hub, monkeypatch):
    """The once-a-night latch is cleared by a start, as the #284 latch is: a
    fault is said; the next retry re-centres and starts the session; that run
    ends and the session is dormant and armed again; a fault on the same
    night after it is a new fact, and is said again.

    NAMED MUTANT M866n "the latch outlives a start" (the new
    ``self._software_fault_said = None`` after a start read as ``pass``):
    RED, one fault warning, not two.
    """
    async def centred(*a, **kw):
        night.gotos.append(a)
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                "rotation": None}

    s = _latch_armed()
    night.script.append(KeyError("a"))
    await night.tick_at(_LATCH_T0)
    assert len(_fault_warnings(night.lines)) == 1, night.lines
    failing_goto = sim_hub.goto_and_center
    monkeypatch.setattr(sim_hub, "goto_and_center", centred)
    await night.tick_at(_LATCH_T0 + ra.RETRY_INTERVAL_S)
    assert night.starts == [s.id], "premise: the retry started the session"
    monkeypatch.setattr(sim_hub, "goto_and_center", failing_goto)
    night.script.append(KeyError("b"))
    later = _LATCH_T0 + 2 * ra.RETRY_INTERVAL_S
    assert night_key(later) == night_key(_LATCH_T0), "premise: one night"
    await night.tick_at(later)
    assert len(night.solves) == 3, night.solves
    faults = _fault_warnings(night.lines)
    assert len(faults) == 2, faults


#: (module file, function name, the file as the warning names it, the file as
#: it is) for names that hold a humanizer key, each beside a partner the
#: warning already carries: "Error" (the type) for nina; "timeout" for camera.
#: The last two rows are names that held a key until #961 and are left whole
#: now: the plate-solve and guiding rules read a whole report, which this
#: warning is not.
_TRIPPING_FRAMES = [
    ("nina.py", "expose", "ni-na.py", "nina.py"),
    ("camera.py", "timeout_read", "ca-mera.py", "camera.py"),
    ("plate.py", "read_result", "plate.py", "plate.py"),
    ("guider.py", "lost_star", "guider.py", "guider.py"),
]


def _raiser_in(module_file: str, func: str):
    """A function compiled as if it lived in ``module_file``, whose raise
    sits on line 15 (a 5 in the line number), so a real traceback frame
    names that file and function."""
    src = "\n" * 13 + f"def {func}():\n    raise AttributeError('x')\n"
    space: dict = {}
    exec(compile(src, module_file, "exec"), space)
    return space[func]


@pytest.mark.parametrize("module_file,func,broken,whole", _TRIPPING_FRAMES,
                         ids=["nina", "camera", "plate", "guid"])
async def test_the_fault_warning_never_trips_the_humanizer(
        monkeypatch, bus_lines, module_file, func, broken, whole):
    """A fault raised from a module whose name holds a humanizer key (a NINA
    rig's nina.py, a camera module) reaches the operator in our words: the key
    is broken by a hyphen, so the UI does not replace the warning with "NINA
    reported an error" or its like. Premise in the same test: with the key
    whole, the line WOULD be rewritten. A plate module and the guider hold no
    key now (#961): their frames are named whole, as the files are called.

    NAMED MUTANT M866o "keys left whole" (``_unpaired`` returns ``text``
    unchanged): RED on nina and camera. NAMED MUTANT M961 "plate and guid
    broken again" (``_HUMANIZER_KEYS`` back to ``nina|camera|plate|guid``): RED
    on plate and guid.
    """
    raiser = _raiser_in(module_file, func)

    class _FaultingHub(_Hub):
        async def solve_and_sync(self, exposure_s: float = 3.0, **kw):
            self.calls.append("solve")
            raiser()

    hub = _FaultingHub(centring={"centered": True, "error_arcmin": 0.3,
                                 "attempts": 1})
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.SOLVE_SOFTWARE_FAULT_WORDS, reason
    said = arm._ladder_software_fault
    assert said is not None
    assert f"{broken}:{func}:15" in said, said
    if broken != whole:
        assert _humanizer_rewrites(said.replace(broken, whole)), (
            "premise: with the key whole the line trips the humanizer", said)
        assert not _humanizer_rewrites(said), said
    _no_absolute_path(said)
