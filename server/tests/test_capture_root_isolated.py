# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""No test writes the developer's real captures/ (#309).

The session store, the report store and the night log resolve
``hub.CAPTURE_DIR`` live, and a test that did not point it elsewhere wrote
into the repo's ``captures/``: 620 session files by 2026-09-25, most of them
two tests' leftovers, which auto-resume's armed scan and a wind-down timing
test then parsed on every run. conftest now points every capture-root seam at
a root of the test's own (``_captures_are_the_tests_own``, over a session
root, ``_never_touch_the_real_captures``), and a session guard
(``_TheRealCapturesStayUntouched``) fails the run naming any file under the
real roots that moved while it ran.

THE KNOWN POSITIVE is a probe that writes the real root behind the fixtures'
back: the guard must name it. It runs in a throwaway pytest session, because
the suite's own guard cannot be tripped from inside the suite without failing
the run that trips it; the throwaway session runs this conftest, copied, with
the real capture root pointed at a stand-in directory (the copy's own
``<repo>/captures``, and ASTRODECK_CAPTURE_DIR set to the same), so no case
here writes the real one. Its control writes through the product only and
must come back clean.

THE NAMED MUTANT "fixture disabled" (`_point_the_capture_root_at` returning
at once, conftest.py) puts back the state before #309. Every mutant was run
in a private copy of ``server/`` under the session scratchpad
(scratchpad/s3-X-mut), where the "real" root is the copy's own
``captures/``, never in the shared tree, from a byte backup, restored and
compared by SHA-256 after each run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PurePath

import pytest

# Imported here, at collection, so they bind the capture root as the process
# found it, which is the real one: that is the binding the sweep has to move.
import astrodeck
import astrodeck.api.app as app_module
import astrodeck.catalog.survey as survey_module
import astrodeck.catalog.survey_pack as survey_pack_module
import astrodeck.hub as hub_module
from astrodeck import events
from astrodeck.events import night_key
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import Session, session_store
from conftest import (  # rootdir-relative, as test_the_mount_floor_needs_a_site does
    _CAPTURE_ENV_AT_START, _REAL_CAPTURE_ROOTS, _REPO_ROOT, _CaptureRoots,
    _point_the_capture_root_at, _TheRealCapturesStayUntouched)

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"
#: The bindings made from the capture root at import, today, in modules this
#: file imports; the sweep must move each. A new one is swept without being
#: listed here; this list is the known positive that the sweep still works.
_IMPORT_TIME_BINDINGS = {
    "astrodeck.api.app.CAPTURE_DIR": (app_module, "CAPTURE_DIR", ""),
    "astrodeck.api.app._WEATHER_TILE_CACHE_DIR":
        (app_module, "_WEATHER_TILE_CACHE_DIR", "_weather_tiles"),
    "astrodeck.catalog.survey.CAPTURE_DIR": (survey_module, "CAPTURE_DIR", ""),
    "astrodeck.catalog.survey._SURVEY_CACHE_DIR":
        (survey_module, "_SURVEY_CACHE_DIR", "_survey"),
    "astrodeck.catalog.survey_pack.CAPTURE_DIR":
        (survey_pack_module, "CAPTURE_DIR", ""),
    "astrodeck.catalog.survey_pack.PACK_ROOT":
        (survey_pack_module, "PACK_ROOT", "_survey_pack"),
}


def _under_real(path: PurePath) -> bool:
    return any(path == r or path.is_relative_to(r)
               for r in (*_REAL_CAPTURE_ROOTS, *_CaptureRoots.real))


# --------------------------------------------------------------- in process

