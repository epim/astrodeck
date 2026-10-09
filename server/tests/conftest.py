# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures/path setup for the server test suite.

Puts the sibling ``relay/`` repo dir on ``sys.path`` so the W3 END-TO-END test
(tests/test_remote_e2e.py) can import the REAL relay-lane package alongside the
home ``astrodeck`` package and wire them through an in-memory tunnel. The relay
package is pure Python (+ websockets, already in the venv); if the dir is absent
the e2e test ``importorskip``s.
"""
from __future__ import annotations

import asyncio
import atexit
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


@pytest.fixture(autouse=True)
def _no_calibration_settle(monkeypatch):
    """Runtime seam (test-suite fast-path), the native guider's half of
    ``_fast_sim_delays``: zero ``guide.native._CAL_SETTLE_S`` (#848's settle
    before a calibration walk, 5 s on a rig) for the WHOLE suite. About twenty
    files start a fresh native calibration, and a real 5 s wait in each buys
    nothing a simulator can show. The tests of the settle itself
    (test_oct08_guider_observability.py) opt back in with their own
    ``monkeypatch.setattr``."""
    import astrodeck.guide.native as native_mod
    monkeypatch.setattr(native_mod, "_CAL_SETTLE_S", 0.0)
    yield


@pytest.fixture(autouse=True)
def _skip_target_holds(monkeypatch):
    """Runtime seam (test-suite fast-path), the engine's own half of
    ``_fast_sim_delays`` above (WP-31 integration follow-up, backlog wave
    4, owner-approved 2026-09-30): skip ``SequenceEngine._await_target_
    window``'s and ``_hold_for_light``'s real-time holds for the WHOLE
    suite.

    These two used to read ``ASTRODECK_FAST_TEST`` directly, the same flag
    ``devices.sim`` and ``solve.simsolver`` read for their own pacing --
    but those two are device/solver SIMULATORS faking elapsed time for
    hardware that is not there, and a hold deciding whether to wait for a
    real sky is not that: a probe or a simulator SERVER started with that
    deployment flag set must still hold for a target's own window and for
    light, exactly as a real rig would. So engine.py now reads its own
    module switch, ``_SKIP_TARGET_HOLDS_FOR_TEST``, which only this
    fixture and test_w4_target_window_and_light_hold.py's own opt-out test
    (which flips it back to ``False`` for itself, in place of the old
    ``monkeypatch.delenv("ASTRODECK_FAST_TEST")``) ever touch.
    test_w4_no_engine_fast_test_read.py guards that no OTHER module under
    server/astrodeck reads the env var at all."""
    import astrodeck.sequence.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_SKIP_TARGET_HOLDS_FOR_TEST", True)
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
    checkout only, pointing at guiding code that was completely innocent.

    THE PROFILE LIBRARY is moved too (2026-09-26, #341). ``profiles.profiles``
    was built at import with ``PROFILES_DIR``, the developer's real
    ``server/config/profiles/``, and nothing here moved it: a probe of the
    whole suite found 8 tests in 3 files (test_connect_rig_guard.py,
    test_hub_solve.py, test_no_route_leaks_the_site_coordinates.py) reading
    that directory, the last listing the two real profiles into the route
    bodies it scans. The store's path and the library's directory are
    recorded first as the developer's real config (``_RealConfig``), which
    ``_no_test_reads_the_real_config`` then refuses for the whole run.

    AND EVERYTHING ELSE BUILT ON ``CONFIG_DIR`` AT IMPORT, swept rather
    than listed (2026-09-28, S5, verifying #361; filed as #436). #361 said
    a new singleton left on the real directory would fail loudly; the guard
    watched two classes, so one of any other class read quietly. A scan of
    every ``astrodeck`` module found three more singletons
    (``plans.plan_library``, ``locations.location_store``,
    ``auth.users.user_store``), five module globals read at call time
    (``flows.store``'s ``CONFIG_DIR``, whose comment says it is resolved
    live, ``config``'s egain, filter-name and focuser files, and
    ``licensing``'s consent file), and eleven more bindings of those paths
    used only at import, as a default argument or not at all. A probe of
    the suite against a stand-in
    real config (scratchpad s5-srvsmall-probe) measured hundreds of tests
    reading them: the sim rig's connect reads ``egain.json`` and
    ``filter_names.json``, the app's startup lists ``plans/``, and the site
    leak scanner read the real ``flows/``, ``plans/``, ``locations.json``
    and ``users.json``. ``_sweep_off_the_real_config`` moves them all, and
    the guard now watches the directory as well as the two classes.

    THE ``config_store``/``CONFIG_DIR`` REDIRECT AND THE WATCHERS NOW START
    AT CONFTEST IMPORT, not here (#675 part 2, WP-H2). This fixture's own
    setup is already too late for a module-level statement in a test file,
    which reaches the shared store at ITS OWN import -- before collection
    has even finished, let alone before the first test asks for THIS
    fixture. ``_arm_real_config_guard_before_collection`` (above) does that
    first half as soon as this file is read; this fixture reuses what it
    already set up (the throwaway directory, the real file/dir, the
    watchers' undo) and FAILS LOUDLY, before doing anything else, if the
    collection window still saw a reach -- see its own docstring for the
    traced example and why a write during collection is possible at all on
    a fresh worktree."""
    import astrodeck.profiles as profiles_mod
    from astrodeck.catalog.ephemeris import elements as elements_mod
    # If this fires, something reached the real config during COLLECTION
    # despite the redirect already being in place -- a `ConfigStore` built
    # explicitly on `CONFIG_FILE`/the real directory, bypassing the
    # singleton that redirect moved. Loud and immediate (every test in the
    # session depends on this autouse fixture, so this fails the whole run
    # at setup) beats an intermittent xdist race on the same real file
    # (#675 part 2): the SAME cause now gives every worker the SAME error,
    # naming it, instead of some workers losing a race others win.
    if _RealConfig.reads:
        raise AssertionError(
            "the developer's real config was reached during test "
            "COLLECTION, before any test -- or this fixture -- could run: "
            + ", ".join(_RealConfig.reads) + ". A module-level statement in "
            "a test file that reaches astrodeck.config.config_store (or "
            "anything built on it) at ITS OWN import is the usual shape "
            "(#675): make that computation lazy, inside a fixture or the "
            "first test that needs it, never a module-level statement.")
    import astrodeck.config as config_mod
    real, real_dir = _REAL_CONFIG_FILE, _REAL_CONFIG_DIR
    real_profiles = profiles_mod.profiles._dir
    real_elements = (elements_mod.ELEMENTS_DIR, elements_mod.SATELLITE_FILE,
                     elements_mod.COMET_FILE)
    real_start = elements_mod.EphemerisStore.start
    d = _CONFIG_COLLECTION_DIR
    assert config_mod.config_store._path != real
    profiles_mod.profiles._dir = Path(d) / "profiles"
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
    # Last, so the moves above are already off the real directory and
    # the sweep's known positives are the seams nothing above moves.
    put_back = _sweep_off_the_real_config(real_dir, Path(d))
    yield
    put_back()
    config_mod.config_store._path = real
    config_mod.config_store._cfg = None
    config_mod.CONFIG_DIR = real_dir
    profiles_mod.profiles._dir = real_profiles
    (elements_mod.ELEMENTS_DIR, elements_mod.SATELLITE_FILE,
     elements_mod.COMET_FILE) = real_elements
    elements_mod.EphemerisStore.start = real_start
    shutil.rmtree(d, ignore_errors=True)
    _unwatch_real_config_guard()


