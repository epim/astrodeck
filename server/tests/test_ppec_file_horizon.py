"""#253, S2 orchestrator ruling 3: a saved PPEC file's measured count
protects it from a save only while the next start could still restore it.

#243 made ``_persist_gp_window`` refuse to save a window over a saved file
whose window holds more measured rows, and never asked how old that file was.
The engine restores a saved window only when the downtime since its
``dumped_at`` is under ``retain_max_pct_period`` percent (40) of the kernel
period it compares against, 80 s for the default 200 s period
(``restore_window``, strict ``<``). A file older than that is never restored
again, yet its count blocked every shorter session's save. After one long
guided night no shorter session saved anything, and each quick stop and start
(the stand-down every mosaic hop makes, spec 6.10) began PPEC from nothing.

Now the count blocks a save only while ``now - dumped_at`` lies inside that
horizon. Python mirrors it (``gp_restore_horizon_s``, ``gp_could_restore``),
and the mirror is pinned here to the Rust source and to the real
``astrodeck_native`` wheel.

Each test names the mutation of ``guide/native.py`` it was shown RED under,
run in a private scratch copy of ``server/`` (never the shared tree), with
the observed failure quoted verbatim.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import pytest

import astrodeck.guide.native as nativemod
from astrodeck.guide.native import (GP_DARK_VARIANCE,
                                    GP_DEFAULT_KERNEL_PERIOD_S,
                                    GP_RETAIN_MAX_PCT_PERIOD,
                                    gp_could_restore, gp_restore_horizon_s)
# The fixtures and helpers of the #243 file, so the #253 probe below drives
# the same fake rig (rootdir-relative import, as elsewhere in this suite).
from test_ppec_persist_needs_measured_points import (  # noqa: F401
    _GP_RS, _WallClock, _gp_path, _guider, _rows, _rust_fn_body, _save_file,
    _sha, _star_frame, _start, _why, needs_wheel, rig)

_ENGINE_RS = _GP_RS.parent.parent / "engine.rs"

#: The horizon for a file with no period, as the mirror computes it. Every
#: age below is derived from it rather than written as 80, so the mirror
#: follows the wheel; the parity test is what pins it to the wheel.
_H = gp_restore_horizon_s()


# ------------------------------------------------ the constants are the engine's


def test_the_horizon_constants_are_the_rust_engines():
    """``GP_RETAIN_MAX_PCT_PERIOD`` and ``GP_DEFAULT_KERNEL_PERIOD_S`` are
    what ``GpParams::default()`` sets, and the engine every start builds
    gives its PPEC axis exactly those defaults (``make_algo``), so the
    restoring engine's kernel period is the default one. Read from the Rust
    source, so the pin holds where the wheel is absent; each regex must match,
    so a refactored engine fails here rather than passing unread.

    MUTANT "Python horizon at 50 percent" (``GP_RETAIN_MAX_PCT_PERIOD =
    50.0``) -- RED, observed verbatim:

        AssertionError: GP_RETAIN_MAX_PCT_PERIOD is 50.0; GpParams::default
        sets retain_max_pct_period 40.0

    MUTANT "the default period drifts" (``GP_DEFAULT_KERNEL_PERIOD_S =
    100.0``) -- RED, observed verbatim:

        AssertionError: GP_DEFAULT_KERNEL_PERIOD_S is 100.0; GpParams::default
        sets periodic_period 200.0
    """
    src = _GP_RS.read_text(encoding="utf-8")
    impl = src.find("impl Default for GpParams")
    assert impl >= 0, "GpParams no longer implements Default"
    body = _rust_fn_body(src[impl:], "default")
    pct = re.search(r"\bretain_max_pct_period:\s*([0-9.eE+-]+)\s*,", body)
    per = re.search(r"\bperiodic_period:\s*([0-9.eE+-]+)\s*,", body)
    assert pct, "GpParams::default no longer sets retain_max_pct_period"
    assert per, "GpParams::default no longer sets periodic_period"
    assert GP_RETAIN_MAX_PCT_PERIOD == float(pct.group(1)), (
        f"GP_RETAIN_MAX_PCT_PERIOD is {GP_RETAIN_MAX_PCT_PERIOD}; "
        f"GpParams::default sets retain_max_pct_period {pct.group(1)}")
    assert GP_DEFAULT_KERNEL_PERIOD_S == float(per.group(1)), (
        f"GP_DEFAULT_KERNEL_PERIOD_S is {GP_DEFAULT_KERNEL_PERIOD_S}; "
        f"GpParams::default sets periodic_period {per.group(1)}")
    make = _rust_fn_body(_ENGINE_RS.read_text(encoding="utf-8"), "make_algo")
    builds = re.findall(r"GaussianProcessGuider::new\(([^)]*\)?)\)", make)
    assert builds == ["GpParams::default()"], (
        f"make_algo builds the PPEC axis with {builds}; the mirror assumes "
        f"the restoring engine's kernel is GpParams::default()'s")


# ------------------------------------------- parity with the real wheel


@needs_wheel
def test_could_restore_answers_as_the_wheel_does_at_the_horizon(rig):
    """For a window restored at a downtime of the horizon minus 0.5 s, one
    ulp under it, exactly it, one ulp over it, and plus 0.5 s (and at zero
    and 0.5 s before the stamp), a fresh engine built from the guider's own
    config answers ``restore_gp_window`` as ``gp_could_restore`` does. At
    exactly the horizon both answer False: the strict ``<``.

    The wheel's boundary for a default engine was measured at 80 s exactly
    (79.99999999999999 restores, 80.0 does not), so the mirror's 40 percent
    of 200 s needed no correction.

    MUTANT "Python horizon at 50 percent" (``GP_RETAIN_MAX_PCT_PERIOD =
    50.0``) -- RED, observed verbatim:

        AssertionError: parity with the wheel broken at horizon - 0.5 s (99.5
        s): wheel False, Python True; one ulp under (99.99999999999999 s):
        wheel False, Python True

    MUTANT "the horizon itself restores" (``<=`` in ``gp_could_restore``)
    -- RED, observed verbatim:

        AssertionError: parity with the wheel broken at exactly the horizon
        (80.0 s): wheel False, Python True

    MUTANT "no lower bound" (``gp_could_restore`` drops ``0.0 <=``) -- RED,
    observed verbatim:

        AssertionError: parity with the wheel broken at 0.5 s before the stamp
        (-0.5 s): wheel False, Python True

    "The default period drifts" (``GP_DEFAULT_KERNEL_PERIOD_S = 100.0``) is
    RED here too, at exactly the horizon, one ulp over and plus 0.5 s: the
    mirror's horizon falls to 40 s while the wheel's stays at 80 s.
    """
    g, _clock = rig
    cfg = g._build_engine_config((0.004178, 0.004178))
    points = [tuple(r) for r in _rows(20)]
    table = {
        "horizon - 0.5 s": _H - 0.5,
        "one ulp under": math.nextafter(_H, -math.inf),
        "exactly the horizon": _H,
        "one ulp over": math.nextafter(_H, math.inf),
        "horizon + 0.5 s": _H + 0.5,
        "zero": 0.0,
        "0.5 s before the stamp": -0.5,
    }
    answers = {}
    for name, downtime in table.items():
        # A fresh engine per ask: a restore that succeeds changes the engine.
        wheel = nativemod._native.GuideEngine(cfg).restore_gp_window(
            points, downtime)
        answers[name] = (downtime, wheel, gp_could_restore(downtime))
    broken = [f"{name} ({d!r} s): wheel {w}, Python {p}"
              for name, (d, w, p) in answers.items() if w != p]
    assert not broken, "parity with the wheel broken at " + "; ".join(broken)
    assert answers["exactly the horizon"][1:] == (False, False), answers
    # Premise: the table straddles a real boundary. A wheel that restored
    # nothing would agree with a Python that restored nothing, vacuously.
    assert answers["horizon - 0.5 s"][1] is True, answers


# ------------------------------------------ the #253 probe, on the real wheel


@needs_wheel
@pytest.mark.asyncio
async def test_253_a_quick_restart_restores_tonights_model(rig, tmp_path,
                                                          bus_lines):
    """The #253 probe. The profile's saved model holds 40 measured rows and
    is a day old. Tonight's session guides 18 measured frames, is stopped
    (the stand-down before a hop), and is started again 35 s later. The
    start restores tonight's 18 rows, and no stop said "not saved".

    MUTANT "the count protects at any age" (the horizon check in
    ``_saved_gp_measured_points`` removed: the #243 rule as it shipped) --
    RED, observed verbatim:

        AssertionError: tonight's window rows: 18 | restored after a 35 s hop:
        0 | ["native guider: PPEC model for profile prof1 not saved: this
        session's window holds 18 measured points and the saved model's 40; the
        saved model is kept"]
    """
    g, clock = rig
    _save_file(tmp_path, _rows(40), dumped_at=clock.wall - 86_400.0)
    star = _star_frame(32.0, 32.0)
    # The first frame is the reuse path's star check and the second locks;
    # each of the other 18 is one measured row.
    await _start(g, clock, [star] * 20)
    tonight = [list(r) for r in g._engine.dump_gp_window()]
    measured = sum(1 for r in tonight if r[2] != GP_DARK_VARIANCE)
    assert len(tonight) == measured == 18, (
        f"premise: tonight's window holds {len(tonight)} rows, {measured} "
        f"measured")
    await g.stop_guiding()
    clock.wall += 35.0
    # The star check only: the loop then starves before it feeds a frame, so
    # the window is exactly what the start restored.
    await _start(g, clock, [star])
    restored = [list(r) for r in g._engine.dump_gp_window()]
    why = _why(bus_lines)
    try:
        assert restored == tonight and not why, (
            f"tonight's window rows: {len(tonight)} | restored after a 35 s "
            f"hop: {len(restored)} | {why}")
    finally:
        await g.stop_guiding()


# --------------------------------- the save gate, on a fake engine and clock


_NOW = 50_000.0


def _stop_over_a_file(tmp_path, monkeypatch, age_s, window, file_rows=40,
                      extra=None):
    """Save a ``file_rows``-measured model ``age_s`` before ``_NOW`` (plus
    any ``extra`` keys), then persist ``window`` at ``_NOW``. Returns the
    file's sha256 from before the persist."""
    monkeypatch.setattr(nativemod, "time", _WallClock(_NOW))
    p = _gp_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"dumped_at": _NOW - age_s,
                             "window": _rows(file_rows), **(extra or {})}),
                 encoding="utf-8")
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    _guider(tmp_path, monkeypatch, window)._persist_gp_window()
    return before