def test_every_capture_seam_points_at_the_tests_own_root(
        _captures_are_the_tests_own):
    """The root a test sees is its own and fresh, under the session's
    temporary directory and under no real root, and every seam points at
    it: ``hub.CAPTURE_DIR``, each binding made from it at import (moved by
    the sweep, which says so), ASTRODECK_CAPTURE_DIR, the session store's
    root and the night log's directory. And no loaded ``astrodeck`` module
    holds a path under a real root.

    MUTANT "fixture disabled" (`_point_the_capture_root_at` returns at
    once): RED (observed; <copy> is the scratch copy, whose own captures/
    is its "real" root):
        E   AssertionError: WindowsPath('<copy>/captures')
        E   assert WindowsPath('<copy>/captures') == WindowsPath('C:/Users/
        bear/AppData/Local/Temp/astrodeck-test-captures-zp_4cfwy/t0')
        E    +  where WindowsPath('<copy>/captures') = hub_module.CAPTURE_DIR

    MUTANT "hub only, no sweep" (the sweep loop in
    `_point_the_capture_root_at` iterates nothing): the import-time
    bindings keep the real root. RED (observed):
        E   AssertionError: bindings not moved to C:\\Users\\bear\\AppData\\
        Local\\Temp\\astrodeck-test-captures-1_l98x7q\\t0: {'astrodeck.api.
        app.CAPTURE_DIR': '<copy>\\captures', ...
        E     Left contains 6 more items:
        E     {'astrodeck.api.app.CAPTURE_DIR': '<copy>\\captures',
        E      'astrodeck.api.app._WEATHER_TILE_CACHE_DIR': '<copy>\\captures
        \\_weather_tiles',
        E      'astrodeck.catalog.survey.CAPTURE_DIR': '<copy>\\captures', ...
    """
    root = _captures_are_the_tests_own
    assert hub_module.CAPTURE_DIR == root, hub_module.CAPTURE_DIR
    parent = _CaptureRoots.parent
    assert parent is not None
    assert root.parent == parent and root.name.startswith("t"), root
    assert not _under_real(root), root
    assert root.is_dir(), root
    assert list(root.iterdir()) == [], (
        f"a test's root must start empty: {sorted(root.iterdir())}")

    missed = {label: str(getattr(mod, attr))
              for label, (mod, attr, rel) in _IMPORT_TIME_BINDINGS.items()
              if getattr(mod, attr) != root / rel}
    assert missed == {}, f"bindings not moved to {root}: {missed}"
    unmoved = set(_IMPORT_TIME_BINDINGS) - set(_CaptureRoots.moved)
    assert unmoved == set(), (
        f"the sweep did not say it moved {sorted(unmoved)}; it moved "
        f"{_CaptureRoots.moved}")

    leaks = [f"{name}.{attr}"
             for name, mod in list(sys.modules.items())
             if mod is not None and name.startswith("astrodeck")
             for attr, value in list(vars(mod).items())
             if isinstance(value, PurePath) and _under_real(value)]
    assert leaks == [], f"modules still bound to a real root: {leaks}"

    assert os.environ["ASTRODECK_CAPTURE_DIR"] == str(root)
    assert session_store._path("probe").parent == root / "sessions"
    assert events.bus.night_log is not None
    assert events.bus.night_log.dir() == root / "logs"


def test_what_a_test_saves_lands_in_its_own_root(_captures_are_the_tests_own):
    """The two leaks #309 found, done the way the leaking tests did them,
    with no redirect of the test's own: a session saved through the store
    (the plan "p" writer in test_abort_stays_aborted.py) and a bus log line
    (the night log). Both land in the test's root and neither at the real
    root's path for the same file.

    MUTANT "fixture disabled": RED (observed), and the session file and the
    night log line went into the scratch copy's own ``captures/``, which is
    what the shared tree's real one got for months; that run's own guard
    then named both:
        E   AssertionError: the session is not in the test's root: C:\\Users
        \\bear\\AppData\\Local\\Temp\\astrodeck-test-captures-zp_4cfwy\\t1\\
        sessions\\92351294b4294827ae422143d2f752aa.json
        ...
        ===== A TEST RUN WROTE INTO THE REAL captures/ (issue #309) =====
        ...
          created (2): <copy>/captures/logs/2026-09-25.jsonl, <copy>/
          captures/sessions/92351294b4294827ae422143d2f752aa.json
    """
    root = _captures_are_the_tests_own
    s = Session(name="isolation probe", status="active",
                plan=SequencePlan(name="isolation probe"))
    session_store.save(s)
    events.bus.log("info", "isolation probe line", "hub")
    events.flush_night_logs()                   # the file is written off-thread
    mine = root / "sessions" / f"{s.id}.json"
    assert mine.is_file(), f"the session is not in the test's root: {mine}"
    for real in _REAL_CAPTURE_ROOTS:
        assert not (real / "sessions" / f"{s.id}.json").exists(), real
    log = root / "logs" / f"{night_key()}.jsonl"
    assert log.is_file() and "isolation probe line" in log.read_text(
        encoding="utf-8"), log


