# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The date replay and the seasonal Sun-cone guard (#1045, #1046).

``tests/tools/date_replay.py`` runs the engine-using test files at chosen days
through ``shift_clock`` and names the failures that are the date's.
``_SunConeGuard`` (conftest.py, with ``_sun_cone.py``) fails, at its own
teardown, a test that slews by the REAL Sun to a target the exclusion cone can
refuse on some day of the year, unless its file is in the ratchet baseline.

What is graded here, against the real code and not against a copy of it:

* ``seasonal`` agrees with the real cone: for each target, "exposed" is true
  exactly when ``coords.sun_radec`` puts the Sun inside 30 deg of it on some
  day of a year. M42 is, the north ecliptic pole is not.
* The wrapper ``install`` puts on ``Hub._check_solar`` records a seasonal
  target asked of the real Sun, records nothing once the Sun is pinned, and
  answers (and raises) exactly what the wrapped method does.
* The tool's pure parts, and the whole tool end to end on three throwaway test
  files: one green on every day, one red on every day, one red only in 2027.
  Only the last is reported as the date's.

NAMED MUTANTS, each run from a byte backup, restored by byte copy:

* ``SUN_PROOF_TARGET`` in ``_simhub.py`` made M42 -> the three tests of #1045
  each end with an ERROR at teardown from the guard, and from 13 June 2027 the
  runs end "unsafe: slew blocked by sun-exclusion cone".
* ``seasonal`` made ``return False`` -> ``test_seasonal_agrees_with_the_real_
  cone`` (M42 is exposed).
* ``date_dependent`` made ``shifted`` -> ``test_the_tool_names_only_the_
  failures_the_date_makes`` (the always-red file is reported).
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from _sun_cone import (closest_sun_approach_deg, install, load_baseline,
                       seasonal)
from astrodeck.catalog import coords
from astrodeck.config import config_store
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import date_replay  # noqa: E402

M42 = (5.5881, -5.3911)
POLE = (18.0, 66.56)
CONE = 30.0


def _days_inside_the_cone(target) -> int:
    first = dt.datetime(2025, 1, 1, 12, tzinfo=dt.timezone.utc)
    n = 0
    for day in range(365):
        when = (first + dt.timedelta(days=day)).timestamp()
        sun = coords.sun_radec(when)
        if coords.angular_sep_deg(*target, *sun) < CONE:
            n += 1
    return n


@pytest.mark.parametrize("target", [M42, POLE, (5.0, 10.0), (0.7, 41.0),
                                    (12.0, -60.0), (6.0, 66.0), (0.0, 0.0)])
def test_seasonal_agrees_with_the_real_cone(target):
    inside = _days_inside_the_cone(target)
    assert seasonal(*target, CONE) is (inside > 0), (
        f"{target}: the real Sun is inside {CONE:g} deg on {inside} days of "
        f"2025, the closest it comes is about "
        f"{closest_sun_approach_deg(*target):.1f} deg")


def test_m42_is_inside_the_cone_for_the_days_the_issue_names():
    assert 15 <= _days_inside_the_cone(M42) <= 25


def test_a_position_that_cannot_be_measured_is_not_called_seasonal():
    assert seasonal("x", None, CONE) is False


# -------------------------------------------------------------- the wrapper

class _Safety:
    solar_avoidance = True
    solar_exclusion_deg = CONE


class _Cfg:
    safety = _Safety()


class _Store:
    @staticmethod
    def cfg():
        return _Cfg


class _FakeCoords:
    """A coords module with a sun function the wrapper can compare."""

    def __init__(self):
        self.sun_radec = lambda *a: (0.0, 0.0)


class _FakeHub:
    def _check_solar(self, ra, dec, *, force=False):
        if dec < 0:
            raise DeviceError("refused")
        return "clear"


def test_the_wrapper_records_a_seasonal_target_and_changes_nothing_else():
    seen, fake = [], _FakeCoords()
    hub_cls = type("H", (_FakeHub,), {})
    install(hub_cls, fake, _Store, lambda *a: seen.append(a))
    hub = hub_cls()
    assert hub._check_solar(*POLE) == "clear"
    assert seen == []
    assert hub._check_solar(5.5881, 10.0, force=True) == "clear"
    assert len(seen) == 1 and seen[0][:2] == (5.5881, 10.0)
    with pytest.raises(DeviceError, match="refused"):
        hub._check_solar(*M42)
    assert len(seen) == 2


def test_a_pinned_sun_takes_the_calendar_out_so_nothing_is_recorded():
    seen, fake = [], _FakeCoords()
    hub_cls = type("H", (_FakeHub,), {})
    install(hub_cls, fake, _Store, lambda *a: seen.append(a))
    fake.sun_radec = lambda *a: (12.0, 0.0)          # a test pinned the Sun
    hub_cls()._check_solar(5.5881, 10.0)
    assert seen == []


def test_the_wrapper_is_installed_once_and_a_disarmed_cone_records_nothing():
    seen, fake = [], _FakeCoords()
    hub_cls = type("H", (_FakeHub,), {})
    install(hub_cls, fake, _Store, lambda *a: seen.append(a))
    wrapped = hub_cls._check_solar
    install(hub_cls, fake, _Store, lambda *a: seen.append(a))
    assert hub_cls._check_solar is wrapped
    _Safety.solar_avoidance = False
    try:
        hub_cls()._check_solar(5.5881, 10.0)
    finally:
        _Safety.solar_avoidance = True
    assert seen == []


# -------------------------------------------------- the guard in conftest.py

@pytest.fixture
def guard(request):
    plugin = request.config.pluginmanager.get_plugin("astrodeck-sun-cone-guard")
    assert plugin is not None, "conftest.py did not register the guard"
    return plugin