def _check(tmp_path, bus_lines, before, window, protects, what):
    why = _why(bus_lines)
    if protects:
        assert _sha(tmp_path) == before, (
            f"a saved model of 40 measured points {what}, which the next "
            f"start could restore, was replaced by a window of 18")
        assert len(why) == 1, f"the skipped save did not say why, once: {why}"
        assert "18 measured" in why[0] and "40" in why[0], why[0]
    else:
        saved = json.loads(_gp_path(tmp_path).read_text(encoding="utf-8"))
        assert saved["window"] == window, (
            f"a saved model of 40 measured points {what}, which the next "
            f"start could not restore, blocked a save of 18: {why}")
        assert not why, why


# Every age is exact at _NOW's magnitude, so ``now - dumped_at`` is the age
# to the bit. (One ulp under 80 is not: 50000 - 79.99999999999999 rounds to
# 49920.0. The parity test above owns the ulps.)
@pytest.mark.parametrize("age_s, protects", [
    (0.0, True),
    (30.0, True),
    (_H - 0.5, True),
    (_H, False),
    (_H + 0.5, False),
    (86_400.0, False),
    (-0.5, False),
], ids=["just_written", "control_30_s_old", "half_a_second_inside",
        "exactly_the_horizon", "half_a_second_outside", "a_day_old",
        "stamped_in_the_future"])