class _RealConfig:
    """The developer's real config, as ``astrodeck.config`` resolved it at
    import, and every time a test reached it (#341).

    Real means the LOCATION: the ``astrodeck.json`` the process store was
    built on (``server/config/``, or ``ASTRODECK_CONFIG_DIR``) and the
    ``profiles/`` directory the profile library was built on; and, for
    everything else in it that no class here owns (``plans/``,
    ``locations.json``, ``users.json``, ``egain.json``), that DIRECTORY
    (``dirs``, S5). The session
    fixture above moves every shared seam off both, so what reaches them
    during a run came around it: a ``ConfigStore()`` or ``ProfileLibrary()``
    built with no path, whose defaults were bound to the real location when
    the class was defined; a reload of ``astrodeck.config``; a path some
    fixture put back. Whatever such a test answers is this machine's config,
    and on another machine it answers something else. The #309 guard's
    reasoning for ``captures/``, applied to reads.

    Not the process store itself. It stands in for the developer's config
    in production, but during a run its file is a throwaway: a probe of the
    whole suite counted 854 tests in 129 files reading optics or a profile
    through it, every one of them getting the same default answer on every
    machine, and the #227 guard fails any test that leaves it changed."""

    #: Resolved paths of the real config file (the store's and
    #: ``CONFIG_FILE``, the same path unless something moved one).
    files: frozenset[str] = frozenset()
    #: Resolved paths of the real profiles directory.
    profile_dirs: frozenset[str] = frozenset()
    #: The real config DIRECTORY, resolved and as given (the audit hook in
    #: ``_watch_the_real_config`` compares unresolved paths, which is what
    #: a path built on ``CONFIG_DIR`` is), for everything in it the two
    #: classes above do not own.
    dirs: frozenset[str] = frozenset()
    #: One line per reach, ``"<seam> (<what>)"``, appended by the watchers
    #: and read by the per-test guard. Names only: never a value read.
    reads: list[str] = []
    #: Where the current test's window starts in ``reads``, set by the
    #: guard; a repeat is dropped only within one window.
    mark: int = 0

    @staticmethod
    def _key(path) -> str:
        return os.path.normcase(str(Path(path).resolve()))

    @classmethod
    def record(cls, *, files, profile_dirs, dirs=()) -> None:
        cls.files = frozenset(cls._key(p) for p in files)
        cls.profile_dirs = frozenset(cls._key(p) for p in profile_dirs)
        cls.dirs = frozenset(
            k for p in dirs
            for k in (cls._key(p), os.path.normcase(os.path.abspath(p))))

    @classmethod
    def entry(cls, path) -> str | None:
        """The entry of a real config directory that ``path`` is in, as the
        guard names it (``plans/``, ``locations.json``), or None.

        No ``resolve()``: this runs for every file the process opens, so it
        is two string operations and a prefix test, against both spellings
        of each directory ``record`` kept."""
        if path is None or isinstance(path, int):
            return None
        try:
            text = os.fsdecode(path) if isinstance(path, bytes) else os.fspath(path)
            key = os.path.normcase(os.path.abspath(text))
        except (TypeError, ValueError, OSError):
            return None
        for real in cls.dirs:
            if key.startswith(real + os.sep):
                head, sep, _ = key[len(real) + 1:].partition(os.sep)
                return head + "/" if sep else head
            if key == real:
                return "./"
        return None

    @classmethod
    def reached(cls, seam: str, where, kind: str) -> bool:
        """Note ``seam`` if ``where`` is the real ``kind`` location; say
        whether it was."""
        if where is None:
            return False
        real = cls.files if kind == "config file" else cls.profile_dirs
        try:
            hit = cls._key(where) in real
        except (OSError, TypeError, ValueError):
            return False
        if hit:
            cls.note(f"{seam} (the real {kind})")
        return hit

    @classmethod
    def note(cls, line: str) -> None:
        # A leaky test may read in a loop: one line per run of reads, but
        # never across windows, or the next test's first read of the same
        # seam would be dropped and that test pass unnamed (it was, until
        # the stand-in run with the session net removed showed 1 test of 5
        # named where all 5 read the real file).
        if len(cls.reads) > cls.mark and cls.reads[-1] == line:
            return
        cls.reads.append(line)


#: The audit events through which a file in the real config directory is
#: read, listed or changed (``os.replace`` raises ``os.rename``).
_DIRECTORY_EVENTS = frozenset({
    "open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.rmdir",
    "os.mkdir", "os.truncate", "shutil.copyfile", "shutil.rmtree"})
#: Of those, the ones whose first two arguments are both paths.
_TWO_PATH_EVENTS = frozenset({"os.rename", "shutil.copyfile"})

#: For each of ``_DIRECTORY_EVENTS`` whose audit signature carries a
#: ``dir_fd`` (Python's audit-events table), the position(s) of that dir_fd
#: WITHIN THE FULL AUDIT TUPLE, one per path ``audited`` checks (position 0
#: for a single-path event, positions 0 and 1 for a two-path one) (#659).
#: ``open`` is not here: its own audit arguments are always ``(path, mode,
#: flags)``, with no dir_fd slot at all, whether or not the call that raised
#: it passed one -- it is told apart a different way, below.
_DIR_FD_ARG_INDEX: dict[str, tuple[int, ...]] = {
    "os.mkdir": (2,),        # (path, mode, dir_fd)
    "os.rmdir": (1,),        # (path, dir_fd)
    "os.remove": (1,),       # (path, dir_fd)
    "shutil.rmtree": (1,),   # (path, dir_fd) -- shutil's own top-level audit
    "os.rename": (2, 3),     # (src, dst, src_dir_fd, dst_dir_fd)
}


