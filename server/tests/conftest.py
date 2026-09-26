"""Shared pytest fixtures/path setup for the server test suite.

Puts the sibling ``relay/`` repo dir on ``sys.path`` so the W3 END-TO-END test
(tests/test_remote_e2e.py) can import the REAL relay-lane package alongside the
home ``astrodeck`` package and wire them through an in-memory tunnel. The relay
package is pure Python (+ websockets, already in the venv); if the dir is absent
the e2e test ``importorskip``s.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import shutil
import sys
from pathlib import Path, PurePath

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

#: The capture roots no test may write under (#309): the repo's captures/,
#: which is ``hub.CAPTURE_DIR``'s default, and ASTRODECK_CAPTURE_DIR when
#: the run was started with it. Read here, at conftest import, before any
#: fixture has set that variable for a test.
_CAPTURE_ENV_AT_START = (os.environ.get("ASTRODECK_CAPTURE_DIR") or "").strip()
_REAL_CAPTURE_ROOTS: tuple[Path, ...] = tuple(dict.fromkeys(
    [_REPO_ROOT / "captures"]
    + ([Path(_CAPTURE_ENV_AT_START).resolve()] if _CAPTURE_ENV_AT_START
       else [])))


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


class _CaptureRoots:
    """Where the capture root is while the suite runs (#309); state shared by
    the two fixtures below and read by test_capture_root_isolated.py."""

    #: The capture roots as this process found them, which no test may
    #: write under: `_REAL_CAPTURE_ROOTS` and ``hub.CAPTURE_DIR`` as it was
    #: before the session fixture moved it.
    real: tuple[Path, ...] = ()
    #: The temporary directory every capture root of this session is made
    #: in: ``session`` for the session's own, ``t<n>`` for each test's.
    #: None outside a session.
    parent: Path | None = None
    serial = itertools.count()
    #: ``module.attr`` of every binding the last sweep moved, so a test can
    #: assert that the sweep took rather than trust it.
    moved: list[str] = []
    #: module name -> (the module, the names in it that held a path when it
    #: was first swept). Reading every global of every module for every
    #: test cost 1.2 ms a test; a module is read whole once, and again only
    #: if it is re-imported. What a cached module could hide is a global
    #: that becomes a path later, and that path would be built from
    #: ``hub.CAPTURE_DIR``, which during a session never names a real root:
    #: at worst a stale temporary root, never the developer's captures.
    path_names: dict[str, tuple[object, tuple[str, ...]]] = {}


def _under_a_capture_root(value: PurePath) -> PurePath | None:
    """``value``'s path below the capture root it sits in, or None when it
    is under none. A root is a real one, or any root this session handed
    out: a module first imported during a test binds that test's root, and
    must be moved on like one that bound the real root."""
    for root in _CaptureRoots.real:
        if value == root or value.is_relative_to(root):
            return value.relative_to(root)
    parent = _CaptureRoots.parent
    if parent is not None and value.is_relative_to(parent):
        parts = value.relative_to(parent).parts
        if parts:
            return PurePath(*parts[1:])
    return None


def _point_the_capture_root_at(mp: pytest.MonkeyPatch, root: Path) -> list[str]:
    """Point every capture-root seam at ``root``, through ``mp``, and return
    the bindings moved.

    * ``hub.CAPTURE_DIR``, which everything that resolves the root at call
      time reads: the session store (``sessions/``), the report store, the
      night log, the gallery, the frame counter, the fingerprint.
    * Every binding made from it at import: ``from ..hub import
      CAPTURE_DIR`` and paths built on it (``PACK_ROOT``,
      ``_SURVEY_CACHE_DIR``, ``_WEATHER_TILE_CACHE_DIR`` today). Found by
      SWEEPING the loaded ``astrodeck`` modules for a path under a capture
      root, never by a list: a list is what the #227 class keeps getting
      wrong (see test_isolation_of_module_singletons), and a module added
      tomorrow is swept without anyone remembering this exists.
    * ASTRODECK_CAPTURE_DIR, so a server a test starts in a child process
      resolves the same root.
    * The bus's night-log writer. It remembers the night it last wrote and
      creates ``logs/`` only when the night changes, so moved to a fresh
      root it would fail its first write, pause, and publish a "paused"
      line into whatever test came next. A fresh writer per root is the
      writer a fresh process would have."""
    import astrodeck.hub as hub_mod
    from astrodeck import events
    root.mkdir(parents=True, exist_ok=True)
    mp.setattr(hub_mod, "CAPTURE_DIR", root)
    moved = ["astrodeck.hub.CAPTURE_DIR"]
    cache = _CaptureRoots.path_names
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "astrodeck"
                               or name.startswith("astrodeck.")):
            continue
        seen = cache.get(name)
        if seen is None or seen[0] is not mod:
            try:
                names = tuple(attr for attr, value in list(vars(mod).items())
                              if isinstance(value, PurePath))
            except TypeError:
                continue
            cache[name] = seen = (mod, names)
        for attr in seen[1]:
            value = getattr(mod, attr, None)
            if not isinstance(value, PurePath):
                continue
            rel = _under_a_capture_root(value)
            if rel is None:
                continue
            new = root / rel
            if new != value:
                mp.setattr(mod, attr, new)
                moved.append(f"{name}.{attr}")
    mp.setenv("ASTRODECK_CAPTURE_DIR", str(root))
    writer = events.bus.night_log
    if writer is not None:
        mp.setattr(events.bus, "night_log",
                   events.NightLogWriter(keep_nights=writer.keep_nights))
    return moved


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_captures():
    """Point the capture root at a throwaway directory for the WHOLE session,
    so nothing a test does, nor a module- or session-scoped fixture set up
    before any test's own fixture, reaches the developer's real
    ``captures/`` (#309).

    It did, for months. The session store, the report store and the night
    log all resolve ``hub.CAPTURE_DIR`` live, and a test that did not point
    it elsewhere wrote into the repo's ``captures/``: 620 files in
    ``captures/sessions`` on 2026-09-25, most of them from two tests
    (``test_nonphotosphere_issue_batch.py``'s "Synthetic immediate stop"
    and ``test_abort_stays_aborted.py``'s plan "p" of one target "T"), with
    the reports those runs finalized in ``captures/reports`` and every bus
    log line in ``captures/logs``. It is not inert: auto-resume's armed
    scan, the Sessions panel and ``load_all`` read that store, and a
    wind-down timing test that parsed all 620 files failed its bound under
    load. Tests also rewrote what other tests had left: 357 of the plan "p"
    sessions, saved "active" by a test that never ran them, carry
    ``crash_resumes`` 1, which for such a session only the boot sweep
    writes (in an app lifespan some later test entered).

    The per-test fixture below is what a test sees; this one covers what
    runs outside any test's fixtures, and the session guard
    (`_TheRealCapturesStayUntouched`) checks the whole run afterwards."""
    import tempfile

    import astrodeck.hub as hub_mod
    _CaptureRoots.real = tuple(dict.fromkeys(
        (*_REAL_CAPTURE_ROOTS, Path(hub_mod.CAPTURE_DIR))))
    with tempfile.TemporaryDirectory(prefix="astrodeck-test-captures-",
                                     ignore_cleanup_errors=True) as d:
        _CaptureRoots.parent = Path(d)
        try:
            with pytest.MonkeyPatch.context() as mp:
                _point_the_capture_root_at(mp, Path(d) / "session")
                yield
        finally:
            _CaptureRoots.parent = None


@pytest.fixture(autouse=True)
def _captures_are_the_tests_own(_never_touch_the_real_captures, monkeypatch):
    """Every test gets a capture root of its own, empty, under the session's
    temporary directory, and every capture-root seam points at it
    (`_point_the_capture_root_at`). Yields the root.

    Not the test's ``tmp_path``: a test that lists its ``tmp_path`` would
    then find a ``logs/`` a night-log line made there, and requesting
    ``tmp_path`` for every test in the suite makes a numbered directory
    that pytest keeps. A test that points ``hub.CAPTURE_DIR`` at its own
    ``tmp_path``, as hundreds do, still wins: its monkeypatch comes after
    this one and is undone before it.

    Removed at teardown (a test that captures real simulator frames leaves
    megabytes), which is also why a test must not rely on another's.

    Its name sorts after ``_a_test_leaves_the_config_as_it_found_it`` and
    ahead of the other autouse fixtures here, so it is what sets up
    ``monkeypatch`` for every test, and the config guard still sets up
    first (see that fixture)."""
    root = _CaptureRoots.parent / f"t{next(_CaptureRoots.serial)}"
    _CaptureRoots.moved = _point_the_capture_root_at(monkeypatch, root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


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


class _TheTreeMustNotMove:
    """Fail the run, once and loudly, if an `astrodeck` source file changes
    while the suite runs.

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

    One check for the whole run, rather than one at each of the 34 sites: if
    nothing moved, every scrape in the run was sound, and if something did, no
    single test's verdict means anything and saying so once is the honest
    report. It compares at the end because that is the only moment that can
    see the whole run - a mid-run check would clear a file that changes
    afterwards.

    ONCE MEANS ONCE PER RUN, NOT ONCE PER PROCESS (issue #250). This was a
    session-scoped fixture raising at teardown, and a session fixture is torn
    down in every process: under xdist that is every worker, and pytest pins
    the error on whichever test the worker ran last - under `--dist
    worksteal`, the last item of the contiguous block of the collection that
    worker was handed, which is why the same "unrelated" node ids come back
    run after run. So one edit came out as
    twelve ERRORs on twelve unrelated tests - "12 errors" across files that
    had nothing to do with each other, truncated in the short summary to
    "AssertionE..." - which reads as a teardown leak on every worker, not as
    one fact about the tree. (With `-x`, pytest also tears the session down
    straight after a failure, so the getsource tests the edit broke carried
    the error on top of their FAILED, and that worker stopped there.)
    Reproduced on a byte copy of `server/` with an edit inserted mid-run: the
    twelve errors, every one naming `api/app.py, hub.py`.

    So it is a plugin, and only the process that owns the run acts on it: the
    xdist controller, or the one process of a `-n0` run. An xdist worker
    leaves it alone - its terminal output and its exit status are not the
    run's, so anything it reported would go nowhere. The verdict is one block
    printed after pytest's own closing counts line, the last thing in the
    output and so still there through `| tail`, and a run that would have
    exited 0 exits 1 instead; a run already exiting non-zero keeps its own
    code, because that code already says something.

    The snapshot is taken at session start, before collection. The fixture
    took it at the first test's setup, after collection had imported every
    module, so an edit landing in between went unreported while every scrape
    in the run read shifted text. The controller of an xdist run starts
    before its workers exist, so its snapshot precedes all of their imports.

    In a repository where agents share one working tree, this is a normal thing
    to do by accident, which is why it gets a message rather than a shrug.
    Its cases are in `test_suite_guards_report_once.py`.
    """

    HEADLINE = "THIS RUN IS INVALID: the source tree changed while it ran"

    def __init__(self, root: Path) -> None:
        self.root = root
        #: path -> mtime at session start; None in a process that is not the
        #: run's (an xdist worker), which therefore never reports.
        self._before: dict[Path, int] | None = None

    def _mtimes(self) -> dict[Path, int]:
        return {p: p.stat().st_mtime_ns for p in self.root.rglob("*.py")}

    def _moved(self, before: dict[Path, int]) -> tuple[list[str], list[str]]:
        """(changed or removed, appeared) since ``before``, as paths under
        the root."""
        now = self._mtimes()
        changed = sorted(p.relative_to(self.root).as_posix()
                         for p, t in before.items() if now.get(p) != t)
        appeared = sorted(p.relative_to(self.root).as_posix()
                          for p in now if p not in before)
        return changed, appeared

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if hasattr(session.config, "workerinput"):
            return
        self._before = self._mtimes()

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session):
        # tryfirst makes this the OUTERMOST wrapper, so the code after the
        # yield runs after the terminal reporter's, which is what prints the
        # summary and the counts line.
        result = yield
        if self._before is None:
            return result
        changed, appeared = self._moved(self._before)
        if not (changed or appeared):
            return result
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        # Names only: paths under the package, never their contents.
        body = [
            "The tests that read their subject's source (inspect.getsource) "
            "were reading a file that no longer matched the code they were "
            "grading, and they can pass that way as easily as fail (issue "
            "#118), so no verdict above can be trusted, the passes included. "
            "Re-run on a quiescent tree.",
            f"  changed or removed: {', '.join(changed) or 'none'}",
            f"  appeared: {', '.join(appeared) or 'none'}",
        ]
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is None:
            sys.stderr.write("\n".join([self.HEADLINE, *body]) + "\n")
        else:
            tr.write_sep("=", self.HEADLINE, red=True, bold=True)
            for line in body:
                tr.write_line(line)
        return result


class _TheRealCapturesStayUntouched:
    """Fail the run, once, naming every file under the real capture roots
    that was created, changed or removed while it ran (#309).

    The fixtures above give every test its own capture root; this is the
    check that they worked, for the whole run, including what no fixture
    covers (a module that writes at import, a child process started with
    its own environment, a helper that computes the repo's ``captures/``
    for itself). Created, changed AND removed: the night log's leak was
    appends to a file that already existed, and a session store over its
    soft quota prunes the developer's oldest sessions.

    A plugin, for the reasons `_TheTreeMustNotMove` gives: only the process
    that owns the run acts (the xdist controller, or a -n0 run's one
    process), since the files are one set whichever worker wrote them, and
    the verdict is one block after pytest's counts line, with exit status 1
    for a run that would have exited 0. The listing is taken at session
    start and again at the end (about 6000 files here, well under a second
    with ``os.scandir``).

    It cannot tell who wrote a file. Anything else writing into the same
    ``captures/`` during the run (a server started from this checkout, a
    suite running an older conftest) is named too, and the report says so.
    To find the writer, open the file: a session names its plan, a report
    its target. Names only in the output, never contents.

    Its cases are in test_capture_root_isolated.py."""

    HEADLINE = "A TEST RUN WROTE INTO THE REAL captures/ (issue #309)"
    #: How many names each list prints before "and N more".
    SHOWN = 25

    def __init__(self, roots: tuple[Path, ...]) -> None:
        self.roots = roots
        #: (root index, path under it) -> (mtime_ns, size) at session start;
        #: None in a process that is not the run's.
        self._before: dict[tuple[int, str], tuple[int, int]] | None = None

    def listing(self) -> dict[tuple[int, str], tuple[int, int]]:
        """Every file under every root, keyed by root and relative path."""
        out: dict[tuple[int, str], tuple[int, int]] = {}
        for i, root in enumerate(self.roots):
            stack = [str(root)]
            while stack:
                here = stack.pop()
                try:
                    entries = list(os.scandir(here))
                except OSError:
                    continue
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    rel = Path(os.path.relpath(e.path, root)).as_posix()
                    out[(i, rel)] = (st.st_mtime_ns, st.st_size)
        return out

    def moved(self, before, after) -> tuple[list[str], list[str], list[str]]:
        """(created, changed, removed), each as ``<root>/<path>`` names."""
        def name(key: tuple[int, str]) -> str:
            return f"{self.roots[key[0]].as_posix()}/{key[1]}"
        created = sorted(name(k) for k in after if k not in before)
        changed = sorted(name(k) for k in after
                         if k in before and after[k] != before[k])
        removed = sorted(name(k) for k in before if k not in after)
        return created, changed, removed

    def report(self, created, changed, removed) -> list[str]:
        """The block's body lines, one list per kind, capped with a count."""
        def line(label: str, names: list[str]) -> str:
            shown = ", ".join(names[:self.SHOWN]) or "none"
            more = len(names) - self.SHOWN
            return (f"  {label} ({len(names)}): {shown}"
                    + (f", and {more} more" if more > 0 else ""))
        return [
            "The developer's real sessions, reports, night logs and frames "
            "live under these roots, and every test has a capture root of "
            "its own (conftest's _captures_are_the_tests_own), so a file "
            "that moved here was written around it: by a module or a "
            "process that computes the root for itself, or by something "
            "else writing here while the suite ran (a server started from "
            "this checkout). Open a file to find its writer: a session "
            "names its plan.",
            line("created", created),
            line("changed", changed),
            line("removed", removed),
        ]

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if hasattr(session.config, "workerinput"):
            return
        self._before = self.listing()

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session):
        result = yield
        if self._before is None:
            return result
        created, changed, removed = self.moved(self._before, self.listing())
        if not (created or changed or removed):
            return result
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        body = self.report(created, changed, removed)
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is None:
            sys.stderr.write("\n".join([self.HEADLINE, *body]) + "\n")
        else:
            tr.write_sep("=", self.HEADLINE, red=True, bold=True)
            for text in body:
                tr.write_line(text)
        return result


def pytest_configure(config):
    # Registered at configure time, so it is in place for pytest_sessionstart,
    # which is before collection imports anything (see the class).
    config.pluginmanager.register(
        _TheTreeMustNotMove(_SERVER_DIR / "astrodeck"), "astrodeck-tree-guard")
    config.pluginmanager.register(
        _TheRealCapturesStayUntouched(_REAL_CAPTURE_ROOTS),
        "astrodeck-captures-guard")