def test_the_night_log_writes_into_every_new_root(
        _captures_are_the_tests_own, tmp_path):
    """The night log keeps writing when the seams move to a root it has not
    written yet: a line goes into this test's root, the seams move on to a
    second root (as they do between tests), and the next line lands there
    with the writer not paused. `_point_the_capture_root_at` gives every
    root a fresh `NightLogWriter`, because the writer creates ``logs/`` only
    when the night changes: a writer carried over fails its first write into
    the new root, pauses for NIGHTLOG_RETRY_S, and publishes its "paused"
    line into whatever test comes next.

    MUTANT "the writer carried over" (the fresh ``NightLogWriter`` in
    `_point_the_capture_root_at` deleted): the process's one writer had
    already written tonight's line into an earlier test's root, so even the
    FIRST line here is lost; a writer that had not written yet would pass
    the first assert and pause on the second root instead. RED (observed,
    this file alone, -n0, in the scratch copy):
        E   AssertionError: []
        E   assert False
        E    +  where False = is_file()
        E    +    where is_file = WindowsPath('C:/Users/bear/AppData/Local/
        Temp/astrodeck-test-captures-swzm71lz/t2/logs/2026-09-25.jsonl').is_file
    """
    root = _captures_are_the_tests_own
    events.bus.log("info", "first root line", "hub")
    events.flush_night_logs()                   # the file is written off-thread
    first = root / "logs" / f"{night_key()}.jsonl"
    assert first.is_file(), sorted(p.name for p in root.rglob("*"))
    second = tmp_path / "second root"
    with pytest.MonkeyPatch.context() as mp:
        _point_the_capture_root_at(mp, second)
        events.bus.log("info", "second root line", "hub")
        events.flush_night_logs()               # the file is written off-thread
        writer = events.bus.night_log
        assert writer is not None and not writer.failed, (
            "the night log paused on its first write into the new root: "
            f"{writer.take_notice() if writer else None}")
        log = second / "logs" / f"{night_key()}.jsonl"
        assert log.is_file() and "second root line" in log.read_text(
            encoding="utf-8"), sorted(p.name for p in second.rglob("*"))
    assert "second root line" not in first.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def _the_root_a_module_fixture_sees():
    """What a module-scoped fixture sees as the capture root: it is set up
    before any test's own fixtures, so only the session fixture covers it."""
    return Path(hub_module.CAPTURE_DIR)


def test_a_module_fixture_sees_the_sessions_root_not_the_real_one(
        _the_root_a_module_fixture_sees):
    """A module- or session-scoped fixture, set up before any test's own
    root exists, already sees the session's temporary root
    (``_never_touch_the_real_captures``), never a real one: what such a
    fixture saves cannot reach the developer's ``captures/``.

    MUTANT "the session fixture does not point" (the
    `_point_the_capture_root_at` call in ``_never_touch_the_real_captures``
    deleted; the per-test fixture still works, so every other case in this
    file stays green, which is how this case came to be written): RED
    (observed; <copy> is the scratch copy, whose own captures/ is its
    "real" root):
        E   AssertionError: WindowsPath('<copy>/captures')
        E   assert WindowsPath('<copy>/captures') == (WindowsPath('C:/Users/
        bear/AppData/Local/Temp/astrodeck-test-captures-ruk_hobg') / 'session')
        E    +  where WindowsPath('C:/Users/bear/AppData/Local/Temp/astrodeck-
        test-captures-ruk_hobg') = _CaptureRoots.parent
    """
    seen = _the_root_a_module_fixture_sees
    assert _CaptureRoots.parent is not None
    assert seen == _CaptureRoots.parent / "session", seen
    assert not _under_real(seen), seen


