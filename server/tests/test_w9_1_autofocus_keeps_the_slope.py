# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 9.1, parts A and B (#1077, #1298): a sweep that proves nothing must not
cost the stored slope, and a default-span sweep that cannot measure its outer
points is retried once, smaller, from what it measured.

THE NIGHTS IT COMES FROM (the point lists are the issues' own):

  * 2026-10-17 02:58: "still no light after holding", then a sweep sized from a
    stored slope; 0 to 2 stars at every point; `not_enough_spread`; the slope
    was discarded and `focus_calibration.json` became `{}`.
  * 2026-10-19 00:32: a rich field (433 stars at the start); the outer points
    were unmeasurable at 8x the focus; the points that WERE measured agreed
    with the slope, and it was discarded anyway.
  * 2026-10-19 03:22: a sparse start (3 stars, then 9) under thin cloud; the
    same discard, of a slope a later sweep had just re-measured. That discard
    wrote the `{}` that #1298 found three weeks later.
  * 2026-11-10 (#1298): the empty file; every sweep at the 350-step default;
    the centre at 11163 read 3.58 px from 84 stars while its 350-step
    neighbours read 25 to 28 px from 5 or 6, and nothing was fitted.

WHAT IT DOES NOW. A stored slope is discarded only when the failed sweep
MEASURED four points and they are too flat for it; the discarded record is kept
as `previous`. A native sweep that fails with a rich tip and a measured wing
point is run once more, centred on the tip, with its outer point on the nearest
wing position that already measured.

HOW THESE TESTS STAY OUT OF THE REAL CONFIG DIRECTORY. The autouse fixture
below rebinds `astrodeck.config.CONFIG_DIR` to pytest's `tmp_path` for every
test in this file and asserts that `focus_calibration_path()` lies under it, so
nothing here can read or write `server/config/` or the session directory.

The sweep engine is scripted (`_Passes`): each pass is the exact sequence of
positions the Rust state machine asked for. The size metric is stubbed by
position, as `test_w2_2_autofocus_lightless_points.py` does.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import pytest

import astrodeck.config as config_mod
import astrodeck.focus.autofocus as AF
import astrodeck.focus.native as N
from astrodeck.config import load_focus_calibration, save_focus_calibration
from astrodeck.devices.base import CameraFrame
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus.autofocus import resolve_sweep
from astrodeck.focus.span import DEFAULT_STEP, STEP_MIN, FocusCalibration

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "frame_class"
if str(_FIXTURES) not in sys.path:
    sys.path.append(str(_FIXTURES))
import synth_frames as synth  # noqa: E402

pytestmark = pytest.mark.native

START = 19200


def _frame(data) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=4.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=-5.0,
                       timestamp=time.time())


@pytest.fixture(autouse=True)
def _own_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    assert tmp_path in config_mod.focus_calibration_path().parents
    yield


# ---------------------------------------------------------------- scaffolding

class _Passes:
    """The sweep engine as one scripted pass per ``FocusSweep`` built. Each
    ``next()`` answers the next step and the last answer repeats, as a real
    engine re-asks for a position it could not measure. Passes beyond the
    scripted ones repeat the last script."""

    scripts: list[list[dict]] = []
    configs: list[dict] = []
    starts: list[int] = []

    def __init__(self, config, start):
        cls = type(self)
        n = len(cls.configs)
        cls.configs.append(dict(config))
        cls.starts.append(int(start))
        self._steps = list(cls.scripts[min(n, len(cls.scripts) - 1)])

    def next(self):
        return self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]

    def add_measurement(self, *args, **kw):
        pass


def _move(*offsets: int) -> list[dict]:
    return [{"action": "move_to", "position": START + o} for o in offsets]


FAILED_FLAT = {"action": "failed", "reason": "not_enough_spread"}


def _done(offset: int = 0) -> dict:
    return {"action": "done", "outcome": {"best_position": START + offset,
                                          "best_value": 3.5}}


def _script(monkeypatch, *scripts: list[dict], start_stars: int = 100) -> None:
    _Passes.scripts = [list(s) for s in scripts]
    _Passes.configs = []
    _Passes.starts = []
    monkeypatch.setattr(N._native, "FocusSweep", _Passes)
    monkeypatch.setattr(
        N._native, "detect_and_measure",
        lambda data, params: ([], {"star_count": start_stars,
                                   "hfr_median": 3.0, "hfr_mad": 0.2}))