def _dir_fd_given(value: object) -> bool:
    """True when ``value`` is a real, open directory file descriptor, i.e.
    this particular call passed a ``dir_fd`` rather than leaving it unset.

    CPython spells "unset" two different ways depending on where the audit
    call is raised: the C-implemented ``os.*`` functions (mkdir/rmdir/
    remove/rename) pass the sentinel ``-1`` (confirmed live against a
    running CPython 3.12 on this platform -- ``os.mkdir``'s own audit event
    shows ``-1``, never ``None``, when no ``dir_fd`` is given), while
    ``shutil.rmtree``'s own pure-Python ``sys.audit("shutil.rmtree", path,
    dir_fd)`` passes Python's own default, ``None``. A real fd is always a
    non-negative int, so one check tells both sentinels from a real one."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _dir_fd_relative_positions(event: str, args: tuple) -> frozenset[int]:
    """Which of ``audited``'s checked path argument(s) (index 0, or 0 and 1
    for a two-path event) must NOT be resolved against the CWD for this
    particular call, because the event fired with a real ``dir_fd`` rather
    than the "unset" sentinel (#659): ``os.path.abspath`` on a path that is
    actually relative to an already-open directory fd is not an
    approximation of the right answer, it is simply the wrong operation --
    which is how a tmp_path holding a directory literally named ``config``
    read as "os.rmdir of ./ (the real config directory)" on the Linux CI
    runner. ``shutil.rmtree``'s fd-based walk (``_rmtree_safe_fd``) descends
    into and removes each child by its BARE NAME relative to the
    already-open PARENT fd, and that bare name can coincide with a real
    config entry's name with nothing in the event to say it was never
    resolved against the CWD at all. Events this guard watches with no
    dir_fd argument at all (``os.listdir``, ``os.scandir``,
    ``os.truncate``, ``shutil.copyfile``, and ``open`` -- see
    ``_open_is_fd_relative``) return empty: unchanged behaviour."""
    fd_positions = _DIR_FD_ARG_INDEX.get(event)
    if fd_positions is None:
        return frozenset()
    return frozenset(path_index for path_index, fd_index in enumerate(fd_positions)
                      if len(args) > fd_index and _dir_fd_given(args[fd_index]))


#: (filename, function name) marking a frame as shutil's REAL fd-based
#: delete walk (``_rmtree_safe_fd``) -- reached either directly
#: (``shutil.rmtree`` on Linux) or through pytest's own tmp-dir cleanup,
#: which calls ``shutil.rmtree`` the very same way (``_pytest/pathlib.py``'s
#: ``rm_rf``), so one signature covers both halves of #659's fix shape ("a
#: relative open raised from inside shutil.rmtree or pytest's tmpdir
#: cleanup"). Matched by normcased filename so a different venv's absolute
#: prefix still lines up.
_FD_RELATIVE_OPEN_FRAME = (os.path.normcase(shutil.__file__), "_rmtree_safe_fd")


def _open_is_fd_relative(frame) -> bool:
    """True when an ``open`` audit event -- whose own arguments never carry
    a dir_fd, ``(path, mode, flags)`` whether or not the call that raised it
    passed one -- was raised while a frame on the stack is shutil's
    fd-based delete walk: the one place this codebase's dependencies open a
    bare, CWD-unresolvable name relative to an already-open directory fd
    (#659).

    Takes the starting frame as a plain parameter, rather than reading
    ``sys._getframe`` itself, so it can be driven directly by a test with a
    constructed stand-in frame chain. That is not a shortcut: dir_fd is not
    implemented on Windows at all for the calls ``_rmtree_safe_fd`` makes
    (``os.open`` and ``os.rmdir`` are both absent from
    ``os.supports_dir_fd`` here), and the module constant the real code path
    reads before it ever gets that far (``os.O_NONBLOCK``) does not even
    exist on this platform -- confirmed live, not assumed -- so the real
    call cannot be executed here under any amount of monkeypatching, and a
    constructed frame is the only honest way to drive this one branch."""
    while frame is not None:
        key = (os.path.normcase(frame.f_code.co_filename),
               frame.f_code.co_name)
        if key == _FD_RELATIVE_OPEN_FRAME:
            return True
        frame = frame.f_back
    return False


#: What the sweep leaves on the real config, by name: the two locations
#: ``_RealConfig`` records, which are also the defaults a ``ConfigStore()``
#: or ``ProfileLibrary()`` built with no path falls back to (the guard's
#: known positive in test_real_config_guard.py builds exactly those).
_LEFT_ON_THE_REAL_CONFIG = frozenset({
    "astrodeck.config.CONFIG_FILE", "astrodeck.config.PROFILES_DIR"})

#: The sweep's known positives, ``(module, attribute path)``: seams tests
#: were measured reaching the real directory through (the s5-srvsmall-probe
#: run: ``egain.json`` and ``filter_names.json`` by the sim rig's connect,
#: ``plans/`` by the app's startup, and ``flows/``, ``locations.json`` and
#: ``users.json`` by the site leak scanner). Each is asserted moved, so a
#: sweep that took nowhere fails at the first test rather than passing
#: every test that never reads one.
#:
#: ``flows/`` and ``users.json`` are no longer here (the S7 integration).
#: Since #436 the flow store and the user store read ``config.CONFIG_DIR``
#: at each call, and the session fixture points that at the throwaway
#: before the sweep runs, so their two entries (``flows.store.CONFIG_DIR``
#: and ``user_store._path``) passed whether the sweep moved anything or
#: not: with the sweep's ``setattr`` made a no-op and only those two
#: entries left, test_s7_store_config_dir_live.py passed 6 of 6, where the
#: four below error every case at setup ("astrodeck.plans.plan_library._dir
#: is still on the developer's real config: the sweep missed it"). A known
#: positive that cannot fail grades nothing; that the two stores follow the
#: directory is test_s7_store_config_dir_live.py's to hold.
_SWEEP_MUST_MOVE = (
    ("astrodeck.plans", "plan_library._dir"),
    ("astrodeck.locations", "location_store._path"),
    ("astrodeck.config", "EGAIN_CONFIG_FILE"),
    ("astrodeck.config", "FILTER_CONFIG_FILE"),
)


def _built_on_the_real_config(real_dir) -> list[tuple[object, str, str, str]]:
    """Every global of a loaded ``astrodeck`` module, and every attribute of
    an ``astrodeck`` object a module holds, that is a path in ``real_dir``:
    ``(owner, attribute, dotted name, path relative to real_dir)``, each
    object once however many modules bind it.

    ``_LEFT_ON_THE_REAL_CONFIG`` is left out, and no function's default
    arguments are looked at: a ``ConfigStore()`` built with no path is the
    guard's to name, not the sweep's to hide. Paths only, never strings:
    ``provenance.LAYER_CONFIG`` is the word "config", which resolves to the
    real directory from ``server/`` and is not a path."""
    real = Path(real_dir).resolve()
    real_key = _RealConfig._key(real)
    found: list[tuple[object, str, str, str]] = []
    seen: set[tuple[int, str]] = set()

    def inside(value) -> str | None:
        if not isinstance(value, PurePath):
            return None
        key = _RealConfig._key(value)
        if key != real_key and not key.startswith(real_key + os.sep):
            return None
        return os.path.relpath(Path(value).resolve(), real)

    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "astrodeck"
                               or name.startswith("astrodeck.")):
            continue
        for attr, value in list(vars(mod).items()):
            where = inside(value)
            if where is not None:
                dotted = f"{name}.{attr}"
                if dotted not in _LEFT_ON_THE_REAL_CONFIG:
                    found.append((mod, attr, dotted, where))
                continue
            if isinstance(value, type) or not str(
                    getattr(type(value), "__module__", "")).startswith(
                    "astrodeck"):
                continue
            try:
                held = list(vars(value).items())
            except TypeError:
                continue
            for inner, path in held:
                where = inside(path)
                if where is not None and (id(value), inner) not in seen:
                    seen.add((id(value), inner))
                    found.append((value, inner, f"{name}.{attr}.{inner}",
                                  where))
    return found


def _sweep_off_the_real_config(real_dir, throwaway: Path):
    """Move everything ``_built_on_the_real_config`` finds to the same place
    under ``throwaway``, and return the undo.

    SWEPT, NOT LISTED, for the reason ``IsolatedConfig.sweep`` gives: the
    session fixture listed the seams it knew, three times over, and each
    new one read the developer's config until someone found it. Only what
    is loaded when the fixture runs is swept; a module imported later binds
    ``config.CONFIG_DIR``, which is already the throwaway, and anything
    else it builds on the real directory is the guard's to name.

    Its known positives (``_SWEEP_MUST_MOVE``) are imported first and
    asserted moved, and the two stores among them are asserted to hold
    nothing loaded: a store that read the real file before the sweep would
    go on serving it from its cache wherever its path pointed."""
    import importlib
    for name, _ in _SWEEP_MUST_MOVE:
        importlib.import_module(name)
    moves: list[tuple[object, str, object]] = []
    for owner, attr, _, where in _built_on_the_real_config(real_dir):
        moves.append((owner, attr, getattr(owner, attr)))
        setattr(owner, attr, throwaway / where)

    def undo() -> None:
        for owner, attr, old in reversed(moves):
            setattr(owner, attr, old)

    try:
        home = _RealConfig._key(throwaway)
        for name, path in _SWEEP_MUST_MOVE:
            owner = sys.modules[name]
            *parents, last = path.split(".")
            for part in parents:
                owner = getattr(owner, part)
            assert _RealConfig._key(getattr(owner, last)).startswith(home), (
                f"{name}.{path} is still on the developer's real config: "
                f"the sweep missed it")
        from astrodeck.auth.users import user_store
        from astrodeck.locations import location_store
        assert location_store._items is None and user_store._users is None, (
            "a store read the developer's real config before the sweep "
            "moved it, and would serve it from its cache")
    except BaseException:
        undo()
        raise
    return undo


def _watch_the_real_config():
    """Wrap every method through which a ``ConfigStore`` reads or writes its
    file and a ``ProfileLibrary`` its directory, and hook every file the
    process touches in the real config directory, so a reach of the real
    location is noted in ``_RealConfig.reads``. Returns the undo.

    At the class, so a store or library built by any test, any fixture or
    any module is watched without being found first. The methods that
    touch the disk pay one path comparison per call: a store loads once and
    saves on a change. ``cfg()``, which every reader calls, pays one
    identity test (40 ns a call, measured): a config ``_load`` read from
    the real file is remembered on its store, and a ``cfg()`` served from
    that cache is a read of the real config too. Without it only the test
    that first loaded such a store was named; a stand-in run with the
    session net removed named 1 of 5 tests reading one. ``functools.wraps``
    keeps ``inspect.getsource`` on a wrapped method reading its own
    source."""
    import functools

    from astrodeck.config import ConfigStore
    from astrodeck.profiles import ProfileLibrary
    undo = []

    def replace(cls, name: str, watched) -> None:
        inner = cls.__dict__[name]
        setattr(cls, name, functools.wraps(inner)(watched(inner)))
        undo.append(lambda: setattr(cls, name, inner))

    def watch(cls, name: str, attr: str, kind: str) -> None:
        seam = f"{cls.__name__}.{name}"

        def watched(inner):
            def call(self, *a, **kw):
                _RealConfig.reached(seam, getattr(self, attr, None), kind)
                return inner(self, *a, **kw)
            return call
        replace(cls, name, watched)

    held_real = "_astrodeck_test_real_cfg"

    def load(inner):
        def call(self, *a, **kw):
            real = _RealConfig.reached("ConfigStore._load",
                                       getattr(self, "_path", None),
                                       "config file")
            loaded = inner(self, *a, **kw)
            if real:
                self.__dict__[held_real] = loaded
            else:
                self.__dict__.pop(held_real, None)
            return loaded
        return call

    def cfg(inner):
        def call(self):
            held = self.__dict__.get(held_real)
            if held is not None and self._cfg is held:
                _RealConfig.note("ConfigStore.cfg (the real config file)")
            return inner(self)
        return call

    replace(ConfigStore, "_load", load)
    replace(ConfigStore, "cfg", cfg)
    watch(ConfigStore, "_save", "_path", "config file")
    for name in ("_all", "get", "save", "delete"):
        watch(ProfileLibrary, name, "_dir", "profiles directory")

    # THE DIRECTORY, for everything in it no class above owns (S5, verifying
    # #361; #436). A plan library, a location store, a user store and five
    # module globals read at call time were built on CONFIG_DIR at import
    # and nobody watched them, so "a new singleton fails loudly" held only
    # for the two classes. An audit hook sees every open, listing, removal
    # and rename in the process, so a singleton nobody has found yet is
    # watched too. Measured: 2 us on a 42 us open-and-read, and 75 ns on
    # every other audit event. A hook cannot be removed, so the undo
    # disarms it; and it must never raise, because an audit hook that
    # raises fails the operation it was watching.
    armed = [True]

    def audited(event: str, args: tuple) -> None:
        if event not in _DIRECTORY_EVENTS or not armed[0]:
            return
        try:
            # #659: a relative path audited here is not necessarily CWD-
            # relative. ``open`` carries no dir_fd in its own arguments at
            # all (told apart by the call stack instead); the other events
            # DO carry one, and a path whose call passed a real dir_fd is
            # skipped rather than wrongly resolved against the CWD.
            if event == "open":
                if _open_is_fd_relative(sys._getframe(1)):
                    return
                checked, skip = args[:1], frozenset()
            else:
                checked = args[:2] if event in _TWO_PATH_EVENTS else args[:1]
                skip = _dir_fd_relative_positions(event, args)
            for i, path in enumerate(checked):
                if i in skip:
                    continue
                entry = _RealConfig.entry(path)
                if entry is not None:
                    _RealConfig.note(
                        f"{event} of {entry} (the real config directory)")
        except Exception:  # noqa: BLE001 - see above
            pass

    sys.addaudithook(audited)
    undo.append(lambda: armed.__setitem__(0, False))

    def unwatch() -> None:
        for step in reversed(undo):
            step()
    return unwatch


def _arm_real_config_guard_before_collection():
    """Do the FIRST half of what ``_never_touch_the_real_config`` below does,
    at conftest IMPORT rather than at that fixture's first setup (#675 part
    2, backlog WP-H2, from the plan the owner approved 2026-09-30): point
    the shared ``config_store`` at a throwaway file
    before any test MODULE is collected, so a module-level statement in a
    test file -- which reaches the singleton at ITS OWN import, before any
    fixture exists to redirect it -- lands on a private file instead of the
    developer's real ``server/config/astrodeck.json``.

    WHY A FIXTURE CANNOT CLOSE THIS WINDOW, no matter how early it is
    declared: ``_never_touch_the_real_config`` is ``scope="session"``, but a
    fixture -- even a session-scoped, autouse one -- is still lazy. pytest
    only materialises it when the FIRST TEST asks for it, which is after
    collection has already imported every test module in the run. Anything
    a test file's import touches at its OWN module level reaches the
    singleton with no fixture in effect yet.

    TRACED TO GROUND (#675, three occurrences across three worktrees,
    reported as an intermittent xdist race -- "ConfigStore._load() falling
    through to _save() in several xdist workers at once"): test_w1_cooling_
    restore_daylight.py's `NIGHT_TS = _night_midpoint()` is a module-level
    statement. It calls astrodeck.sequence.schedule.observing_night, which
    calls `config_store.cfg()`. On a FRESH worktree -- `server/config/` is
    gitignored, so every WP worktree in this backlog plan starts without a
    file -- that reaches `ConfigStore._load`'s ``FileNotFoundError`` branch,
    which falls through to `_save()`, writing the default config to the REAL
    path. One worker doing that alone is harmless (a default file nobody
    asked for, but not wrong). Several xdist workers importing the same test
    file at once race to create the same file at the same path and
    intermittently collide: observed, verbatim, ``FileNotFoundError`` then
    ``PermissionError`` on ``astrodeck.json`` or its ``.tmp`` rename, and
    pytest-xdist reporting "Different tests were collected" once a worker's
    collection raised where another worker's import got there first and
    succeeded.

    test_w1_cooling_restore_daylight.py is not a file this WP may edit (it
    is outside its owned-files list), so the fix here is structural: close
    the window every such statement writes through, rather than that one
    statement. test_w1_cooling_restore_daylight.py's own fix -- making
    ``NIGHT_TS`` lazy -- is filed as its own issue (see this WP's return).

    THE WATCHERS ARE ARMED HERE TOO (``_watch_the_real_config``), not only
    the redirect: a ``ConfigStore`` built explicitly on ``CONFIG_FILE``,
    bypassing the singleton this function moves, still reaches the real
    path, and now gets recorded from collection onward rather than only from
    the first test. ``_never_touch_the_real_config`` fails loudly, at its
    own setup, if anything was recorded before it got a chance to run --
    exactly the collection window this closes (empty in the healthy case:
    the redirect above means the traced statement no longer reaches the real
    path AT ALL, so there is nothing left for the watcher to catch there;
    what it still catches is anything that reaches the real path SOME OTHER
    way).

    Returns ``(directory, real_file, real_dir, unwatch)`` for
    ``_never_touch_the_real_config`` to finish the job with: the throwaway
    directory this function already pointed ``config_store``/``CONFIG_DIR``
    at (REUSED, not recreated there, so profiles/ephemeris land beside it
    under one root, as before); the genuine real file and directory (read
    HERE, before anything moves them -- ``config_store._path``/``CONFIG_DIR``
    by the time that fixture runs already answer this throwaway directory,
    not the developer's real location, so it cannot re-read them itself);
    and the watchers' undo, called at that fixture's own teardown, same as
    today."""
    import tempfile

    import astrodeck.config as config_mod
    import astrodeck.profiles as profiles_mod
    # CONFIG_FILE/PROFILES_DIR are plain module constants, computed once at
    # config.py's own import and never reassigned anywhere in the codebase
    # (grepped) -- unlike config_store._path/CONFIG_DIR, which this function
    # is about to move, they stay correct to read at ANY later point. Using
    # them (rather than config_store._path/CONFIG_DIR, read before moving)
    # is what lets `_never_touch_the_real_config` below read "what is real"
    # from this function's return instead of needing its own early capture.
    real_file = config_mod.CONFIG_FILE
    real_dir = real_file.parent
    _RealConfig.record(files=(real_file,),
                       profile_dirs=(profiles_mod.profiles._dir,
                                     config_mod.PROFILES_DIR),
                       dirs=(real_dir,))
    unwatch = _watch_the_real_config()
    directory = tempfile.mkdtemp(prefix="astrodeck-test-config-")
    config_mod.config_store._path = Path(directory) / "astrodeck.json"
    config_mod.config_store._cfg = None      # drop anything already loaded
    config_mod.CONFIG_DIR = Path(directory)
    # Backstop for a leak this move introduced (found verifying WP-H2,
    # filed as its own issue): the ONLY other cleanup is
    # `_never_touch_the_real_config`'s session-fixture teardown
    # (`shutil.rmtree(d, ...)` below), and pytest never sets up or tears
    # down fixtures for a `--collect-only` run -- exactly the command this
    # WP's own fix shape says to run repeatedly to validate it. Measured
    # directly: one `--collect-only -n 0` run left one orphaned
    # `astrodeck-test-config-*` directory in the OS temp dir with no
    # in-session code path left to remove it; under `-n auto` that is one
    # per worker, every run, forever. `atexit` fires at ordinary
    # interpreter shutdown (collect-only still exits normally), so this
    # removes the directory even when the fixture that would otherwise
    # clean it up never runs.
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    return directory, real_file, real_dir, unwatch


#: Armed at conftest IMPORT, before any test module is collected -- see
#: ``_arm_real_config_guard_before_collection``'s own docstring for why this
#: cannot wait for a fixture. ``_never_touch_the_real_config`` below reuses
#: all four rather than redoing this work at its own (later) setup.
(_CONFIG_COLLECTION_DIR, _REAL_CONFIG_FILE, _REAL_CONFIG_DIR,
 _unwatch_real_config_guard) = _arm_real_config_guard_before_collection()


def _config_reads_stay_off_the_real_one(nodeid: str):
    """The body of ``_no_test_reads_the_real_config``, as a plain generator
    so test_real_config_guard.py can drive it."""
    mark = len(_RealConfig.reads)
    outer, _RealConfig.mark = _RealConfig.mark, mark
    try:
        yield
    finally:
        _RealConfig.mark = outer
    got = list(dict.fromkeys(_RealConfig.reads[mark:]))
    if got:
        # Names only, never values: that config holds the site and the
        # auth secrets, and a teardown error is printed wherever the run's
        # output goes.
        raise AssertionError(
            f"{nodeid} reached the developer's real config: "
            f"{', '.join(got)}. The session fixture moves every shared seam "
            f"built on it that is loaded when it runs, so this came around "
            f"them: a ConfigStore(), ProfileLibrary() or other store built "
            f"with no path, a seam built after that fixture ran, a reload "
            f"of astrodeck.config, or a path put back "
            f"(_never_touch_the_real_config, _sweep_off_the_real_config). "
            f"What the test answers is then this machine's config (issues "
            f"#341, #361, #436). Give "
            f"it a config of its own: the isolated_config fixture "
            f"(conftest), or ConfigStore(path=tmp_path / ...).")


@pytest.fixture(autouse=True)
def _no_test_reads_the_real_config(_never_touch_the_real_config, request):
    """Fail, at its own teardown, any test that read or wrote the
    developer's real config file, profiles directory or anything else in
    the real config directory (``_RealConfig``; issues #341, #361, #436). Its
    reads are noted by the watchers the session fixture
    installs; this fixture only attributes them.

    What it sees: the test body and every function-scoped fixture set up
    after it, which is every non-autouse one (``client``, ``env``, a
    lifespan), including a read from the cache of a store that loaded the
    real file earlier. What it does not see: a read made while a module-
    or session-scoped fixture sets up, before it.

    A READ AT IMPORT, BEFORE ANY FIXTURE, is a case this fixture itself
    cannot see either (it has not even been asked for yet) -- a probe of the
    whole suite once found none of those, in all 13 processes, but that
    probe was wrong: #675 traced one to test_w1_cooling_restore_daylight.py's
    module-level ``NIGHT_TS = _night_midpoint()``. ``_never_touch_the_real_
    config``'s own setup now fails loudly on exactly this case (see
    ``_arm_real_config_guard_before_collection``), by redirecting the store
    before collection starts (so the traced statement, and anything shaped
    like it, lands on a throwaway file) and asserting nothing was recorded
    against the real one regardless.

    Its cases, a throwaway run of this conftest with a deliberately leaky
    fixture, are in test_real_config_guard.py."""
    yield from _config_reads_stay_off_the_real_one(request.node.nodeid)


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


def _inert_gateway_probe(timeout_s: float) -> None:
    """The suite's gateway probe: "could not tell", having run nothing."""
    return None


def _inert_dns_probe(host: str, timeout_s: float) -> None:
    """The suite's DNS probe: "could not tell", having looked nothing up."""
    return None


@pytest.fixture(autouse=True)
def _no_test_probes_the_real_network(monkeypatch):
    """No test runs the relay client's system link probes (#521, H4; #571).

    Since H4 the relay client checks the rig's own network after every drop:
    ``route print`` (or ``/proc/net/route``), a ping of the default gateway,
    and a lookup of the relay's host. A client built directly gets no probes,
    which is what every relay test does. But ``run_relay_client``, the app's
    lifespan entry, reads ``SYSTEM_LINK_PROBES`` at call time, so a test that
    enables remote and enters ``TestClient(app)`` starts the production
    client. Two did (``test_remote_status.py``'s tunneled-request and
    device-token cases, on ``wss://relay.example.test``): the dial failed, the
    drop's check ran, and the suite pinged the developer's router and asked
    the resolver about the relay host, from a module whose docstring says "no
    network". Found by H4-RELAY's verifier, who instrumented the two probes
    and saw one call from each case.

    Replaced here, per test, by probes that answer "could not tell" and touch
    nothing, so the check still runs and says its line. A test that grades
    the system probes calls them by name (``_system_gateway_probe``) with the
    OS pieces patched, and ``test_the_lifespan_client_carries_the_system_probes``
    patches this name itself. The guard, with its mutant, is
    ``test_h4_no_test_probes_the_real_network.py``.
    """
    import astrodeck.remote.relay_client as relay_client
    monkeypatch.setattr(
        relay_client, "SYSTEM_LINK_PROBES",
        relay_client.LinkProbes(gateway=_inert_gateway_probe,
                                dns=_inert_dns_probe))
    yield


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


class IsolatedConfig:
    """A config of the test's own, and every rig fact read off it: what the
    ``isolated_config`` fixture hands a test (#341).

    * ``store``, a ``ConfigStore`` at ``dir / "astrodeck.json"``, SWEPT into
      every loaded ``astrodeck`` module that holds a ``config_store``. Swept,
      never listed: ``from .config import config_store`` binds the singleton
      into each importing module, and a fixture that patched three of them
      (config, hub, api.app, as test_flows_wizard_route.py's did) isolated
      three of them. ``sweep()`` again after ``create_app()``, which may
      import more.
    * ``dir``, set as ``CONFIG_DIR``; the profile library reads
      ``dir / "profiles"``, and the hub's cached active profile is dropped,
      so a profile a test saves is the one the rig facts read.
    * ``captures``, the capture root, every seam of it moved as the per-test
      root is (``_point_the_capture_root_at``).
    * ``hub.last_sky_angle`` None, and no camera or rotator in the hub's
      device map: the rig facts read the connected camera's sensor when the
      optics are not set and a connected rotator as a rotator, and the hub
      outlives every test on a worker. A test that wants either puts it
      there after this fixture.

    Everything through ``monkeypatch``, so the #227 guard sees nothing
    changed at teardown."""

    def __init__(self, mp: pytest.MonkeyPatch, root: Path) -> None:
        import astrodeck.config as config_mod
        import astrodeck.hub as hub_mod
        import astrodeck.profiles as profiles_mod
        self._mp = mp
        self.dir = root / "config"
        self.captures = root / "captures"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = config_mod.ConfigStore(path=self.dir / "astrodeck.json")
        #: ``module`` of every binding a sweep moved.
        self.swept: list[str] = []
        self.sweep()
        mp.setattr(config_mod, "CONFIG_DIR", self.dir)
        mp.setattr(profiles_mod.profiles, "_dir", self.dir / "profiles")
        hub = hub_mod.hub
        mp.setattr(hub, "_profile_cache_id", None)
        mp.setattr(hub, "_profile_cache", None)
        mp.setattr(hub, "last_sky_angle", None, raising=False)
        for role in ("camera", "rotator"):
            mp.delitem(hub.devices, role, raising=False)
        _point_the_capture_root_at(mp, self.captures)

    def sweep(self) -> list[str]:
        """Point every loaded ``astrodeck`` module's ``config_store`` at this
        store; returns the modules moved, all sweeps so far.

        THE KNOWN POSITIVES, asserted so a sweep that took nowhere fails
        here rather than passing every test that never reads config: the
        three modules the rig facts read through, ``astrodeck.config``
        (``profiles.resolve_optics`` looks it up per call), ``astrodeck.hub``
        (the active profile) and ``astrodeck.api.app`` when it is loaded
        (``_rig_facts``' standards)."""
        for name, mod in list(sys.modules.items()):
            if mod is None or not (name == "astrodeck"
                                   or name.startswith("astrodeck.")):
                continue
            held = getattr(mod, "config_store", None)
            if held is None or held is self.store:
                continue
            self._mp.setattr(mod, "config_store", self.store)
            self.swept.append(name)
        for name in ("astrodeck.config", "astrodeck.hub", "astrodeck.api.app"):
            mod = sys.modules.get(name)
            if name == "astrodeck.api.app" and mod is None:
                continue
            assert getattr(mod, "config_store", None) is self.store, (
                f"{name} reads a config store other than this test's: the "
                f"sweep missed it")
        return self.swept


@pytest.fixture
def isolated_config(tmp_path, monkeypatch) -> IsolatedConfig:
    """A config of the test's own (``IsolatedConfig``), under ``tmp_path``:
    ``tmp_path / "config"`` and ``tmp_path / "captures"``. The one
    isolation for a test that reads the rig's optics, its profiles or the
    rig facts built from them (#341); a fresh store has no optics until the
    test sets them."""
    return IsolatedConfig(monkeypatch, tmp_path)


def _sweep_config_store(monkeypatch: pytest.MonkeyPatch, store) -> list[str]:
    """Point every already-imported ``astrodeck`` module's bound
    ``config_store`` name at ``store``. Returns the module names patched, so
    a case can assert the sweep actually reached something.

    ``from .config import config_store`` binds the singleton BY REFERENCE
    into each importing module's own namespace at the time it is imported,
    not a live lookup of the name. A test that instead patches one object --
    ``monkeypatch.setattr(config_store, "cfg", ...)`` on the object its own
    `from astrodeck.config import config_store` just fetched -- only reaches
    callers that still hold THAT exact object. A module whose binding has
    diverged from it (a reload of ``astrodeck.config``, or some earlier
    test's own un-swept rebind of the name in one module: the #19 class)
    keeps reading its own copy, unpatched, and answers from whatever config
    that copy holds.

    This is ``test_no_route_leaks_the_site_coordinates.py``'s private sweep
    of the same name, pulled here so ``test_pier_side_is_published.py``'s
    ``_am5`` can use it too (#497): the AM5 driver (``zwo_am5.py``) binds its
    own module-level ``config_store`` at import, and was not one of the
    handful of modules ``_am5`` used to patch by name, so the far-side
    prediction read a store with no saved site and answered UNKNOWN instead
    of predicting a side, in one parallel run. Swept, not listed, for the
    same reason both copies give: a module imported tomorrow is covered
    without anyone having to remember either exists.
    """
    patched: list[str] = []
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "astrodeck" or name.startswith("astrodeck.")):
            continue
        if getattr(mod, "config_store", None) is None:
            continue
        monkeypatch.setattr(mod, "config_store", store, raising=False)
        patched.append(name)
    return patched


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


@pytest.fixture(autouse=True)
def _reset_active_auth_provider():
    """Reset the auth package's process-wide active-provider slot before AND
    after every test (#443, WP-27a).

    ``astrodeck.auth.deps._active_provider`` is a module-level singleton that
    ``create_app()`` installs into (via ``configure_provider_from_auth``), and
    that a good many tests also set directly to exercise a denied/limited
    caller. Nothing wound it back between tests, so whichever provider the
    PREVIOUS test on this worker left active was still active for the next
    one's fresh ``TestClient(create_app())`` -- that route then resolved
    whatever principal the leftover provider produced, not the open admin a
    test author building a plain app would expect. Order-dependent and
    worker-dependent: which provider (if any) was left depends on which
    tests xdist drew onto this worker and in what order, exactly the #227 /
    #341 shape applied to this singleton.

    Resetting at SETUP is what breaks the order dependency: every test now
    starts from the same known state (the open ``none`` provider,
    ``trust_loopback`` True, the session-secret interlock disarmed)
    regardless of what ran before it on this worker. The TEARDOWN reset is
    belt-and-suspenders, covering a test that sets a provider and then
    errors before its own cleanup runs, and the last test of a run.

    A test that wants a specific provider still calls ``set_active_provider``
    itself, same as today (``test_wcs_config_route_requires_site_optics_cap``
    and others already do, resetting in their own ``finally``) -- this
    fixture only guarantees what the test did NOT ask for.

    RED under mutant (the ``reset_active_provider()`` calls removed from both
    halves): see
    test_wcs_stamp.py::test_the_active_auth_provider_reset_leaves_the_open_default_for_the_next_request,
    which seeds a leftover provider itself and drives this fixture's real
    generator directly, so it goes red in the normal xdist suite run (W3
    integration, #443/WP-27 re-pin -- the old two-test pair relied on
    definition order, which ``-n 12 --dist worksteal`` does not preserve).
    """
    from astrodeck.auth import reset_active_provider
    reset_active_provider()
    yield
    reset_active_provider()


@pytest.fixture(autouse=True)
def _a_session_store_instance_dict_is_unshadowed():
    """Strip, after every test, any entry ``vars(session_store)`` holds that
    is merely a leftover of a ``monkeypatch.setattr(session_store, <name>,
    ...)`` call undoing itself (#522, WP-27b).

    ``monkeypatch.setattr`` reads the OLD value with ``getattr`` before it
    patches, and its own undo writes that old value back with ``setattr``.
    When nothing was set on the INSTANCE before -- the normal case, since
    until patched a method name resolves through the CLASS -- the value
    ``getattr`` read is the bound method the class descriptor handed out,
    and the undo plants that bound method into ``vars(session_store)``,
    where nothing was. Once there it stays for the rest of this worker
    process: instance-``__dict__`` lookup wins over the class for a plain
    (non-data-descriptor) attribute, so every LATER test that does
    ``monkeypatch.setattr(SessionStore, <name>, ...)`` -- patching the
    CLASS, the correct way to spy on or fault-inject a singleton -- patches
    something the instance never looks at again. The result is a spy that
    silently stops seeing calls, or a fault injection that silently stops
    firing, depending on which test ran first on this worker.

    Only entries that ARE one of ``SessionStore``'s own functions, bound to
    THIS instance, are removed; anything else a test legitimately put on the
    instance (plain data, not a shadowed method) is left alone.

    THE NAME IS LOAD-BEARING (mirrors ``_a_test_leaves_the_config_as_it_
    found_it`` above, same reason, quoted there in full): the shadow this
    strips is exactly what the ``monkeypatch`` FIXTURE's own undo writes, at
    ITS teardown, so this fixture's teardown must run AFTER that -- i.e.
    this fixture must be set up BEFORE anything pulls ``monkeypatch`` in, and
    pytest sets up one conftest's autouse fixtures in ``dir()`` order
    (alphabetical) and tears down in reverse. Sorting ahead of
    ``_captures_are_the_tests_own`` and ``_fast_sim_delays`` (both of which
    take ``monkeypatch``) is not enough on its own -- a THIRD autouse fixture
    added later, earlier alphabetically, that also takes ``monkeypatch``,
    would pull it in sooner still -- so this is named ``_a_...`` to sort
    ahead of every other autouse fixture in this file, not just today's two.
    It asks for no fixture that could pull ``monkeypatch`` in first.

    Measured: with this fixture named ``_unshadow_session_store_instance_
    dict`` (sorting AFTER ``_fast_sim_delays``), a throwaway two-test probe
    -- test 1 ``monkeypatch.setattr(session_store, "save", ...)`` and never
    undoing it itself, test 2 asserting no shadow -- still failed:

        AssertionError: leftover shadow present: {'save': <bound method
        SessionStore.save of <astrodeck.sequence.session.SessionStore
        object at 0x...>>}

    because THIS fixture's teardown ran, found nothing yet (monkeypatch
    had not undone its edit), and then monkeypatch's own teardown ran
    afterward and planted the shadow -- too late for this fixture to see it
    that test, and it was still there for the next one. Renamed to sort
    first, the same probe passed.

    RED under mutant (this fixture deleted): see
    test_w3_session_store_unshadow.py.

    EVERY SINGLETON, NOT ONLY THIS ONE (#833). #522 fixed the class for the
    one object it was found on. Wave 16's CI then went red on Linux because
    ``monkeypatch.setattr(app_module.engine, "abort", ...)`` planted the
    engine's bound ``abort`` the same way, and test_connect_rig_guard's
    ``monkeypatch.setattr(SequenceEngine, "abort", ...)`` spy, later on the
    same worker, never saw the forced connect's abort. The sweep now covers
    every module-level instance of an ``astrodeck`` class in every loaded
    ``astrodeck`` module (engine, hub, bus, stores), still removing only an
    entry that is the class's own function bound to that very instance,
    which changes no lookup. The name stays, since the #522 cases drive this
    fixture by it.

    RED under mutant "sweep only session_store" (``*_singletons()`` dropped
    from the swept list): ``pytest -n 0 -p no:randomly
    tests/test_w15_remote_profile_activate.py tests/test_connect_rig_guard.py``
    failed ``test_force_gets_through_and_aborts_the_run_first`` with
    ``assert [] == [True]``; the helper's own case is
    test_w16_singleton_unshadow.py.
    """
    yield
    from astrodeck.sequence.session import session_store
    for obj in [session_store, *_singletons()]:
        _strip_restored_bound_methods(obj)


def _singletons() -> list:
    """Every module-level instance of an ``astrodeck`` class in a loaded
    ``astrodeck`` module, once each. Read at teardown, after monkeypatch has
    put every module attribute back, so a patched-in stand-in is not what is
    swept."""
    seen: dict[int, object] = {}
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "astrodeck"
                               or name.startswith("astrodeck.")):
            continue
        for value in list(vars(mod).values()):
            if (isinstance(value, type)
                    or not type(value).__module__.startswith("astrodeck")
                    or not hasattr(value, "__dict__")):
                continue
            seen.setdefault(id(value), value)
    return list(seen.values())


def _strip_restored_bound_methods(obj) -> list[str]:
    """Remove from ``vars(obj)`` each entry that is ``type(obj)``'s own
    function bound to ``obj`` (what monkeypatch's undo leaves on an
    instance), and return the names removed."""
    import types
    try:
        entries = list(vars(obj).items())
    except TypeError:
        return []
    cls = type(obj)
    stale = [name for name, value in entries
             if isinstance(value, types.MethodType)
             and value.__self__ is obj
             and getattr(cls, name, None) is value.__func__]
    for name in stale:
        delattr(obj, name)
    return stale


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
    # **kw (W2 WP-11, #166): bus.log grew a ``site_derived`` keyword (and may
    # grow others), and a spy that only took (level, message, source) raised
    # TypeError the moment any call site passed it -- which is exactly what
    # blocked flagging hub.py's own meridian-flip line. The tuple this
    # fixture hands back is unchanged; unknown keywords are read and dropped,
    # never recorded, so a caller reading `bus_lines` sees the same three
    # fields as before.
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub", **kw: out.append(
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

    TWO TREES, ONE CLASS (#254). The same fault one directory over is a
    parallel agent editing a test file or this conftest under a running suite:
    the run then grades a mix of two versions of the tests. So `label` names
    the tree for the headline, and `pytest_configure` registers the class a
    second time over `server/tests`, as "astrodeck-tests-guard". `label`
    defaults to "source tree" and `why` to the paragraph above's finding, so a
    caller written before either existed (test_suite_guards_report_once.py
    builds it that way) reads the words it always did. The second guard
    catches a test file or conftest changed DURING the run; a mutant already
    in a test file when the run starts moves nothing afterwards, and is
    refused at the start by scripts/gate_run.py instead. The second guard's
    cases are in `test_w15_tests_tree_guard.py`.
    """

    HEADLINE = "THIS RUN IS INVALID: the source tree changed while it ran"

    _SOURCE_WHY = (
        "The tests that read their subject's source (inspect.getsource) "
        "were reading a file that no longer matched the code they were "
        "grading, and they can pass that way as easily as fail (issue "
        "#118), so no verdict above can be trusted, the passes included. "
        "Re-run on a quiescent tree.")

    def __init__(self, root: Path, label: str = "source tree",
                 why: str | None = None) -> None:
        self.root = root
        self.label = label
        # Set on the instance, so the class attribute above stays what a
        # reader of the old code expects for the default label.
        self.HEADLINE = f"THIS RUN IS INVALID: the {label} changed while it ran"
        self._why = why or self._SOURCE_WHY
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
        # Names only: paths under the watched root, never their contents.
        body = [
            self._why,
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
    # The same guard over the tests (#254): a test file or this conftest
    # edited under a running suite makes the run grade two versions of it.
    config.pluginmanager.register(
        _TheTreeMustNotMove(
            _SERVER_DIR / "tests", label="test tree",
            why=("A test file or conftest.py changed while the suite was "
                 "running, so the run graded a mix of two versions of the "
                 "tests: modules already imported are the old text, files "
                 "collected or read later are the new, and a test that "
                 "scrapes a test file's source reads shifted lines (issue "
                 "#254). No verdict above can be trusted, the passes "
                 "included. Re-run in a copy of the tree that nothing else "
                 "is editing.")),
        "astrodeck-tests-guard")
    config.pluginmanager.register(
        _TheRealCapturesStayUntouched(_REAL_CAPTURE_ROOTS),
        "astrodeck-captures-guard")
