"""Shared pytest fixtures/path setup for the server test suite.

Puts the sibling ``relay/`` repo dir on ``sys.path`` so the W3 END-TO-END test
(tests/test_remote_e2e.py) can import the REAL relay-lane package alongside the
home ``astrodeck`` package and wire them through an in-memory tunnel. The relay
package is pure Python (+ websockets, already in the venv); if the dir is absent
the e2e test ``importorskip``s.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# server/tests/conftest.py -> parents[1] is server/, parents[2] the git root.
_SERVER_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]

# The ``server/`` dir on sys.path so ``tools.mock_nina`` (server/tools/, a package
# OUTSIDE the installed ``astrodeck`` package -- setuptools find only includes
# ``astrodeck*``) imports at COLLECTION time. Without this the suite errors under
# CI's `pip install -e . && pytest` (the `pytest` console script does NOT add the
# CWD to sys.path, unlike `python -m pytest`), turning the whole server gate red.
# conftest.py is imported before the test modules in this dir, so the shim lands
# before test_nina.py's `from tools.mock_nina import ...` is collected.
_server_str = str(_SERVER_DIR)
if _server_str not in sys.path:
    sys.path.insert(0, _server_str)

_RELAY_DIR = _REPO_ROOT / "relay"
if _RELAY_DIR.is_dir():
    p = str(_RELAY_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)

# Repo root on sys.path so the standalone ``supervisor`` package (which lives at
# the repo root, outside the installed ``astrodeck`` package) imports in tests.
_root_str = str(_REPO_ROOT)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)


@pytest.fixture(autouse=True)
def _fast_sim_delays(monkeypatch):
    """Runtime seam (test-suite fast-path): collapse the simulator's hard-coded
    real-time pacing sleeps to ~0 for the WHOLE suite. ``devices.sim._sim_delay``
    reads ``ASTRODECK_FAST_TEST`` LIVE on every call, so setting it here (per
    test, via ``monkeypatch`` so it's torn down cleanly) zeroes every routed
    connect-latency / exposure-dwell / guide-pulse / status-loop / polar-sim
    wait, and since #207 the mount-slew and rotator-move dwell too. PACING
    ONLY: the sim derives every VALUE (RA/Dec offsets, star/frame pixels,
    calibration geometry, guiding corrections, slew and rotator positions)
    from the logical/virtual clock + the requested exposure/pulse or the step
    index, never from elapsed wall-clock dwell, so results stay
    bit-identical — the suite just stops paying wall-clock for the sim's
    fake time. A test that needs real dwell, or an operation still IN
    FLIGHT when it next looks (a slew, a roof close, a rotator move to halt),
    opts back OUT with ``monkeypatch.delenv("ASTRODECK_FAST_TEST")``; grep for
    it, there are several. The end-to-end timing-realism anchor is
    test_native_guider_e2e.py::test_native_guider_converges_on_sim, via its
    ``_real_dwell`` fixture."""
    monkeypatch.setenv("ASTRODECK_FAST_TEST", "1")
    yield


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_config():
    """Point the process-wide ``config_store`` at a throwaway file for the WHOLE
    session, so no test can read or write the developer's real
    ``server/config/astrodeck.json``.

    This closed two problems at once, both observed for real on 2026-07-29:

    1. A route test whose isolation helper patched only ``config.config_store``
       left ``api.app`` (which did ``from ..config import config_store`` at
       import time) writing to the REAL store. The suite went green while
       quietly persisting its fixtures — a 17.5 degree altitude floor, a -18
       degree twilight, an invented focal length — into the developer's config.
    2. Because a dozen session/resume tests read that same real store, the
       injected floor then made ~25 unrelated tests fail. The failures pointed
       at sequencing code that was completely innocent, and reproduced only on
       that one machine.

    A per-test store would be better still, but the singleton is bound by name
    in several modules; redirecting the PATH is the one move that is correct no
    matter which module a test reaches it through. Tests that want their own
    isolated store keep building one (``ConfigStore(path=tmp_path/...)``) — this
    only guarantees the shared fallback is never the real file.

    ``CONFIG_DIR`` is repointed too (2026-08-07). The store's path was patched
    but the DIRECTORY was not, and everything that resolves
    ``from ..config import CONFIG_DIR`` lazily — guider calibration/PPEC
    persistence, users.json, session_secret — still read AND WROTE the
    developer's real ``server/config/``. The observed failure took three weeks
    to fire: guider tests persisted calibrations into ``config/guider/`` in
    July; a sim pier-side fix on 2026-08-06 made the calibration-reuse gate
    match those stale files at some hours of the day and not others; and three
    convergence tests started failing by wall-clock time of day, in this
    checkout only, pointing at guiding code that was completely innocent."""
    import tempfile
    import astrodeck.config as config_mod
    from astrodeck.catalog.ephemeris import elements as elements_mod
    real = config_mod.config_store._path
    real_dir = config_mod.CONFIG_DIR
    real_elements = (elements_mod.ELEMENTS_DIR, elements_mod.SATELLITE_FILE,
                     elements_mod.COMET_FILE)
    real_start = elements_mod.EphemerisStore.start
    with tempfile.TemporaryDirectory(prefix="astrodeck-test-config-") as d:
        config_mod.config_store._path = Path(d) / "astrodeck.json"
        config_mod.config_store._cfg = None      # drop anything already loaded
        config_mod.CONFIG_DIR = Path(d)
        # THE ORBITAL-ELEMENT CACHE, for the third time in this fixture's life
        # and for the same reason (2026-09-10). ``elements.py`` computes
        # ``ELEMENTS_DIR = CONFIG_DIR / "ephemeris"`` at IMPORT, so repointing
        # CONFIG_DIR above does not move it -- and once S7L started the poller
        # in the app lifespan, any test whose TestClient outlived the 60 s check
        # interval fetched CelesTrak and the MPC and wrote 400 kB into the
        # developer's real ``server/config/ephemeris/``. That file is not inert:
        # ``catalog.search`` merges comet rows out of it, so a suite that had
        # ever run long enough got an extra row in ``search("sun")`` and three
        # catalog tests failed on a machine-specific artefact of an earlier run.
        elements_mod.ELEMENTS_DIR = Path(d) / "ephemeris"
        elements_mod.SATELLITE_FILE = elements_mod.ELEMENTS_DIR / "satellites.json"
        elements_mod.COMET_FILE = elements_mod.ELEMENTS_DIR / "comets.json"
        # ...and no poller at all. The redirect above makes the write harmless;
        # this makes the FETCH not happen, because a test suite that reaches the
        # internet fails on a train and passes at a desk. A test that wants the
        # loop calls ``_run``/``refresh`` directly, which is what the ephemeris
        # tests already do.
        elements_mod.EphemerisStore.start = lambda self: None
        assert config_mod.config_store._path != real
        yield
    config_mod.config_store._path = real
    config_mod.config_store._cfg = None
    config_mod.CONFIG_DIR = real_dir
    (elements_mod.ELEMENTS_DIR, elements_mod.SATELLITE_FILE,
     elements_mod.COMET_FILE) = real_elements
    elements_mod.EphemerisStore.start = real_start


def _config_a_reader_would_see(store) -> object:
    """What the next ``store.cfg()`` would hand a caller, as plain data,
    WITHOUT making the store load.

    A filled cache is dumped as it stands. An empty one (``_cfg is None``) is
    what a reset leaves, and there the next reader gets whatever ``_load``
    builds from the file - so that is what stands in for it: the store's own
    ``_load``, run on a scratch copy of the file and its ``.bak``, so the
    migrations and the backup recovery are the real ones and cannot drift from
    a second copy of them written here.

    Loading the store itself is the extra load this must not force. It would
    fill the cache and, for a store with no file yet, write one, so the next
    test would start from a state it was never handed - and the cold-path
    clients in ``test_framing.py`` exist to reach exactly the empty cache that
    a pre-load would take away from them.

    The decision that follows: a reset whose reload equals the old value is not
    a leak, and a reset over a file that now says something else is, because
    the next reader gets the something else. A file that no longer loads at all
    is a change too - the next reader gets a RuntimeError instead of a config -
    and so is a cache some test replaced with a thing that is not a config.
    """
    cfg = store._cfg
    if cfg is not None:
        if not hasattr(cfg, "model_dump"):
            return f"not a config ({type(cfg).__name__})"
        return cfg.model_dump(mode="json")
    import shutil
    import tempfile
    from astrodeck.config import ConfigStore
    with tempfile.TemporaryDirectory(prefix="astrodeck-config-peek-") as d:
        probe = ConfigStore(path=Path(d) / store._path.name)
        for src, dst in ((store._path, probe._path),
                         (store._bak_path(), probe._bak_path())):
            if src.is_file():
                shutil.copy2(src, dst)
        try:
            return probe._load().model_dump(mode="json")
        except Exception as exc:     # the next reader's cfg() raises this too
            return f"unloadable ({type(exc).__name__})"


def _changed_fields(before: object, after: object,
                    path: tuple[str, ...] = ()) -> list[str]:
    """The dotted names of every leaf that differs, ``safety.max_alt_deg``
    rather than ``safety``, so a report points at the write that made it. A
    list compares whole: its elements have no names to report."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        for key in sorted(set(before) | set(after), key=str):
            if key not in before or key not in after:
                out.append(".".join((*path, str(key))))
            else:
                out += _changed_fields(before[key], after[key],
                                       (*path, str(key)))
        return out
    if before == after:
        return []
    return [".".join(path) or "the whole config"]


#: The two top-level fields a test is not blamed for moving, and why. Each
#: reason holds for every test in the suite; nothing else is exempt, and a
#: change to any other field is reported even when one of these moved with it.
_NOT_A_LEAK = {
    # The save counter. Every save moves it, including the save that puts a
    # value back, so a test that restores through the product's own setter -
    # `test_polar_solve_settings.py` resets its frames block that way on both
    # sides of every test - would be blamed for leaving everything as it found
    # it. Nothing reads its absolute value: a token check compares against the
    # store's current one, which the caller reads first.
    "version",
    # A fact the HUB writes on every camera connect
    # (`ConfigStore.remember_camera_can_cool`), not anything a test asked for.
    # It changes once per worker - on the first camera to connect there - so
    # blaming it blames scheduling: both full sweeps run before this exemption
    # named 12 tests, the first to connect a camera on each of the 12 workers,
    # while a probe of a later run counted 517 tests connecting a camera on
    # the shared store. The product reads it only while no camera is
    # connected (`_camera_can_cool` in api/app.py, for the flow compile's
    # cooling advisory); the one test that grades it,
    # `test_a_run_without_a_temperature_says_so`, gives itself a private
    # store, and a test that reads it must set it.
    "camera_can_cool_seen",
}


def _config_left_as_found(nodeid: str, store=None):
    """The body of the guard below, as a plain generator so a test can drive
    it against a private store (``test_the_mount_floor_needs_a_site.py``).

    ``store`` defaults to the singleton AS IT IS NOW, and that object is the
    one compared at the end. Re-reading ``astrodeck.config.config_store`` at
    teardown would grade whatever a test left bound to the name, which is the
    one thing a leaking test is least likely to have put back.
    """
    if store is None:
        import astrodeck.config as config_mod
        store = config_mod.config_store
    before = _config_a_reader_would_see(store)
    yield
    changed = [field for field in
               _changed_fields(before, _config_a_reader_would_see(store))
               if field.split(".", 1)[0] not in _NOT_A_LEAK]
    if changed:
        # Names only, never values: this config holds the site and the auth
        # secrets, and a teardown error is printed wherever the run's output
        # goes.
        raise AssertionError(
            f"{nodeid} left the process-wide config changed: "
            f"{', '.join(changed)}. Every later test on this worker reads that "
            f"config, and fails or passes for reasons of its own (issue #227). "
            f"Write it through monkeypatch "
            f"(monkeypatch.setattr(cfg.safety, 'min_alt_deg', ...)), hand the "
            f"code under test a deep copy (cfg.model_copy(deep=True)), or give "
            f"the test its own ConfigStore(path=tmp_path / ...).")


@pytest.fixture(autouse=True)
def _a_test_leaves_the_config_as_it_found_it(request):
    """Fail, at its own teardown, any test that leaves the process-wide
    ``config_store``'s config changed (issue #227).

    ``_never_touch_the_real_config`` above keeps the suite off the developer's
    file, but it is SESSION-scoped: one store per worker, shared by every test
    that worker runs. ``test_the_mount_floor_needs_a_site.py`` wrote its
    safety limits and an unset site straight onto that store's cached config
    and never put them back. The next engine test on the same worker then ran
    under a zenith keep-out and a floor it never asked for, and aborted
    'unsafe' with 0 of 12 frames - but only when xdist drew the two files onto
    one worker AND the clock put the victim's target below the horizon, which
    it is for part of every day. The red landed on recovery code that was
    innocent, on whichever worker drew the pair.

    Compared BY VALUE, not by identity: the leak was an in-place write, which
    leaves the cached object the very same object.

    ITS TEARDOWN MUST BE THE LAST ONE, after ``monkeypatch`` has undone its
    edits, or every correctly patched test is reported as a leak. Pytest sets
    up higher-scoped fixtures first, then the autouse ones ahead of the rest,
    and the autouse fixtures of one conftest in ``dir()`` order - alphabetical
    - and tears down in reverse. So this name sorts ahead of
    ``_fast_sim_delays``, which is what brings ``monkeypatch`` in for every
    test, and it asks for no fixture that could set ``monkeypatch`` up first.
    RED under mutant "set up after monkeypatch" (renamed
    ``_z_test_leaves_the_config_as_it_found_it``): every test in
    ``test_the_mount_floor_needs_a_site.py`` that patches the config errors
    at teardown, its monkeypatch control included (observed, verbatim):

        __ ERROR at teardown of test_a_config_edit_through_monkeypatch_is_not_a_leak __
        E           AssertionError: tests/test_the_mount_floor_needs_a_site.py::
        test_a_config_edit_through_monkeypatch_is_not_a_leak left the
        process-wide config changed: safety.max_alt_deg, safety.min_alt_deg,
        site.name. ...

    The named mutant that puts #227's leak back, and the mutants of the
    helpers above, are recorded where they are graded, in that file.

    What it does not see: a module- or session-scoped fixture's edit, which is
    in place before this runs and gone after; a rebinding of the name
    ``config_store`` in some module (the #19 class), which leaves the
    singleton itself untouched; and a test that SAVES a changed value while
    the cache is put back, which leaves the file saying something the cache
    does not - harmless until some later test resets the cache without
    restoring it, and then blamed on that test.
    """
    yield from _config_left_as_found(request.node.nodeid)


@pytest.fixture(autouse=True)
def _no_inherited_focus_calibration():
    """No test starts with another test's measured sweep span.

    The fixture above is SESSION-scoped: one throwaway config dir for the whole
    worker, not one per test. That is fine for a store every test overwrites,
    and wrong for a file tests WRITE AS A SIDE EFFECT — which
    `focus_calibration.json` now is. Every successful autofocus run records the
    defocus slope it measured (focus/span.py), so any sim sweep anywhere in the
    suite leaves one behind, and the next test in that worker would size its
    sweep from it. Order-dependent, worker-dependent, and invisible: exactly the
    shape of the guider-calibration leak this fixture's neighbour documents,
    which took three weeks to surface.

    An unlink either side, so a test that wants a calibration plants its own.
    Swallowed, because a housekeeping fixture must not be able to fail a test:
    `test_session_store.py` monkeypatches `Path.unlink` itself to raise, and a
    teardown that propagated that would report an error in a test that passed.
    """
    import contextlib

    import astrodeck.config as config_mod
    path = config_mod.focus_calibration_path()
    with contextlib.suppress(Exception):
        path.unlink(missing_ok=True)
    yield
    with contextlib.suppress(Exception):
        path.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _reset_hub_singleton_locks():
    """Test-isolation seam: ``astrodeck.hub.hub`` is a process-wide singleton, but
    many tests each spin up their own ``TestClient(app)`` (own event loop) or their
    own ``asyncio.run``-driven async test (pytest-asyncio, also its own loop)
    against that SAME singleton. A plain ``asyncio.Lock`` permanently binds to
    whichever event loop first acquires it (see ``asyncio.mixins._LoopBoundMixin``)
    and never rebinds, so a lock touched by one test's loop raises
    ``RuntimeError: ... is bound to a different event loop`` when a later test's
    loop touches it again -- a cross-test-order flake, not a real bug (a real
    process only ever has one event loop for its whole lifetime). Give every test
    a fresh set of locks so the singleton never leaks loop affinity across tests."""
    import astrodeck.hub as hub_mod
    h = hub_mod.hub
    h._connect_lock = asyncio.Lock()
    h._capture_lock = asyncio.Lock()
    h._motion_lock = asyncio.Lock()
    # Same loop-affinity trap for the warm-down ramp's lock (2026-08-04). It is
    # touched by every warm/cool/teardown path, so without this the FIRST test to
    # warm a camera would poison the singleton for every later test on a
    # different loop.
    h._warm_lock = asyncio.Lock()
    # A ramp task left running by an earlier test would also outlive it, holding a
    # reference to that test's (now closed) loop and its camera double. Drop the
    # task and the state so each test starts from "no warm has ever run".
    task = h._warm_task
    if task is not None and not task.done():
        task.cancel()
    h._warm_task = None
    h._warm_state = None
    yield


@pytest.fixture
def bus_lines(monkeypatch):
    """Every ``bus.log`` line a test provokes, as ``(level, message, source)``.

    USE THIS, never ``bus.log_history[mark:]``.

    ``EventBus._history`` is a ``deque(maxlen=200)`` shared by the whole test
    process. The slice idiom takes ``mark = len(bus.log_history)`` and reads
    everything after it — which works right up until the ring reaches its cap,
    after which ``len`` is pinned at 200 and the slice is EMPTY FOREVER. The test
    then fails with nothing wrong in its own module or in the code it exercises:
    on 2026-08-01 three guide-preview tests went red because unrelated suites
    (coarse focus, the native guider) had already filled the ring on that xdist
    worker. Whether it passes depends on which worker it lands on and what ran
    before it, which is the definition of a flake.

    Capturing the call intercepts the line at its source, so nothing that
    happens elsewhere in the process can hide it — and as a bonus the test's own
    noise never enters the shared ring to break somebody else.
    """
    out: list[tuple[str, str, str]] = []
    from astrodeck import events
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub": out.append(
                            (level, message, source)))
    return out