async def _rig():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return cam, foc


def _wire(monkeypatch, cam, foc, table: dict[int, list[tuple[float, int]]],
          starved=synth.stars_frame):
    """At an offset in ``table`` the size metric answers the next ``(size,
    stars)`` of its list (the last one repeating); anywhere else it measures
    nothing, a starved point, whose frame is ``starved()``. No frame context
    is passed to the sweep, as the engine passes none: the frame can name an
    obstruction but never no light."""
    seen = {"pos": None}
    used: dict[int, int] = {}
    real_expose = cam.expose

    async def expose(*args, **kw):
        seen["pos"] = await foc.get_position()
        if seen["pos"] - START not in table:
            return _frame(starved())
        return await real_expose(*args, **kw)

    cam.expose = expose

    def metric(frame):
        # the frame's position is not recoverable after the pipelined
        # exposure, so the metric answers from the last exposed position
        row = table.get(seen["pos"] - START)
        if not row:
            return (None, 0)
        i = min(used.get(seen["pos"], 0), len(row) - 1)
        used[seen["pos"]] = used.get(seen["pos"], 0) + 1
        return row[i]

    monkeypatch.setattr(N, "native_sweep_metric", metric)


def _logs(q) -> list[str]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append(str(ev.data.get("message", "")))
    return out


async def _sweep(cam, foc, **kw):
    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(
            cam, foc, exposure_s=0.05, gain=200, steps_each_side=4,
            binning=1, **kw)
        return res, _logs(q)
    finally:
        bus.unsubscribe(q)


def _plant(slope: float, in_focus: float, **kw) -> FocusCalibration:
    cal = FocusCalibration(slope, in_focus, 1, 10, START,
                           kw.get("swept_half_span", 0.0), "2026-10-19")
    save_focus_calibration(None, cal)
    assert load_focus_calibration(None) == cal
    return cal


def _file() -> Path:
    return config_mod.focus_calibration_path()


# ---------------------------------------------------- A: the slope is kept

async def test_a_starless_sweep_keeps_the_stored_calibration(monkeypatch):
    """2026-10-17 02:58 (the NAMED MUTANT's test). A sweep sized from a stored
    slope found 0 to 2 stars at every point and failed `not_enough_spread`. It
    measured nothing, so it proves nothing about the slope: the file's bytes
    must be what they were."""
    cal = _plant(0.0812, 2.88)
    before = _file().read_bytes()
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    _script(monkeypatch, _move(2 * st, st, -st, -2 * st) + [FAILED_FLAT],
            start_stars=5)
    _wire(monkeypatch, cam, foc, {}, starved=synth.no_light_frame)

    res, said = await _sweep(cam, foc)

    assert not res.success
    assert load_focus_calibration(None) == cal, (
        "the slope was lost although the sweep measured nothing")
    assert _file().read_bytes() == before
    assert any("keeping that calibration" in m for m in said), said
    assert not any("discarding that calibration" in m for m in said), said
    assert len(_Passes.configs) == 1, "a sweep with no tip must not be retried"


async def test_a_rich_field_with_an_unmeasurable_outer_point_keeps_the_slope(
        monkeypatch):
    """2026-10-19 00:32. Stored 0.0767 px/step over 2.16 px (step 56). The
    outer points held 1 star; 11226 measured 6.79 px from 3 stars; the centre
    measured 3.48 then 3.25 from 59 and 65. Three measured points, rising: no
    evidence against the slope."""
    cal = _plant(0.0767, 2.16)
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    assert st == 56
    _script(monkeypatch,
            _move(4 * st, 3 * st, 2 * st, st, 0, 0) + [FAILED_FLAT],
            [FAILED_FLAT], start_stars=433)
    _wire(monkeypatch, cam, foc,
          {0: [(3.48, 59), (3.25, 65)], st: [(6.79, 3)]})

    res, said = await _sweep(cam, foc)

    assert not res.success
    assert load_focus_calibration(None) == cal, "the slope was discarded"
    assert any("keeping that calibration" in m for m in said), said


