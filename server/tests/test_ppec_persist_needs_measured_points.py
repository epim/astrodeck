"""#243: the native guider's stop saves a PPEC window only with enough
measured points in it, and never over a saved model that holds more.

``_persist_gp_window`` used to save any window of two or more rows. A session
that locked and then lost the star holds only dead-reckoned (dark) rows, and
its stop wrote them to ``CONFIG_DIR/guider/<profile>-gp.json`` over whatever
model the profile had trained on a good night; the next start restored it as
a fresh model. Now:

* a MEASURED row is one whose variance is not the dark-guiding variance the
  Rust engine stamps (``GP_DARK_VARIANCE``, ``handle_dark_guiding``);
* the window is saved only with at least ``GP_MIN_MEASURED_POINTS`` of them,
  the engine's own inference threshold (``n_measurements() > 10``) plus one;
* it is never saved over a file whose window holds more measured rows;
* a skipped save says why in the log, once.

Each test names the mutation of ``guide/native.py`` it was shown RED under,
run from a byte-for-byte backup and restored byte-identical afterwards, with
the observed failure quoted verbatim.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrodeck.config as configmod
import astrodeck.guide.native as nativemod
from astrodeck.guide.native import (GP_DARK_VARIANCE, GP_MIN_MEASURED_POINTS,
                                    NativeGuider)
from astrodeck.providers import NATIVE_AVAILABLE

needs_wheel = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                 reason="native wheel absent")

_GP_RS = (Path(__file__).resolve().parents[2] / "native" / "crates"
          / "astro-guide" / "src" / "algorithms" / "gaussian_process.rs")


# ------------------------------------------------ the constants are the engine's


def _rust_fn_body(src: str, name: str) -> str:
    """The body of ``fn name`` in ``src``, by brace matching."""
    m = re.search(rf"\bfn {name}\b[^{{;]*\{{", src)
    assert m, f"fn {name} is not in {_GP_RS.name}: the engine was refactored"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def test_the_python_constants_are_the_rust_engines():
    """``GP_DARK_VARIANCE`` is what ``handle_dark_guiding`` stamps, and
    ``GP_MIN_MEASURED_POINTS`` is one more than ``deduce_result_impl``'s
    inference threshold, both read from the Rust source. Each regex must
    match, so a refactored engine fails here rather than passing unread.

    MUTANT "MIN is the threshold itself" (``GP_MIN_MEASURED_POINTS =
    _GP_INFERENCE_ENGAGES_ABOVE``) -- RED, observed verbatim:

        AssertionError: GP_MIN_MEASURED_POINTS is 10; the engine infers once
        n_measurements() > 10, so a window needs 11 points

    MUTANT "the dark variance drifts" (``GP_DARK_VARIANCE = 1e3``) -- RED,
    observed verbatim:

        AssertionError: GP_DARK_VARIANCE is 1000.0; handle_dark_guiding stamps
        1e4
    """
    src = _GP_RS.read_text(encoding="utf-8")
    dark = re.search(r"\blp\.variance\s*=\s*([0-9.eE+-]+)\s*;",
                     _rust_fn_body(src, "handle_dark_guiding"))
    assert dark, "handle_dark_guiding no longer stamps lp.variance"
    assert GP_DARK_VARIANCE == float(dark.group(1)), (
        f"GP_DARK_VARIANCE is {GP_DARK_VARIANCE}; handle_dark_guiding stamps "
        f"{dark.group(1)}")
    infer = re.search(r"self\.n_measurements\(\)\s*>\s*(\d+)",
                      _rust_fn_body(src, "deduce_result_impl"))
    assert infer, "deduce_result_impl no longer gates on n_measurements()"
    above = int(infer.group(1))
    assert GP_MIN_MEASURED_POINTS == above + 1, (
        f"GP_MIN_MEASURED_POINTS is {GP_MIN_MEASURED_POINTS}; the engine "
        f"infers once n_measurements() > {above}, so a window needs "
        f"{above + 1} points")


# ------------------------------------------------ the persist, on a fake engine


class _FakeEngine:
    def __init__(self, window) -> None:
        self._window = window

    def dump_gp_window(self):
        return self._window


def _rows(measured: int, dark: int = 0, t0: float = 0.0):
    """A window of ``measured`` measured rows then ``dark`` dark ones, 5 s
    apart, in the engine's ``[t, measurement, variance, control]`` shape."""
    out = [[t0 + 5.0 * i, 0.1, 0.4, -0.02] for i in range(measured)]
    out += [[t0 + 5.0 * (measured + i), 0.0, GP_DARK_VARIANCE, 0.0]
            for i in range(dark)]
    return out