def test_the_file_protects_itself_only_inside_the_horizon(
        tmp_path, monkeypatch, bus_lines, age_s, protects):
    """A saved model with 40 measured rows, ``age_s`` old at the stop, and a
    window with 18. Inside the horizon the file is kept byte-identical and
    the skip says why once (CONTROL: #243's rule, unchanged); from exactly
    the horizon on it protects nothing and the window is saved. Nor does a
    file stamped after now: the restore refuses a negative downtime, and a
    clock stepped back by an unknown amount would otherwise let the file
    protect itself for as long as the step.

    MUTANT "the count protects at any age" -- RED (exactly_the_horizon,
    half_a_second_outside, a_day_old, stamped_in_the_future), observed
    verbatim (a_day_old):

        AssertionError: a saved model of 40 measured points 86400.0 s old
        (horizon 80 s), which the next start could not restore, blocked a save
        of 18: ["native guider: PPEC model for profile prof1 not saved: this
        session's window holds 18 measured points and the saved model's 40; the
        saved model is kept"]

    MUTANT "the horizon itself restores" -- RED (exactly_the_horizon),
    observed verbatim:

        AssertionError: a saved model of 40 measured points 80.0 s old (horizon
        80 s), which the next start could not restore, blocked a save of 18:
        ["native guider: PPEC model for profile prof1 not saved: this session's
        window holds 18 measured points and the saved model's 40; the saved
        model is kept"]

    MUTANT "no lower bound" -- RED (stamped_in_the_future), observed
    verbatim:

        AssertionError: a saved model of 40 measured points -0.5 s old (horizon
        80 s), which the next start could not restore, blocked a save of 18:
        ["native guider: PPEC model for profile prof1 not saved: this session's
        window holds 18 measured points and the saved model's 40; the saved
        model is kept"]

    MUTANT "nothing protects" (``_saved_gp_measured_points`` returns 0) --
    RED (just_written, control_30_s_old, half_a_second_inside), observed
    verbatim (control_30_s_old):

        AssertionError: a saved model of 40 measured points 30.0 s old (horizon
        80 s), which the next start could restore, was replaced by a window of
        18

    Not caught here, by construction: "Python horizon at 50 percent". The
    ages derive from the mirror's own horizon, so they move with it; the
    constants, parity and period tests catch it. "The default period drifts"
    passes here for the same reason.
    """
    window = _rows(18, t0=900.0)
    before = _stop_over_a_file(tmp_path, monkeypatch, age_s, window)
    _check(tmp_path, bus_lines, before, window, protects,
           f"{age_s!r} s old (horizon {_H:g} s)")