def test_the_guard_watches_the_repos_captures(request):
    """The guard this run registered watches the repo's ``captures/``, and
    the capture root the product computes by default is among the roots
    the fixtures protect, so the two cannot drift apart.

    MUTANT "guard never registered" (its ``register`` call in conftest's
    `pytest_configure` replaced by ``pass``): RED (observed):
        E   AssertionError: None
        E   assert False
        E    +  where False = isinstance(None, _TheRealCapturesStayUntouched)
    """
    guard = request.config.pluginmanager.get_plugin("astrodeck-captures-guard")
    assert isinstance(guard, _TheRealCapturesStayUntouched), guard
    assert _REPO_ROOT / "captures" in guard.roots, guard.roots
    # hub.py's own rule for its default, applied to the hub.py this run
    # imported: the guard, which never imports it, must be watching that.
    default = (Path(_CAPTURE_ENV_AT_START).resolve() if _CAPTURE_ENV_AT_START
               else Path(hub_module.__file__).resolve().parents[2] / "captures")
    assert default in guard.roots, (default, guard.roots)
    assert default in _CaptureRoots.real, (default, _CaptureRoots.real)


def test_the_guard_names_what_was_created_changed_and_removed(tmp_path):
    """The guard's own comparison, on a stand-in root: a file created, a
    file appended to (the night log's leak was appends) and a file removed
    (a store over its quota prunes) are each named, by root and relative
    path, and an untouched file is not. CONTROL: a second listing with
    nothing moved names nothing.

    MUTANT "created only" (`moved` returns empty changed and removed):
    RED (observed):
        E   AssertionError: []
        E   assert [] == ['C:/Users/be...-09-25.jsonl']
        E     Right contains one more item: 'C:/Users/bear/AppData/Local/Temp/
        pytest-of-bear/pytest-19096/test_the_guard_names_what_was_0/captures/
        logs/2026-09-25.jsonl'
    """
    root = tmp_path / "captures"
    (root / "sessions").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "sessions" / "kept.json").write_text("{}", encoding="utf-8")
    (root / "sessions" / "pruned.json").write_text("{}", encoding="utf-8")
    night = root / "logs" / "2026-09-25.jsonl"
    night.write_text("{}\n", encoding="utf-8")
    guard = _TheRealCapturesStayUntouched((root,))
    before = guard.listing()

    assert guard.moved(before, guard.listing()) == ([], [], [])

    (root / "sessions" / "leaked.json").write_text("{}", encoding="utf-8")
    with night.open("a", encoding="utf-8") as fh:
        fh.write('{"a leaked line": 1}\n')
    (root / "sessions" / "pruned.json").unlink()
    created, changed, removed = guard.moved(before, guard.listing())
    base = root.as_posix()
    assert created == [f"{base}/sessions/leaked.json"], created
    assert changed == [f"{base}/logs/2026-09-25.jsonl"], changed
    assert removed == [f"{base}/sessions/pruned.json"], removed
    body = guard.report(created, changed, removed)
    assert "  created (1): " + created[0] in body, body


# ------------------------------------------- a throwaway run of the conftest

_LEAK_PROBE = '''\
from pathlib import Path


def test_writes_the_real_root_behind_the_fixtures_back():
    real = Path(__file__).resolve().parents[2] / "captures"
    (real / "sessions" / "probe-leak.json").write_text("{}", encoding="utf-8")
'''

_PRODUCT_PROBE = '''\
import astrodeck.hub as hub
from astrodeck.events import bus
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import Session, session_store

SEEN = []


def test_writes_only_through_the_product():
    s = Session(name="probe", status="active", plan=SequencePlan(name="probe"))
    session_store.save(s)
    assert (hub.CAPTURE_DIR / "sessions" / f"{s.id}.json").is_file()
    bus.log("info", "probe line", "hub")
    (hub.CAPTURE_DIR / "marker.txt").write_text("first", encoding="utf-8")
    SEEN.append(hub.CAPTURE_DIR)


def test_the_next_test_starts_from_a_root_of_its_own():
    assert SEEN and hub.CAPTURE_DIR != SEEN[0], (hub.CAPTURE_DIR, SEEN)
    assert not (hub.CAPTURE_DIR / "marker.txt").exists()
'''