async def test_a_sparse_start_keeps_the_slope(monkeypatch):
    """2026-10-19 03:22. Thin cloud: the probe counted 9 stars, the points
    starved. The slope a later sweep had just measured was discarded."""
    cal = _plant(0.0802, 2.54)
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    _script(monkeypatch, _move(3 * st, 2 * st, st) + [FAILED_FLAT],
            start_stars=9)
    _wire(monkeypatch, cam, foc, {st: [(7.0, 3)]})

    res, _said = await _sweep(cam, foc)

    assert not res.success
    assert res.sparse_field
    assert load_focus_calibration(None) == cal
    assert len(_Passes.configs) == 1, "a sparse start is the engine's retry"


async def test_measured_flat_points_discard_the_slope_and_demote_it(
        monkeypatch):
    """The discard keeps its purpose: five measured points inside a tenth of a
    pixel say the span the slope chose was too narrow to see a V. The record
    goes to `previous`, the key stays, the map is not `{}`."""
    cal = _plant(5.0, 3.0)
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    # five measured points a hundredth of a pixel apart, whatever the step is
    offs = [-2 * st, -st, 0, st, 2 * st]
    table = {o: [(3.0 + 0.01 * i, 50)] for i, o in enumerate(offs)}
    _script(monkeypatch, _move(*offs) + [FAILED_FLAT], start_stars=100)
    _wire(monkeypatch, cam, foc, table)

    res, said = await _sweep(cam, foc)

    assert not res.success
    assert load_focus_calibration(None) is None
    raw = json.loads(_file().read_text(encoding="utf-8"))
    assert raw != {}, "a discard must never write an empty map"
    (entry,) = raw.values()
    assert entry["previous"] == cal.to_json()
    assert isinstance(entry["discarded"], str) and entry["discarded"]
    assert any("discarding that calibration" in m for m in said), said
    assert any("previous" in m for m in said), said
    g = resolve_sweep(foc, None, 4)
    assert g.step == DEFAULT_STEP and not g.measured


# ------------------------------------------------ the evidence rule, in pure

def _wing(tip: float, m: float, d: float) -> float:
    return math.sqrt(tip * tip + (m * d) ** 2)


def _rows(tip: float, m: float, ds=(-200, -100, 0, 100, 200)):
    return [(START + d, _wing(tip, m, d) if d else tip) for d in ds]


STORED = 0.08


@pytest.mark.parametrize("rows, contradicts", [
    ([], False),
    (_rows(3.0, 0.0, (-100, 0, 100)), False),           # three flat points
    (_rows(3.0, 0.0, (-100, 0, 100, 200)), True),       # four flat points
    (_rows(3.0, STORED * 0.45), True),                  # rises at 0.45x
    (_rows(3.0, STORED * 0.55), False),                 # rises at 0.55x
    (_rows(3.0, STORED), False),                        # agrees
    (_rows(3.0, STORED * 3), False),                    # steeper than stored
    (_rows(3.0, STORED, (0, 100, 200, 300)), False),    # one-sided, agrees
    ([(START, 3.0)], False),                            # a tip alone
], ids=["none", "three-flat", "four-flat", "0.45x", "0.55x", "agrees",
        "steeper", "one-sided", "tip-only"])
def test_slope_contradicted_table(rows, contradicts):
    verdict = AF.slope_contradicted(STORED, rows)
    assert verdict.contradicts is contradicts, verdict
    assert verdict.reason, "every verdict says why, in words"


def test_the_evidence_constants_are_the_rulings():
    assert AF.MIN_EVIDENCE_POINTS == 4
    assert AF.SLOPE_DISAGREEMENT_FACTOR == 2.0


# ------------------------------------------------------ the file's history

def _cal(slope: float) -> FocusCalibration:
    return FocusCalibration(slope, 3.0, 1, 10, START, 0.0, "2026-10-19")


def test_one_previous_per_key():
    a, b, c = _cal(0.07), _cal(0.08), _cal(0.09)
    other = _cal(0.05)
    save_focus_calibration("other", other)
    save_focus_calibration("k", a)
    assert config_mod.load_previous_focus_calibration("k") is None
    save_focus_calibration("k", b)
    save_focus_calibration("k", c)
    raw = json.loads(_file().read_text(encoding="utf-8"))
    assert raw["k"]["previous"] == b.to_json(), "one previous, the one before"
    assert "previous" not in raw["k"]["previous"]
    assert config_mod.load_focus_calibration("k") == c
    assert config_mod.load_previous_focus_calibration("k") == b

    assert config_mod.demote_focus_calibration("k", "points were flat") is True
    raw = json.loads(_file().read_text(encoding="utf-8"))
    assert raw["k"]["previous"] == c.to_json()
    assert set(raw["k"]) == {"previous", "discarded"}
    assert config_mod.load_focus_calibration("k") is None
    assert raw["other"] == other.to_json(), "another focuser's entry is untouched"
    # nothing live to demote the second time
    assert config_mod.demote_focus_calibration("k", "again") is False
    assert json.loads(_file().read_text(encoding="utf-8")) == raw