@pytest.mark.seasonal_sun_target
def test_the_guard_sees_the_real_hub_ask_the_real_sun_about_m42(guard):
    assert getattr(Hub._check_solar, "_sun_cone_guard", False)
    try:
        Hub()._check_solar(*M42)
    except DeviceError:
        pass                    # refused today or not: the guard asks the sky
    hits = guard.hits.pop(guard.current, [])
    assert len(hits) == 1 and hits[0][:2] == M42


def test_the_guard_does_not_see_the_pole_or_a_pinned_sun(guard, monkeypatch):
    Hub()._check_solar(*POLE)
    monkeypatch.setattr(coords, "sun_radec", lambda t=None: (12.0, 0.0))
    Hub()._check_solar(*M42)
    assert guard.hits.get(guard.current, []) == []


def test_the_baseline_names_files_that_exist():
    here = Path(__file__).resolve().parent
    missing = sorted(p for p in load_baseline() if not (here / p).is_file())
    assert not missing, f"stale entries in sun_cone_baseline.txt: {missing}"


# ------------------------------------------------------- the tool, pure parts

def test_default_dates_are_the_next_four_seasons_in_order():
    now = dt.datetime(2026, 10, 16, 8, tzinfo=dt.timezone.utc)
    assert date_replay.dates_for(now) == [
        dt.date(2026, 12, 21), dt.date(2027, 3, 21), dt.date(2027, 6, 13),
        dt.date(2027, 9, 23)]


def test_a_day_on_or_after_today_is_this_year_and_one_before_it_is_next():
    now = dt.datetime(2026, 6, 13, 23, tzinfo=dt.timezone.utc)
    got = date_replay.dates_for(now, days=("06-13", "06-12"))
    assert got == [dt.date(2026, 6, 13), dt.date(2027, 6, 12)]


def test_every_days_gives_count_days_that_far_apart():
    now = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)
    got = date_replay.dates_for(now, every_days=60, count=3)
    assert got == [dt.date(2026, 12, 15), dt.date(2027, 2, 13),
                   dt.date(2027, 4, 14)]


def test_the_offset_makes_the_clock_read_noon_utc_of_the_day():
    now = dt.datetime(2026, 10, 16, 8, tzinfo=dt.timezone.utc).timestamp()
    off = date_replay.offset_seconds(dt.date(2027, 6, 13), now)
    read = dt.datetime.fromtimestamp(now + off, dt.timezone.utc)
    assert read == dt.datetime(2027, 6, 13, 12, tzinfo=dt.timezone.utc)


def test_failures_are_read_from_the_short_summary():
    out = textwrap.dedent("""\
        ==== short test summary info ====
        FAILED tests/test_a.py::test_one - AssertionError: x
        ERROR tests/test_b.py::test_two
        FAILED tests/test_c.py::test_p[a-b] - boom
        3 failed, 9 passed in 1.0s
        """)
    assert date_replay.parse_failures(out) == {
        "tests/test_a.py::test_one", "tests/test_b.py::test_two",
        "tests/test_c.py::test_p[a-b]"}


def test_the_dates_failures_are_shifted_minus_real_minus_artifacts():
    shifted = {"a", "b", "c"}
    assert date_replay.date_dependent(shifted, {"b"}, frozenset()) == {"a", "c"}
    assert date_replay.date_dependent(shifted, {"b"}, frozenset({"c"})) == {"a"}


def test_the_engine_files_include_the_three_files_of_1045():
    names = {p.name for p in date_replay.engine_using_files()}
    assert {"test_w5_unguided_frame_rejected.py",
            "test_w17_dusk_flats_panel.py"} <= names
    assert "test_shift_clock_plugin.py" not in names


# ------------------------------------------------------ the tool, end to end

_PROBES = {
    "test_green.py": "def test_green():\n    assert True\n",
    "test_always_red.py": "def test_always_red():\n    assert False\n",
    "test_red_in_2027.py": textwrap.dedent("""\
        import datetime

        def test_red_in_2027():
            assert datetime.datetime.now().year < 2027
        """),
}


def test_the_tool_names_only_the_failures_the_date_makes(tmp_path):
    if dt.date.today().year >= 2027:
        pytest.skip("the probe is green only before 2027")
    files = []
    for name, body in _PROBES.items():
        (tmp_path / name).write_text(body, encoding="utf-8", newline="\n")
        files.append(str(tmp_path / name))
    proc = subprocess.run(
        [sys.executable, str(Path(date_replay.__file__)), "--files", *files,
         "--dates", "2027-06-13", "--workers", "0", "--python", sys.executable,
         "--cwd", str(tmp_path)],
        capture_output=True, text=True, timeout=240, cwd=tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    reported = [ln for ln in out.splitlines() if "DATE-DEPENDENT" in ln]
    assert len(reported) == 1 and "test_red_in_2027" in reported[0], out
    assert "test_always_red" not in "".join(reported), out
    assert "1 red on the real clock too" in out, out


def test_the_tool_exits_clean_when_no_date_breaks_anything(tmp_path):
    (tmp_path / "test_green.py").write_text("def test_g():\n    pass\n",
                                            encoding="utf-8", newline="\n")
    proc = subprocess.run(
        [sys.executable, str(Path(date_replay.__file__)), "--files",
         str(tmp_path / "test_green.py"), "--dates", "2027-06-13",
         "--workers", "0", "--python", sys.executable, "--cwd", str(tmp_path)],
        capture_output=True, text=True, timeout=240, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 date-dependent" in proc.stdout


def test_a_bad_date_is_a_usage_error_not_a_silent_real_clock_run():
    proc = subprocess.run(
        [sys.executable, str(Path(date_replay.__file__)), "--dates", "soon"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2, proc.stdout + proc.stderr
