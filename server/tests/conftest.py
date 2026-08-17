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
    wait. PACING ONLY: the sim derives every VALUE (RA/Dec offsets, star/frame
    pixels, calibration geometry, guiding corrections) from the logical/virtual
    clock + the requested exposure/pulse, never from elapsed wall-clock dwell, so
    results stay bit-identical — the suite just stops paying wall-clock for the
    sim's fake time. ONE end-to-end timing-realism anchor
    (test_native_guider_e2e.py::test_native_guider_converges_on_sim) opts back
    OUT via its ``_real_dwell`` fixture."""
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
    real = config_mod.config_store._path
    real_dir = config_mod.CONFIG_DIR
    with tempfile.TemporaryDirectory(prefix="astrodeck-test-config-") as d:
        config_mod.config_store._path = Path(d) / "astrodeck.json"
        config_mod.config_store._cfg = None      # drop anything already loaded
        config_mod.CONFIG_DIR = Path(d)
        assert config_mod.config_store._path != real
        yield
    config_mod.config_store._path = real
    config_mod.config_store._cfg = None
    config_mod.CONFIG_DIR = real_dir


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
