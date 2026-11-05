# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Date replay: the Sun cone, file ages and the config guard (#1120: #1069,
#1070, #807).

The suite run as if on another date (``tests/tools/shift_clock.py``) must pass,
so a test cannot depend on today's sky, on file ages, or on the order of
fixtures.

* ``_sky.target()`` (#1069): a target at least 60 deg from the Sun and 15 deg
  up at the made-up test site on every day and hour, and one that
  ``_SunConeGuard`` does not call seasonal. Replaces the RA 5 h Dec +10 that
  the Sun sits within 30 deg of from early May to early July.
* ``conftest._ReplayedFileTimes`` (#1070): ``os.stat`` reads a file's time on
  the replayed clock, so a file written now reads as new and a file a test aged
  reads as aged, under a replay as under none. The write-then-age cases run
  everywhere; they only mean something under ``-p shift_clock``, which the
  last case in this group does in a child session.
* The config guard (#807): a fixture that asks for ``monkeypatch`` ahead of the
  guard no longer makes every correctly patched test a leak, and a real leak is
  still named. And a reach of the real config made outside any test's window
  (a module-scoped fixture) fails the session at its end (#1120).

NAMED MUTANT, run from a byte backup of conftest.py and restored by byte copy
(sha256 compared): ``_ReplayedFileTimes.read`` ending ``return st`` in place of
``return self.shifted(st)`` (the stat shift dropped). Under ``ASTRODECK_SHIFT_CLOCK_S`` for 2026-06-15 the
write-then-age cases fail (observed, verbatim, on the 4 that write a file and
read its age):

    E   AssertionError: assert 12301804.0 < 60.0

which is the age of a file written a moment ago: the whole offset. The class
cases and ``test_a_replayed_session_reads_file_ages_on_the_replayed_clock``
fail in an ordinary run, on the child's output and on the offset.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import _sky
import astrodeck
from _simhub import _TEST_SITE
from _sun_cone import seasonal
from astrodeck.catalog import coords
from conftest import _ReplayedFileTimes

_TESTS = Path(__file__).resolve().parent
_SERVER = _TESTS.parent
_CONFTEST = _TESTS / "conftest.py"
_SOUTH = (-50.0, 150.0)


# ----------------------------------------------------------------------- sky


def _hours_of_2026():
    first = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    return [first + 3600.0 * h for h in range(0, 365 * 24, 5)]


def test_the_target_is_clear_of_the_sun_and_up_at_every_hour_of_a_year():
    """The Sun is on the ecliptic and the target is its pole: 90 deg away on
    every day, and (at the made-up site) never below 16.5 deg."""
    seen = set()
    for when in _hours_of_2026():
        ra, dec = _sky.target(when)
        seen.add((ra, dec))
        sun = coords.sun_radec(when)
        assert coords.angular_sep_deg(ra, dec, *sun) >= _sky.MIN_SUN_SEP_DEG
        alt = coords.altaz(ra, dec, _TEST_SITE.latitude, _TEST_SITE.longitude,
                           when)[0]
        assert alt >= _sky.MIN_ALT_DEG, (when, alt)
    assert seen == {_sky.POLES[0]}


def test_the_target_is_not_one_the_sun_cone_guard_calls_seasonal():
    for pole in _sky.POLES:
        assert seasonal(*pole, 30.0) is False


def test_the_replayed_clock_is_the_one_asked(monkeypatch):
    """With no time given the target is chosen at the clock now, which under
    shift_clock is the replayed one: a clock read of 2026-06-15 asks the Sun
    where it is in June."""
    june = dt.datetime(2026, 6, 15, 12, tzinfo=dt.timezone.utc).timestamp()
    asked = []
    real = coords.sun_radec

    def sun(unix_time=None):
        asked.append(unix_time)
        return real(unix_time)

    monkeypatch.setattr(coords, "sun_radec", sun)
    monkeypatch.setattr(time, "time", lambda: june)
    _sky.target()
    assert asked == [june]


def test_a_southern_site_is_given_the_south_pole():
    for when in _hours_of_2026()[::40]:
        assert _sky.target(when, latitude=_SOUTH[0],
                           longitude=_SOUTH[1]) == _sky.POLES[1]


def test_a_site_where_no_pole_stands_high_enough_is_refused():
    """At the equator a pole is up 23 deg at best and below the horizon for
    half a day: the helper says so, not hands back a target the mount cannot
    reach."""
    outcomes = set()
    for when in _hours_of_2026()[:60]:
        try:
            ra, dec = _sky.target(when, latitude=0.0, longitude=0.0)
        except AssertionError as refused:
            assert "no ecliptic pole" in str(refused)
            outcomes.add("refused")
        else:
            assert coords.altaz(ra, dec, 0.0, 0.0, when)[0] >= _sky.MIN_ALT_DEG
            outcomes.add("target")
    assert outcomes == {"refused", "target"}


# ---------------------------------------------------------------- file times

#: Within a minute of the age the test gave the file.
_SLACK_S = 60.0


def _age(path: Path) -> float:
    return time.time() - path.stat().st_mtime


def test_a_file_written_now_reads_as_new(tmp_path):
    """Under a replay months ahead of the real clock, the OS stamps the file
    with the real time, and ``time.time()`` reads the replayed one."""
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    assert abs(_age(path)) < _SLACK_S
    assert abs(time.time() - os.path.getmtime(path)) < _SLACK_S


def test_a_file_a_test_aged_reads_as_that_age(tmp_path):
    """Write, then age: the shape of the dusk-flats freshness rule and of the
    solve-frame sweep. The stamp is the test's own number, read back whole."""
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    three_days = 3 * 86400.0
    old = time.time() - three_days
    os.utime(path, (old, old))
    assert abs(_age(path) - three_days) < _SLACK_S


def test_a_file_stamped_and_then_written_again_reads_as_new(tmp_path):
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    old = time.time() - 3 * 86400.0
    os.utime(path, (old, old))
    path.write_bytes(b"y")
    assert abs(_age(path)) < _SLACK_S


def test_a_stamped_file_that_is_moved_keeps_its_stamp(tmp_path):
    """The gallery trash renames a frame with its times (test_gallery.py)."""
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    two_months = 60 * 86400.0
    old = time.time() - two_months
    os.utime(path, (old, old))
    moved = tmp_path / "trash" / "frame.fits"
    moved.parent.mkdir()
    os.replace(path, moved)
    assert abs(_age(moved) - two_months) < _SLACK_S


def test_a_listing_reads_the_same_age_as_a_stat(tmp_path):
    """``os.scandir`` entries are what the gallery and the report sweep read.
    A test that reads one file through ``Path.stat`` and the code under test
    through a listing must agree."""
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    with os.scandir(tmp_path) as listing:
        [entry] = list(listing)
    assert abs(time.time() - entry.stat().st_mtime) < _SLACK_S
    assert entry.stat().st_mtime == path.stat().st_mtime
    assert [e.name for e in os.scandir(tmp_path)] == ["frame.fits"]
    assert [name for _, _, names in os.walk(tmp_path) for name in names] == [
        "frame.fits"]


@pytest.mark.real_file_times
def test_a_marked_test_reads_the_file_times_the_os_stamped(tmp_path):
    """A test that hands files to a child process (the gallery cache, keyed on
    file times) is not shifted; ``os.fstat`` is never shifted, so it is the
    reference whether or not this run is replayed."""
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    with os.scandir(tmp_path) as listing:
        [entry] = list(listing)
    with open(path, "rb") as handle:
        raw = os.fstat(handle.fileno())
    assert path.stat().st_mtime_ns == raw.st_mtime_ns
    assert entry.stat().st_mtime_ns == raw.st_mtime_ns


def test_touching_a_stamped_file_makes_it_new_again(tmp_path):
    path = tmp_path / "frame.fits"
    path.write_bytes(b"x")
    old = time.time() - 3 * 86400.0
    os.utime(path, (old, old))
    os.utime(path)
    assert abs(_age(path)) < _SLACK_S


# The class itself, with an offset of its own, in this process. Differences
# are taken against the stat the class wrapped, so the cases hold whether or
# not this session is itself under a replay.

_OFFSET_S = 1.0e7


@pytest.fixture
def shim(tmp_path):
    """A ``_ReplayedFileTimes`` over ``tmp_path`` and what it wrapped."""
    times = _ReplayedFileTimes(_OFFSET_S, [tmp_path])
    times.install()
    try:
        yield times, times._real[0], times._real[1], times._real[3]
    finally:
        times.uninstall()


def test_a_file_under_the_root_reads_the_offset_later(shim, tmp_path):
    times, raw_stat, raw_lstat, _ = shim
    path = tmp_path / "a"
    path.write_bytes(b"x")
    for read, raw in ((os.stat, raw_stat), (os.lstat, raw_lstat),
                      (lambda p: Path(p).stat(), raw_stat)):
        got, was = read(path), raw(path)
        assert got.st_mtime - was.st_mtime == pytest.approx(_OFFSET_S)
        assert got.st_atime - was.st_atime == pytest.approx(_OFFSET_S)
        assert got.st_ctime - was.st_ctime == pytest.approx(_OFFSET_S)
        assert got.st_mtime_ns - was.st_mtime_ns == round(_OFFSET_S * 1e9)
        assert got[8] - was[8] == int(_OFFSET_S)
        assert (got.st_size, got.st_mode, got.st_ino) == (
            was.st_size, was.st_mode, was.st_ino)


def test_every_spelling_of_a_path_under_the_root_is_shifted(shim, tmp_path):
    times, raw_stat, _, _ = shim
    path = tmp_path / "a"
    path.write_bytes(b"x")
    was = raw_stat(path).st_mtime
    for spelling in (str(path), os.fsencode(path), path,
                     str(tmp_path / "." / "a")):
        assert os.stat(spelling).st_mtime - was == pytest.approx(_OFFSET_S)


def test_a_file_outside_the_root_and_a_descriptor_are_not_shifted(
        shim, tmp_path):
    times, raw_stat, _, _ = shim
    assert os.stat(__file__).st_mtime == raw_stat(__file__).st_mtime
    path = tmp_path / "a"
    path.write_bytes(b"x")
    with open(path, "rb") as handle:
        assert os.stat(handle.fileno()).st_mtime_ns == os.fstat(
            handle.fileno()).st_mtime_ns


def test_a_listing_entry_is_shifted_like_a_stat_and_only_under_the_root(
        shim, tmp_path):
    times, raw_stat, _, raw_scandir = shim
    path = tmp_path / "a"
    path.write_bytes(b"x")
    with os.scandir(tmp_path) as listing:
        [entry] = list(listing)
    assert entry.name == "a" and entry.is_file() and os.fspath(entry) == str(path)
    assert entry.stat().st_mtime - raw_stat(path).st_mtime == pytest.approx(
        _OFFSET_S)
    outside = os.scandir(Path(__file__).parent)
    assert type(outside) is type(raw_scandir(Path(__file__).parent))
    outside.close()


def test_a_stamp_is_read_back_whole_and_a_rewrite_is_shifted_again(
        shim, tmp_path):
    times, raw_stat, _, _ = shim
    path = tmp_path / "a"
    path.write_bytes(b"x")
    os.utime(path, (1_700_000_000.0, 1_700_000_000.0))
    assert os.stat(path).st_mtime == pytest.approx(1_700_000_000.0)
    os.utime(path, ns=(1_700_000_001 * 10**9, 1_700_000_001 * 10**9))
    assert os.stat(path).st_mtime == pytest.approx(1_700_000_001.0)
    path.write_bytes(b"y")
    assert os.stat(path).st_mtime - raw_stat(path).st_mtime == pytest.approx(
        _OFFSET_S)
    os.utime(path, (1_700_000_000.0, 1_700_000_000.0))
    os.utime(path)
    assert os.stat(path).st_mtime - raw_stat(path).st_mtime == pytest.approx(
        _OFFSET_S)


def test_uninstalling_puts_every_function_back(tmp_path):
    before = (os.stat, os.lstat, os.utime)
    times = _ReplayedFileTimes(_OFFSET_S, [tmp_path])
    times.install()
    assert (os.stat, os.lstat, os.utime) != before
    times.uninstall()
    assert (os.stat, os.lstat, os.utime) == before


# ----------------------------------------------- a replayed session, end to end


def _run(args, env_extra=None, cwd=_SERVER):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")
           and k != "ASTRODECK_SHIFT_CLOCK_S"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "pytest", "-p",
                           "no:cacheprovider", "-n", "0", "-q", *args],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=300)


@pytest.mark.parametrize("days", [165, -142], ids=["ahead", "behind"])
def test_a_replayed_session_reads_file_ages_on_the_replayed_clock(days):
    """The write-then-age cases above, in a child session with the
    plugin loaded and the clock moved by months. Without the stat shift each
    reads the whole offset as its file's age."""
    env = {"ASTRODECK_SHIFT_CLOCK_S": str(days * 86400),
           "PYTHONPATH": os.pathsep.join([str(_TESTS / "tools"),
                                          str(_SERVER)])}
    done = _run(["-p", "shift_clock", "-k",
                 "reads_as_new or reads_as_that_age or makes_it_new_again "
                 "or keeps_its_stamp or reads_the_same_age or marked_test",
                 str(Path(__file__))], env)
    out = done.stdout + done.stderr
    assert "shift_clock: wall clock" in out, out
    assert "7 passed" in out and done.returncode == 0, out


# ------------------------------------------------------------- the config guard

_PROBE_ORDER = '''\
import pytest

import astrodeck.config as config_mod


def test_a_field_patched_through_monkeypatch_is_not_a_leak(monkeypatch):
    cfg = config_mod.config_store.cfg()
    monkeypatch.setattr(cfg.safety, "min_alt_deg", 33.0)
    assert config_mod.config_store.cfg().safety.min_alt_deg == 33.0


def test_a_field_written_in_place_is_still_a_leak():
    config_mod.config_store.cfg().safety.min_alt_deg = 44.0
'''

#: An autouse fixture that sorts ahead of the guard and asks for monkeypatch:
#: what a plugin's replay fixture did (#807).
_FIRST_FIXTURE = '''

@pytest.fixture(autouse=True)
def __first(monkeypatch):
    yield
'''

_PROBE_MODULE_FIXTURE = '''\
import pytest

from astrodeck.config import ConfigStore


@pytest.fixture(scope="module")
def reaches_the_real_config():
    return ConfigStore().cfg()


def test_that_asks_for_it(reaches_the_real_config):
    assert reaches_the_real_config is not None
'''

_PROBE_CLEAN = '''\
def test_that_reaches_nothing():
    assert True
'''


def _session(tmp_path: Path, probe: str, conftest: bytes) -> str:
    """Run ``probe`` under ``conftest`` in a throwaway tree laid out like the
    repo, with ``ASTRODECK_CONFIG_DIR`` on an empty directory standing for the
    developer's. Returns the child's output."""
    repo = tmp_path / "repo"
    tests = repo / "server" / "tests"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_bytes(conftest)
    (tests / "test_probe.py").write_text(probe, encoding="utf-8")
    ini = repo / "server" / "pytest.ini"
    ini.write_text("[pytest]\naddopts =\n", encoding="utf-8")
    real = tmp_path / "real-config"
    real.mkdir()
    done = _run(["-c", str(ini), "--rootdir", str(repo / "server"),
                 str(tests / "test_probe.py")],
                {"ASTRODECK_CONFIG_DIR": str(real),
                 "ASTRODECK_CAPTURE_DIR": str(repo / "captures"),
                 "PYTHONPATH": str(Path(astrodeck.__file__).resolve().parents[1])},
                cwd=repo / "server")
    return done.stdout + done.stderr


def test_a_fixture_ahead_of_the_guard_that_asks_for_monkeypatch_is_no_leak(
        tmp_path):
    """#807, in a throwaway session. The patched field is put back before the
    guard compares, wherever ``monkeypatch`` is first asked for; the field
    written in place is named, with the test and the field and no value."""
    out = _session(tmp_path, _PROBE_ORDER,
                   _CONFTEST.read_bytes() + _FIRST_FIXTURE.encode("ascii"))
    assert "ERROR at teardown of test_a_field_patched_through_monkeypatch" \
        not in out, out
    assert ("ERROR at teardown of test_a_field_written_in_place_is_still_a_leak"
            in out), out
    assert "left the process-wide config changed: safety.min_alt_deg" in out, out
    assert "44.0" not in out
    assert "2 passed" in out and "1 error" in out, out


def test_a_reach_of_the_real_config_outside_any_test_fails_the_session(
        tmp_path):
    """A module-scoped fixture builds a store with no path, which is the
    developer's own file. No test's window is open while it sets up, so no
    test is blamed, and the test that asks for it passes; the session fixture
    names the seam at the end, and never a value."""
    out = _session(tmp_path, _PROBE_MODULE_FIXTURE, _CONFTEST.read_bytes())
    assert ("ERROR at teardown of test_that_asks_for_it" in out), out
    assert ("reached outside any test's guard window: ConfigStore" in out), out
    assert "1 passed" in out and "1 error" in out, out


def test_a_session_that_reaches_nothing_is_clean(tmp_path):
    out = _session(tmp_path, _PROBE_CLEAN, _CONFTEST.read_bytes())
    assert "1 passed" in out and "error" not in out, out