def _gp_path(tmp_path) -> Path:
    return tmp_path / "guider" / "prof1-gp.json"


def _save_file(tmp_path, window, dumped_at=1_000.0) -> str:
    """Write a saved model and return its sha256."""
    p = _gp_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"dumped_at": dumped_at, "window": window}),
                 encoding="utf-8")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha(tmp_path) -> str | None:
    p = _gp_path(tmp_path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _guider(tmp_path, monkeypatch, window, fed_at=40_000.0):
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    g = NativeGuider.__new__(NativeGuider)   # no devices needed
    g.profile_id = "prof1"
    g._engine = _FakeEngine(window)
    g._gp_fed_at = fed_at
    return g


def _why(lines) -> list[str]:
    return [m for _lvl, m, _src in lines if "PPEC model" in m
            and "not saved" in m]


@pytest.mark.parametrize("measured, dark, written", [
    (GP_MIN_MEASURED_POINTS - 1, 5, False),
    (GP_MIN_MEASURED_POINTS, 0, True),
], ids=["one_short_padded_with_dark_rows", "control_exactly_enough"])
def test_a_window_needs_enough_measured_points(tmp_path, monkeypatch,
                                               bus_lines, measured, dark,
                                               written):
    """One measured point short of the threshold, padded with dark rows past
    it, is not saved and says why once; exactly the threshold is saved.

    MUTANT "no measured-point gate" (the ``measured <
    GP_MIN_MEASURED_POINTS`` return removed) -- RED (the short window),
    observed verbatim:

        AssertionError: a window of 10 measured point(s) was saved; the
        engine infers from 11

    MUTANT "count every row as measured" (``_gp_measured_points`` returns
    ``len(window)``, for the window and the file alike) -- RED (the short
    window), observed verbatim:

        AssertionError: a window of 10 measured point(s) was saved; the
        engine infers from 11

    MUTANT "a silent skip" (the log line of the measured-point skip removed)
    -- RED (the short window), observed verbatim:

        AssertionError: the skipped save did not say why, once: []

    MUTANT "MIN off by one" (``measured <= GP_MIN_MEASURED_POINTS``) -- RED
    (the control), observed verbatim:

        AssertionError: a window of exactly 11 measured points was not saved
    """
    g = _guider(tmp_path, monkeypatch, _rows(measured, dark))
    g._persist_gp_window()
    if written:
        assert _gp_path(tmp_path).exists(), (
            f"a window of exactly {measured} measured points was not saved")
        assert not _why(bus_lines)
    else:
        assert not _gp_path(tmp_path).exists(), (
            f"a window of {measured} measured point(s) was saved; the engine "
            f"infers from {GP_MIN_MEASURED_POINTS}")
        why = _why(bus_lines)
        assert len(why) == 1, f"the skipped save did not say why, once: {why}"
        assert f"{measured} measured" in why[0], why[0]


def test_fewer_measured_points_than_the_file_leave_it_untouched(
        tmp_path, monkeypatch, bus_lines):
    """The saved model has 20 measured rows. This session's window has 12
    measured (enough to be saved on its own) and 10 dark, 22 rows in all: it
    must not replace the file, which stays byte-identical, and the skip says
    why once.

    MUTANT "overwrite regardless of the file" (the ``saved > measured``
    return removed) -- RED, observed verbatim:

        AssertionError: a window of 12 measured points replaced a saved model
        of 20

    MUTANT "count every row as measured" -- RED, observed verbatim:

        AssertionError: a window of 12 measured points replaced a saved model
        of 20
    """
    before = _save_file(tmp_path, _rows(20))
    g = _guider(tmp_path, monkeypatch, _rows(12, 10))
    g._persist_gp_window()
    assert _sha(tmp_path) == before, (
        "a window of 12 measured points replaced a saved model of 20")
    why = _why(bus_lines)
    assert len(why) == 1, f"the skipped save did not say why, once: {why}"
    assert "12 measured" in why[0] and "20" in why[0], why[0]


@pytest.mark.parametrize("fed_at", [40_000.0, None],
                         ids=["fed_this_session", "never_fed"])
def test_more_measured_points_than_the_file_are_saved_under_the_stamp_rule(
        tmp_path, monkeypatch, bus_lines, fed_at):
    """The saved model has 12 measured rows and 30 dark ones (42 rows). This
    session's window has 20 measured and 3 dark: it is saved, whole, stamped
    under the H2 rule (#210): the feed time when the session fed the model,
    the write's own clock only when nothing did.

    MUTANT "count every row as measured" -- RED, observed verbatim (the file
    counts 42 rows, the window 23):

        AssertionError: a window of 20 measured points did not replace a saved
        model of 12
    """
    monkeypatch.setattr(nativemod, "time", _WallClock(50_000.0))
    _save_file(tmp_path, _rows(12, 30))
    window = _rows(20, 3, t0=900.0)
    g = _guider(tmp_path, monkeypatch, window, fed_at=fed_at)
    g._persist_gp_window()
    saved = json.loads(_gp_path(tmp_path).read_text(encoding="utf-8"))
    assert saved["window"] == window, (
        "a window of 20 measured points did not replace a saved model of 12")
    assert saved["dumped_at"] == (50_000.0 if fed_at is None else fed_at)
    assert not _why(bus_lines)


def test_control_as_many_measured_points_as_the_file_are_saved(
        tmp_path, monkeypatch, bus_lines):
    """CONTROL: a window with as many measured rows as the saved model is
    the newer model, and is saved. (A restored model stopped before it is fed
    again re-saves its own window and its own stamp this way.)

    MUTANT "overwrite only a file with fewer" (``saved >= measured``) -- RED,
    observed verbatim:

        AssertionError: a window as well measured as the saved model did not
        replace it
    """
    _save_file(tmp_path, _rows(15), dumped_at=1_000.0)
    window = _rows(15, t0=500.0)
    g = _guider(tmp_path, monkeypatch, window)
    g._persist_gp_window()
    saved = json.loads(_gp_path(tmp_path).read_text(encoding="utf-8"))
    assert saved["window"] == window, (
        "a window as well measured as the saved model did not replace it")


@pytest.mark.parametrize("content", [
    "{not json", json.dumps([[0, 0, 0, 0]]), json.dumps({"dumped_at": 1.0}),
    json.dumps({"window": _rows(20)}),
    json.dumps({"dumped_at": 1.0, "window": [r[:3] for r in _rows(20)]}),
], ids=["corrupt", "legacy_bare_array", "no_window", "no_stamp",
        "three_column_rows"])
def test_an_unreadable_saved_model_counts_as_none(
        tmp_path, monkeypatch, bus_lines, content):
    """A saved model the restore could not read either (``_load_gp_window``
    ignores all five shapes) holds no measured points to protect, so a window
    with enough is saved over it. The last two hold 20 rows the count alone
    would call measured, more than the window's 16: only a count that
    rejects what the restore rejects lets the save through.

    MUTANT "an unreadable file protects itself" (the ``except`` of
    ``_saved_gp_measured_points`` returns ``10 ** 9``) -- RED (corrupt),
    observed verbatim:

        AssertionError: a saved model the restore cannot read blocked a save
        of 16 measured points

    MUTANT "a foreign shape protects itself" (its shape check returns ``10 **
    9``) -- RED (legacy_bare_array, no_window), with the same line.

    MUTANT "the count skips the restore's stamp check" (the
    ``float(data["dumped_at"])`` line removed; also the count as the
    implementer first wrote it) -- RED (no_stamp), with the same line.

    MUTANT "the count skips the restore's row parse" (the row unpack
    replaced by ``window = data["window"]``) -- RED (three_column_rows), with
    the same line.
    """
    p = _gp_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    window = _rows(GP_MIN_MEASURED_POINTS + 5)
    g = _guider(tmp_path, monkeypatch, window)
    g._persist_gp_window()
    assert p.read_text(encoding="utf-8") != content, (
        f"a saved model the restore cannot read blocked a save of "
        f"{len(window)} measured points")
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["window"] == window


# ------------------------------------------ the #243 session, on the real wheel


def _star_frame(cx, cy, w=64, h=64, amp=4000.0, sg=1.6, bg=100):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sg * sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)