def test_a_discard_of_the_only_key_does_not_empty_the_map():
    save_focus_calibration("only", _cal(0.08))
    config_mod.demote_focus_calibration("only", "flat")
    assert json.loads(_file().read_text(encoding="utf-8")) != {}


# ------------------------------------- R: an old file reads exactly as today

class _Foc:
    _state_key = "zwo-usb-ad03"
    max_position = 60000


OLD_RECORD = {"slope_px_per_step": 0.0802, "in_focus_px": 2.54, "binning": 1,
              "n_points": 5, "best_position": 11163, "swept_half_span": 504.0,
              "measured_on": "2026-10-19", "temperature_c": 10.8}


def _write(text: str) -> None:
    _file().write_text(text, encoding="utf-8")


def test_an_old_file_with_the_key_reads_as_before():
    _write(json.dumps({"zwo-usb-ad03": OLD_RECORD}, indent=2))
    cal = load_focus_calibration("zwo-usb-ad03")
    assert cal == FocusCalibration(0.0802, 2.54, 1, 5, 11163, 504.0,
                                   "2026-10-19", 10.8)
    g = resolve_sweep(_Foc(), None, 4)
    assert g.measured and "0.0802 px/step" in g.basis


def test_an_old_file_without_the_key_reads_as_before(monkeypatch):
    _write(json.dumps({"some-other-focuser": OLD_RECORD}, indent=2))
    assert load_focus_calibration("zwo-usb-ad03") is None
    g = resolve_sweep(_Foc(), None, 4)
    assert not g.measured and g.step == DEFAULT_STEP
    assert json.loads(_file().read_text(encoding="utf-8")) == {
        "some-other-focuser": OLD_RECORD}, "reading must not rewrite the file"


def test_an_old_empty_file_reads_as_before():
    _write("{}")
    assert load_focus_calibration("zwo-usb-ad03") is None
    g = resolve_sweep(_Foc(), None, 4)
    assert not g.measured and g.step == DEFAULT_STEP
    assert _file().read_text(encoding="utf-8") == "{}"


def test_a_minimal_old_record_still_loads():
    _write(json.dumps({"zwo-usb-ad03": {"slope_px_per_step": 0.08,
                                        "in_focus_px": 3.0}}))
    cal = load_focus_calibration("zwo-usb-ad03")
    assert cal is not None and cal.slope_px_per_step == 0.08


def test_a_new_entry_keeps_every_old_key_and_adds_only_two_optional_ones():
    save_focus_calibration("zwo-usb-ad03", FocusCalibration(**{
        k: OLD_RECORD[k] for k in OLD_RECORD}))
    save_focus_calibration("zwo-usb-ad03", _cal(0.09))
    entry = json.loads(_file().read_text(encoding="utf-8"))["zwo-usb-ad03"]
    flat = {k: v for k, v in entry.items() if k != "previous"}
    assert set(flat) == set(OLD_RECORD)
    assert entry["previous"] == OLD_RECORD


# ---------------------------------- the lines that say what happened (#1298)

def _warnings(fn):
    q = bus.subscribe()
    try:
        fn()
        out = []
        while not q.empty():
            ev = q.get_nowait()
            if ev.type == "log":
                out.append((ev.data.get("level"), str(ev.data.get("message"))))
        return out
    finally:
        bus.unsubscribe(q)


def test_a_missing_file_is_info_and_an_emptied_file_is_a_warning():
    lines = _warnings(lambda: resolve_sweep(_Foc(), None, 4))
    assert not [m for lv, m in lines if lv == "warning"], lines

    _write("{}")
    lines = _warnings(lambda: resolve_sweep(_Foc(), None, 4))
    said = [m for lv, m in lines if lv == "warning"]
    assert said, "a file that exists without the key must warn (#1298)"
    assert "zwo-usb-ad03" in said[0] and str(_file()) in said[0], said