@pytest.mark.parametrize("period_s, age_s, protects", [
    (100.0, 35.0, True),
    (100.0, 45.0, False),
    (None, 45.0, True),
], ids=["own_period_inside", "own_period_outside", "control_no_period"])
def test_a_period_in_the_file_sets_its_horizon(tmp_path, monkeypatch,
                                               bus_lines, period_s, age_s,
                                               protects):
    """The horizon is 40 percent of the file's ``period_s`` when it carries
    one (40 s for 100 s), and of the default kernel's 200 s when it does not
    (80 s). No writer puts ``period_s`` in the file today; the restore
    compares against the fresh engine's default kernel, and the key is read
    for the day a writer persists the learned period and the restore applies
    it.

    Also RED here, besides the tests that name them: "Python horizon at 50
    percent" (own_period_outside) and "the default period drifts"
    (control_no_period), because these ages are fixed numbers of seconds.

    MUTANT "the file's period is ignored" (``gp_restore_horizon_s`` always
    uses ``GP_DEFAULT_KERNEL_PERIOD_S``) -- RED (own_period_outside),
    observed verbatim:

        AssertionError: a saved model of 40 measured points 45.0 s old with
        period_s 100.0, which the next start could not restore, blocked a save
        of 18: ["native guider: PPEC model for profile prof1 not saved: this
        session's window holds 18 measured points and the saved model's 40; the
        saved model is kept"]
    """
    window = _rows(18, t0=900.0)
    extra = {} if period_s is None else {"period_s": period_s}
    before = _stop_over_a_file(tmp_path, monkeypatch, age_s, window,
                               extra=extra)
    _check(tmp_path, bus_lines, before, window, protects,
           f"{age_s!r} s old with period_s {period_s!r}")


@pytest.mark.parametrize("period_s", [
    0, -100.0, "abc", float("nan"), float("inf"), True, [200.0],
], ids=["zero", "negative", "text", "nan", "infinite", "boolean", "list"])
@pytest.mark.parametrize("age_s, protects", [
    (45.0, True), (86_400.0, False),
], ids=["inside_80_s", "a_day_old"])
def test_a_period_that_is_not_a_period_means_the_default(
        tmp_path, monkeypatch, bus_lines, period_s, age_s, protects):
    """A ``period_s`` that is not a positive finite number is ignored, as the
    restore ignores every ``period_s``: the horizon stays the default
    kernel's 80 s. So the file protects itself at 45 s and not at a day.
    Trusted, zero, a negative number, NaN or a boolean would shrink the
    horizon below 45 s or to nothing, text or a list would make the whole
    file unreadable, and infinity would let a day-old file block saves
    forever, the #253 defect again.

    MUTANT "any period_s is trusted" (``period = float(period_s)`` whenever
    it is not None) -- RED (inside_80_s for zero, negative, text, nan,
    boolean, list; a_day_old for infinite), observed verbatim (zero,
    inside_80_s):

        AssertionError: a saved model of 40 measured points 45.0 s old with
        period_s 0, which the next start could restore, was replaced by a
        window of 18

    and (infinite, a_day_old):

        AssertionError: a saved model of 40 measured points 86400.0 s old with
        period_s inf, which the next start could not restore, blocked a save of
        18: ["native guider: PPEC model for profile prof1 not saved: this
        session's window holds 18 measured points and the saved model's 40; the
        saved model is kept"]
    """
    window = _rows(18, t0=900.0)
    before = _stop_over_a_file(tmp_path, monkeypatch, age_s, window,
                               extra={"period_s": period_s})
    _check(tmp_path, bus_lines, before, window, protects,
           f"{age_s!r} s old with period_s {period_s!r}")