_BLANK = _star_frame(32.0, 32.0, amp=0.0)

_IDENT_CAL = {"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
              "y_angle": math.pi / 2, "y_angle_error": 0.0,
              "declination": 0.0, "pier_side": "west",
              "ra_parity": "even", "dec_parity": "even",
              "rotator_angle": 0.0, "binning": 1, "is_valid": True}


class _WallClock:
    def __init__(self, wall: float = 50_000.0) -> None:
        self.wall = wall

    def time(self) -> float:
        return self.wall

    def __getattr__(self, name):
        return getattr(time, name)


class _ScriptCam:
    """Serves ``frames`` in order, one 5 s exposure of virtual clock each,
    then starves until ``stop_guiding`` cancels the exposure. The first frame
    of a start is the reuse path's star-existence check."""

    name = "fake guide camera"

    def __init__(self, clock: _WallClock, frames) -> None:
        self.clock = clock
        self.frames = list(frames)
        self.starved = asyncio.Event()
        self._never = asyncio.Event()

    async def expose(self, exposure_s, gain, offset, binning=1):
        if not self.frames:
            self.starved.set()
            await self._never.wait()
        self.clock.wall += 5.0
        return SimpleNamespace(data=self.frames.pop(0),
                               timestamp=self.clock.wall)


class _Mount:
    name = "fake mount"
    can_pulse_guide = True

    async def pulse_guide(self, direction, ms):
        return None

    async def guide_rates(self):
        return (0.004178, 0.004178)

    async def get_position(self):
        return (5.0, 0.0)

    async def pier_side(self):
        return SimpleNamespace(value="west")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A PPEC guider for ``prof1`` with a reusable persisted calibration, on
    a virtual clock. Returns ``(guider, clock)``."""
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    clock = _WallClock()
    monkeypatch.setattr(nativemod, "time", clock)
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1.json").write_text(
        json.dumps({**_IDENT_CAL, "image_scale_arcsec": 2.0}), encoding="utf-8")
    g = NativeGuider(None, _Mount(),
                     config={"ra_algorithm": "ppec", "image_scale_arcsec": 2.0,
                             "image_scale_known": True, "exposure_s": 5.0},
                     profile_id="prof1")
    return g, clock


async def _start(g, clock, frames) -> None:
    g.cam = _ScriptCam(clock, frames)
    await g.start_guiding()
    await asyncio.wait_for(g.cam.starved.wait(), timeout=30.0)


@needs_wheel
@pytest.mark.asyncio
@pytest.mark.parametrize("saved_before", [True, False],
                         ids=["over_a_saved_model", "with_no_saved_model"])
async def test_a_lock_then_two_blank_frames_saves_nothing(
        rig, tmp_path, bus_lines, saved_before):
    """The #243 session. This session locks, then gets two blank frames, and
    is stopped: its window is two dark rows and nothing else. The stop writes
    nothing, and says why once.

    ``over_a_saved_model``: the profile has a model saved on a good night (20
    measured rows, too old for this start to restore), and it stays
    byte-identical (sha256). Both rules protect it (the window measured
    fewer than the minimum, and fewer than the file), so only a persist with
    neither turns it RED. ``with_no_saved_model``: nothing to compare with,
    so the measured-point gate alone stands between this window and the
    disk.

    MUTANT "no measured-point gate" -- RED (with_no_saved_model; the other
    case is held by the file rule), observed verbatim:

        Failed: the stop of a session that measured nothing wrote a PPEC
        model (2 dark row(s))

    MUTANT "neither rule" (both returns removed: the pre-#243 persist) -- RED
    (both cases), observed verbatim:

        Failed: the stop of a session that measured nothing replaced the
        saved PPEC model (window now: 2 dark row(s))
    """
    g, clock = rig
    before = (_save_file(tmp_path, _rows(20), dumped_at=clock.wall - 10_000.0)
              if saved_before else None)
    await _start(g, clock, [_star_frame(32.0, 32.0), _star_frame(32.0, 32.0),
                            _BLANK, _BLANK])
    # Premise, from the engine: no old model was restored, and the new window
    # is the two dead-reckoned frames, both dark.
    window = g._engine.dump_gp_window()
    assert len(window) == 2 and all(r[2] == GP_DARK_VARIANCE for r in window), (
        window)
    await g.stop_guiding()
    if _sha(tmp_path) != before:
        now = json.loads(_gp_path(tmp_path).read_text(encoding="utf-8"))
        dark = sum(1 for r in now["window"] if r[2] == GP_DARK_VARIANCE)
        if saved_before:
            pytest.fail(f"the stop of a session that measured nothing "
                        f"replaced the saved PPEC model (window now: {dark} "
                        f"dark row(s))")
        pytest.fail(f"the stop of a session that measured nothing wrote a "
                    f"PPEC model ({dark} dark row(s))")
    why = _why(bus_lines)
    assert len(why) == 1 and "0 measured" in why[0], why


@needs_wheel
@pytest.mark.asyncio
async def test_control_a_guided_session_saves_its_model(rig, tmp_path):
    """CONTROL, green under every mutant in this file: a session that locks
    and guides twelve measured frames saves its model at the stop, as before
    #243, stamped with its last measured frame."""
    g, clock = rig
    star = _star_frame(32.0, 32.0)
    await _start(g, clock, [star] * 14)
    window = g._engine.dump_gp_window()
    measured = sum(1 for r in window if r[2] != GP_DARK_VARIANCE)
    assert measured >= GP_MIN_MEASURED_POINTS, f"premise: {measured} measured"
    last_fed = clock.wall
    await g.stop_guiding()
    saved = json.loads(_gp_path(tmp_path).read_text(encoding="utf-8"))
    assert saved["window"] == [list(r) for r in window]
    assert saved["dumped_at"] == last_fed