def _throwaway_run(tmp_path: Path, probe: str
                   ) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run ``probe`` under a copy of this conftest in a throwaway tree laid
    out like the repo (``repo/server/tests``), whose ``repo/captures`` stands
    in for the real root and already holds a session and a night log. The
    child imports the same ``astrodeck`` as this process. Returns the
    completed run and the stand-in root."""
    repo = tmp_path / "repo"
    tests = repo / "server" / "tests"
    tests.mkdir(parents=True)
    real = repo / "captures"
    (real / "sessions").mkdir(parents=True)
    (real / "logs").mkdir()
    (real / "sessions" / "existing.json").write_text("{}", encoding="utf-8")
    (real / "logs" / "2026-01-01.jsonl").write_text("{}\n", encoding="utf-8")
    (tests / "conftest.py").write_bytes(_CONFTEST.read_bytes())
    (tests / "test_probe.py").write_text(probe, encoding="utf-8")
    ini = repo / "server" / "pytest.ini"
    ini.write_text("[pytest]\naddopts =\n", encoding="utf-8")
    # Nothing of the run that is running THIS test tells the inner run what
    # it is (an xdist worker's variables, PYTEST_ADDOPTS).
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_")}
    env["ASTRODECK_CAPTURE_DIR"] = str(real)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(astrodeck.__file__).resolve().parents[1]),
         *filter(None, [env.get("PYTHONPATH")])])
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "0",
         "-q", "-c", str(ini), "--rootdir", str(repo / "server"),
         str(tests / "test_probe.py")],
        cwd=repo / "server", env=env, capture_output=True, text=True,
        timeout=300)
    return done, real


@pytest.mark.integration
def test_a_probe_that_writes_the_real_root_is_named(tmp_path):
    """THE KNOWN POSITIVE. The probe passes, and writes a session file into
    the real root by a path it computes for itself, as no fixture can
    catch. The guard fails the run (exit 1) and names that file, and only
    that file: the stand-in root's existing session and night log are not
    named.

    MUTANT "guard never registered": the probe's write goes unreported.
    RED (observed):
        E   AssertionError: .                                          [100%]
        E     1 passed in 0.81s
        E   assert 'A TEST RUN WROTE INTO THE REAL captures/ (issue #309)'
        in '.                                                          [100%]
        \\n1 passed in 0.81s\\n'
    """
    done, real = _throwaway_run(tmp_path, _LEAK_PROBE)
    out = done.stdout + done.stderr
    assert "1 passed" in out, out
    assert _TheRealCapturesStayUntouched.HEADLINE in out, out
    assert (f"created (1): {real.resolve().as_posix()}/sessions/probe-leak.json"
            in out), out
    assert "changed (0): none" in out and "removed (0): none" in out, out
    assert done.returncode == 1, (done.returncode, out)


@pytest.mark.integration
def test_control_a_run_that_writes_through_the_product_is_clean(tmp_path):
    """CONTROL. A run whose tests write only through the product (a session
    saved to the store, a bus log line, a file under ``hub.CAPTURE_DIR``)
    leaves the real root as it found it: exit 0 and no verdict. The second
    test also sees a root that is not the first test's and holds none of
    its files.

    MUTANT "fixture disabled": both writes land in the stand-in root, the
    second probe sees the first's root, and the guard names all three files.
    RED (observed; <tmp> is this test's tmp_path):
        E   AssertionError: .F                                         [100%]
        E     ___ test_the_next_test_starts_from_a_root_of_its_own ___
        E     >       assert SEEN and hub.CAPTURE_DIR != SEEN[0], ...
        ...
        E       created (3): <tmp>/repo/captures/logs/2026-09-25.jsonl,
        <tmp>/repo/captures/marker.txt, <tmp>/repo/captures/sessions/
        d26bb2f6c8ab4efc91fc3c1ba8cd6dc3.json
    """
    done, real = _throwaway_run(tmp_path, _PRODUCT_PROBE)
    out = done.stdout + done.stderr
    assert _TheRealCapturesStayUntouched.HEADLINE not in out, out
    assert "2 passed" in out, out
    assert done.returncode == 0, (done.returncode, out)
    assert sorted(p.relative_to(real).as_posix()
                  for p in real.rglob("*") if p.is_file()) == [
        "logs/2026-01-01.jsonl", "sessions/existing.json"]