def test_a_discarded_record_says_so_at_warning_level():
    save_focus_calibration("zwo-usb-ad03", _cal(0.08))
    config_mod.demote_focus_calibration("zwo-usb-ad03",
                                        "measured points were flat")
    lines = _warnings(lambda: resolve_sweep(_Foc(), None, 4))
    said = [m for lv, m in lines if lv == "warning"]
    assert said, lines
    assert "zwo-usb-ad03" in said[0] and str(_file()) in said[0], said
    assert "discarded" in said[0] and "previous" in said[0], said
    assert "measured points were flat" in said[0], said


# ----------------------------------------- B: one retry, from the evidence

#: 2026-11-10 21:58 (#1298): the tip and the 350-step neighbours that measured.
#: The tip sits 40 steps off the sweep's start so the retry's centre is visible.
TIP = 40
ND_TABLE = {TIP: [(3.58, 84)], TIP - 350: [(25.0, 6)],
            TIP + 350: [(28.0, 5)]}
#: One default pass: the outer points starve, the last position is re-asked
#: three times, the sweep gives up with the curve it has.
ND_PASS = _move(-1400, -1050, -700, TIP - 350, TIP, TIP + 350, 700, 700, 700)
#: The retry's points: slope 0.08 px/step, 88 steps, every point measurable.
RETRY_TABLE = {TIP + k * 88: [(_wing(3.58, 0.08, k * 88) if k else 3.58, 40)]
               for k in range(-4, 5)}


async def test_a_default_sweep_that_cannot_measure_its_outer_points_is_retried(
        monkeypatch):
    """#1298. The wings at +/-350 measured (25 and 28 px, 5 and 6 stars), the
    outer points did not, and nothing was fitted. The retry puts its outer
    point on the nearest wing position that measured: 350/4 = 88 steps."""
    cam, foc = await _rig()
    _script(monkeypatch, ND_PASS,
            _move(*[TIP - 352 + 88 * i for i in range(9)]) + [_done(TIP)])
    _wire(monkeypatch, cam, foc, {**ND_TABLE, **RETRY_TABLE})

    res, said = await _sweep(cam, foc)

    assert res.success, res.message
    assert len(_Passes.configs) == 2
    assert _Passes.configs[0]["step_size"] == DEFAULT_STEP
    assert _Passes.configs[0]["offset_steps"] == 4
    assert _Passes.configs[1]["step_size"] == 88
    assert _Passes.configs[1]["offset_steps"] == 4
    assert _Passes.starts[1] == START + TIP, (
        "the retry must start from the last good position, the tip")
    assert any("retrying once" in m for m in said), said
    assert load_focus_calibration(None) is not None, (
        "a successful retry must re-learn the slope")


async def test_the_retry_floors_at_the_step_minimum_and_narrows_the_sides(
        monkeypatch):
    """2026-10-19 00:32: the nearest wing measured at 56 steps, so the
    quarter is 14, under the 25-step floor: two sides of 25."""
    _plant(0.0767, 2.16)
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    _script(monkeypatch,
            _move(4 * st, 3 * st, 2 * st, st, 0, 0) + [FAILED_FLAT],
            [FAILED_FLAT], start_stars=433)
    _wire(monkeypatch, cam, foc,
          {0: [(3.48, 59), (3.25, 65)], st: [(6.79, 3)]})

    await _sweep(cam, foc)

    assert len(_Passes.configs) == 2
    assert _Passes.configs[1]["step_size"] == STEP_MIN == 25
    assert _Passes.configs[1]["offset_steps"] == 2


async def test_a_failed_retry_is_final_and_discards_nothing(monkeypatch):
    cam, foc = await _rig()
    _script(monkeypatch, ND_PASS, _move(-88, 0, 88) + [FAILED_FLAT])
    _wire(monkeypatch, cam, foc, {**ND_TABLE, **RETRY_TABLE})

    res, _said = await _sweep(cam, foc)

    assert not res.success
    assert len(_Passes.configs) == 2, "exactly one retry, never a third sweep"
    assert not _file().exists(), "a failed retry must write nothing"