@pytest.fixture(autouse=True, scope="session")
def _the_tree_must_not_move_under_the_run():
    """Fail loudly if an `astrodeck` source file changes while the suite runs.

    34 tests in 23 files assert on their subject's SOURCE, through
    `inspect.getsource`. That takes the LINE NUMBER from the loaded code object
    and the TEXT from the file on disk, via `linecache`, which re-reads a file
    whenever its mtime moves. So an edit during a run makes every one of them
    read a slice of the new file at the old line numbers - source belonging to
    some other part of the module (issue #118).

    Observed 2026-09-20: the suite run that overlapped two inserts near the top
    of `hub.py` failed `TestNoteSaved::test_the_capture_path_calls_it`, which
    scrapes a method 1600 lines below them. Reproduced on a throwaway module
    with no test framework: after three lines were inserted into an earlier
    function, `inspect.getsource` returned a different function's body for the
    same live object.

    The false FAIL is the expensive half - it is indistinguishable from a real
    intermittent, and it cost this session a six-run hunt for a different
    issue. The false PASS is the dangerous half: a shifted read can land on
    source that still names the call while the loaded code no longer does, so
    the guard passes while the seam is gone, which is the exact failure
    `TestNoteSaved`'s own docstring says it exists to prevent.

    Session-scoped and one place, rather than a check at each of the 34 sites:
    if nothing moved, every scrape in the run was sound, and if something did,
    no single test's verdict means anything and saying so once is the honest
    report. It runs at teardown because that is the only moment that can see
    the whole run - a mid-run check would clear a file that changes afterwards.

    In a repository where agents share one working tree, this is a normal thing
    to do by accident, which is why it gets a message rather than a shrug.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "astrodeck"
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*.py")}
    yield
    moved = sorted(
        p.relative_to(root).as_posix() for p, t in before.items()
        if not p.is_file() or p.stat().st_mtime_ns != t)
    gone = sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*.py") if p not in before)
    if moved or gone:
        raise AssertionError(
            "the source tree changed while this suite was running, so no "
            "verdict in it can be trusted - the tests that read their "
            "subject's source (inspect.getsource) were reading a file that no "
            "longer matched the code they were grading, and they can pass that "
            "way as easily as fail (issue #118). Re-run on a quiescent tree.\n"
            f"  changed: {', '.join(moved) or 'none'}\n"
            f"  appeared: {', '.join(gone) or 'none'}")