async def test_a_failed_retry_keeps_a_stored_slope(monkeypatch):
    cal = _plant(0.0767, 2.16)
    cam, foc = await _rig()
    st = resolve_sweep(foc, None, 4).step
    _script(monkeypatch,
            _move(4 * st, 3 * st, 2 * st, st, 0, 0) + [FAILED_FLAT],
            _move(-25, 0, 25) + [FAILED_FLAT], start_stars=433)
    _wire(monkeypatch, cam, foc,
          {0: [(3.48, 59), (3.25, 65)], st: [(6.79, 3)],
           25: [(3.25, 60)], -25: [(3.25, 60)]})
    before = _file().read_bytes()

    res, _said = await _sweep(cam, foc)

    assert not res.success and len(_Passes.configs) == 2
    assert load_focus_calibration(None) == cal
    assert _file().read_bytes() == before


async def test_a_narrow_retry_writes_no_record(monkeypatch):
    """The +/-50 retry of 2026-10-19 00:32 succeeds, and writes nothing:
    `defocus_slope` needs four wing points at 1.5x the tip, and a narrow
    retry offers two. So the `swept_half_span` ceiling is not set by it."""
    cam, foc = await _rig()
    st = 56
    _script(monkeypatch,
            _move(4 * st, 3 * st, 2 * st, st, 0, 0) + [FAILED_FLAT],
            _move(-50, -25, 0, 25, 50) + [_done(0)], start_stars=433)
    table = {0: [(3.48, 59), (3.25, 65)], st: [(6.79, 3)]}
    for o in (-50, -25, 25, 50):
        table[o] = [(_wing(3.25, 0.1065, o), 40)]
    _wire(monkeypatch, cam, foc, table)

    res, _said = await _sweep(cam, foc)

    assert res.success, res.message
    assert len(_Passes.configs) == 2
    assert load_focus_calibration(None) is None


def _no_retry_case(**over):
    base = dict(table=ND_TABLE, start_stars=100, step=None)
    base.update(over)
    return base


@pytest.mark.parametrize("case", [
    _no_retry_case(table={TIP: [(3.58, 9)], TIP - 350: [(25.0, 6)],
                          TIP + 350: [(28.0, 5)]}),        # thin tip
    _no_retry_case(table={TIP: [(3.58, 84)], TIP - 350: [(3.9, 6)],
                          TIP + 350: [(4.1, 5)]}),         # no wing point
    _no_retry_case(start_stars=9),                          # sparse start
    _no_retry_case(step=350),                               # operator's step
    _no_retry_case(table={TIP: [(3.58, 84)]}),              # tip only
], ids=["thin-tip", "no-wing", "sparse-start", "explicit-step", "tip-only"])
async def test_no_retry_without_a_rich_tip_and_a_measured_wing(
        monkeypatch, case):
    cam, foc = await _rig()
    _script(monkeypatch, ND_PASS, start_stars=case["start_stars"])
    _wire(monkeypatch, cam, foc, case["table"])
    kw = {} if case["step"] is None else {"step": case["step"]}

    res, _said = await _sweep(cam, foc, **kw)

    assert not res.success
    assert len(_Passes.configs) == 1


async def test_a_success_is_not_retried(monkeypatch):
    cam, foc = await _rig()
    _script(monkeypatch, _move(-88, 0, 88) + [_done(0)])
    _wire(monkeypatch, cam, foc, {-88: [(7.9, 40)], 0: [(3.58, 80)],
                                  88: [(7.9, 40)]})
    res, _said = await _sweep(cam, foc)
    assert res.success and len(_Passes.configs) == 1


def test_retry_geometry_table():
    rg = AF.retry_geometry
    tip = START + TIP
    pts = [(tip - 350, 25.0), (tip, 3.58), (tip + 350, 28.0)]
    g = rg(pts, [6, 84, 5], 350, 4)
    assert (g.centre, g.step, g.steps_each_side) == (tip, 88, 4)
    # the nearer wing wins
    g = rg(pts + [(tip + 100, 9.0)], [6, 84, 5, 4], 350, 4)
    assert (g.step, g.steps_each_side) == (STEP_MIN, 4)
    # a sweep already as small as the retry would be is not retried
    assert rg([(tip - 25, 8.0), (tip, 3.58), (tip + 25, 8.0)],
              [6, 84, 5], 25, 2) is None
    assert rg([], [], 350, 4) is None
    assert rg(pts, [6, 9, 5], 350, 4) is None, "a thin tip"
