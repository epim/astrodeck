# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every plate-solve frame gets its own name, and a sharing violation on its
write is retried, never a centring strike (#532, hub half; H4 contract 1).

2026-09-29, 0.3.36 on the rig, the NGC 1499 mosaic:

    centering: plate solve failed ([WinError 32] The process cannot access
    the file because it is being used by another process:
    '...\\captures\\_solve\\solve.fits'); using raw GoTo

An agent was copying the previous ``_solve/solve.fits`` off the rig as the
engine wrote the next frame to the same fixed path. Every solve path wrote one
fixed file (``solve.fits``, ``rotate.fits``, ``rotsync.fits``, the two
guide-offset frames), so any reader holding the last one (a copy, an
antivirus scan, an indexer, a preview) failed the next solve outright, and on
a mosaic that failure counted toward setting the panel aside.

Now each frame is written to ``_solve/<kind>-<token>.fits``, handed to the
solver, and renamed to ``latest-<kind>.fits`` (the inspection copy) once the
solver is done, with the solver's sidecars deleted; a sharing violation on the
write is tried again under a new name after an ``asyncio.sleep`` backoff, and
when the bounded tries run out the centring result carries
``solve_transient: True`` (H4 contract 1), which the engine does not count.

The write is failed through ``astrodeck.hub.save_fits``, the seam the hub
writes every solve frame through. Every mutation named below was run in a
private scratch copy of ``server/`` (issue #254, #475), never in the shared
tree. The failure each produced is recorded verbatim on the test that caught
it.
"""
from __future__ import annotations

import asyncio
import errno
import re
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.hub as hub_module
from astrodeck.solve.simsolver import SimSolver

RA, DEC = 5.0, 10.0
_UNIQUE = re.compile(r"^(?P<kind>[a-z_]+)-[0-9a-f]{12}\.fits$")


@pytest.fixture(autouse=True)
def _on_target(sim_hub):
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC


@pytest.fixture
def solved(monkeypatch):
    """Every path the solver was handed, in order, and whether the file was
    there when it was handed over. Delegates to the real ``SimSolver``."""
    seen: list[tuple[Path, bool]] = []
    real = SimSolver.solve

    async def spy(self, fits_path, **kw):
        seen.append((Path(fits_path), Path(fits_path).is_file()))
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", spy)
    return seen


@pytest.fixture
def quick_backoff(monkeypatch):
    """A backoff this suite can recognise and afford: 13 ms, then 26 ms and
    52 ms. Every ``asyncio.sleep`` is recorded, so the backoff is seen to be
    awaited, never slept on the loop."""
    monkeypatch.setattr(hub_module, "SOLVE_WRITE_BACKOFF_S", 0.013)
    slept: list[float] = []
    real = asyncio.sleep

    async def sleep(delay, *a, **kw):
        slept.append(delay)
        return await real(delay, *a, **kw)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return slept


def _sharing_violation(path) -> PermissionError:
    """What Windows raises when another process holds ``path``. The
    ``winerror`` is set by hand, so the case runs on any platform."""
    e = PermissionError(errno.EACCES, "The process cannot access the file "
                        "because it is being used by another process",
                        str(path))
    e.winerror = 32
    return e


def _failing_writes(monkeypatch, fail) -> list[Path]:
    """``hub.save_fits`` raising ``fail(path)`` for each path it returns an
    exception for (None writes the file for real). Returns every path a write
    was tried at, in order."""
    tried: list[Path] = []
    real = hub_module.save_fits

    def save_fits(frame, path, **kw):
        tried.append(Path(path))
        err = fail(Path(path))
        if err is not None:
            raise err
        return real(frame, path, **kw)

    monkeypatch.setattr(hub_module, "save_fits", save_fits)
    return tried


# ------------------------------------------------------------- the names


async def test_two_solves_write_two_paths(sim_hub, solved, tmp_path):
    """Two consecutive solves hand the solver two different files, each named
    ``solve-<token>.fits`` under ``_solve``, each there when handed over and
    gone once the solve is done; the newest is kept as the inspection copy.

    Mutation 'fixed solve.fits path' (``_write_solve_frame`` names the file
    ``f"{kind}.fits"``) went red here, and on the three retry cases (every
    try one name; the rotate case's fault, keyed on a ``rotate-`` name,
    never fired):

        >       assert len(paths) == 2 and paths[0] != paths[1], paths
        E       AssertionError: [WindowsPath('C:/Users/bear/AppData/Local/Temp/pytest-of-bear/pytest-26485/test_two_solves_write_two_path0/_solve/solv...Path('C:/Users/bear/AppData/Local/Temp/pytest-of-bear/pytest-26485/test_two_solves_write_two_path0/_solve/solve.fits')]

    Mutation 'frames left behind' (``_retire_solve_frame`` returns before it
    does anything) went red here:

        >           assert not p.exists(), f"{p.name} outlived its solve"
        E           AssertionError: solve-a540fb8b4037.fits outlived its solve
    """
    await sim_hub.solve_and_sync(0.05)
    await sim_hub.solve_and_sync(0.05)

    paths = [p for p, _ in solved]
    assert len(paths) == 2 and paths[0] != paths[1], paths
    for p, present in solved:
        assert p.parent == tmp_path / "_solve", p
        m = _UNIQUE.match(p.name)
        assert m and m.group("kind") == "solve", p.name
        assert present, f"the solver was handed {p.name} before it existed"
        assert not p.exists(), f"{p.name} outlived its solve"
    assert (tmp_path / "_solve" / "latest-solve.fits").is_file()


async def test_the_solver_never_reads_the_inspection_copy(sim_hub, solved,
                                                          tmp_path):
    """The stable copy is written after the solve and never read by it: a
    reader holding ``latest-solve.fits`` open (the #532 copy, today) cannot
    reach the file a solve is using. Held open here while the next solve
    runs, which must still solve; on Windows the rename onto the held copy
    fails and the frame is deleted instead, so nothing is left either way.

    Mutation 'the solve reads the stable copy' (``_write_solve_frame`` names
    the file ``latest-<kind>.fits``) went red here, and on the two-paths case
    and the three retry cases:

        >       assert not any(p.name.startswith("latest-") for p, _ in solved), solved
        E       AssertionError: [(WindowsPath('C:/Users/bear/AppData/Local/Temp/pytest-of-bear/pytest-26489/test_the_solver_never_reads_th0/_solve/latest-solve.fits'), True)]
        E       assert not True

    'frames left behind' went red here too, with no copy kept:

        >       assert latest.is_file()
        E       AssertionError: assert False
    """
    await sim_hub.solve_and_sync(0.05)
    latest = tmp_path / "_solve" / "latest-solve.fits"
    assert latest.is_file()
    assert not any(p.name.startswith("latest-") for p, _ in solved), solved
    with open(latest, "rb"):
        out = await sim_hub.solve_and_sync(0.05)
    assert out["solver"] == "Simulator", out
    assert not any(p.name.startswith("latest-") for p, _ in solved), solved
    left = sorted(p.name for p in (tmp_path / "_solve").iterdir())
    assert left == ["latest-solve.fits"], left


# ------------------------------------------------------ the sharing violation


async def test_a_write_held_twice_then_free_centres(sim_hub, monkeypatch,
                                                    quick_backoff):
    """The write meets a sharing violation twice and then goes through: the
    goto centres, the three tries were three different names, and the two
    waits between them were awaited (13 ms, then 26 ms).

    Mutation 'no retry' (the ``if not _sharing_violation(e): raise`` test
    made ``if True:``) went red here, and on both exhausted-retry cases:

        >       assert result["centered"] is True, result
        E       AssertionError: {'attempts': 1, 'centered': False, 'error_arcmin': None, 'rotation': None, ...}
        E       assert False is True

    Mutation 'blocking backoff' (``time.sleep(wait)`` in place of ``await
    asyncio.sleep(wait)``) went red here alone:

        >       assert 0.013 in quick_backoff and 0.026 in quick_backoff, quick_backoff
        E       AssertionError: [0.0, 2.0, 0.0, 0.0, 0.0, 0.0, ...]
        E       assert (0.013 in [0.0, 2.0, 0.0, 0.0, 0.0, 0.0, ...])
    """
    held = {"n": 0}

    def fail(path):
        if held["n"] < 2:
            held["n"] += 1
            return _sharing_violation(path)
        return None

    tried = _failing_writes(monkeypatch, fail)

    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["centered"] is True, result
    assert "solve_transient" not in result, result
    first = tried[:3]
    assert len(set(first)) == 3, first
    assert all(_UNIQUE.match(p.name).group("kind") == "solve" for p in first)
    assert 0.013 in quick_backoff and 0.026 in quick_backoff, quick_backoff


async def test_an_exhausted_retry_carries_solve_transient(sim_hub, monkeypatch,
                                                          quick_backoff,
                                                          bus_lines):
    """Held on every try: the centring gives up on the solve, as for any
    failed solve, and says the failure was this computer's
    (``solve_transient: True``, H4 contract 1). The write was tried
    ``SOLVE_WRITE_ATTEMPTS`` times, each under a new name, with nothing left
    behind.

    Mutation 'key dropped' (the ``solve_transient`` merge removed from the
    solve-failed return in ``goto_and_center``) went red here alone:

        >       assert result.get("solve_transient") is True, result
        E       AssertionError: {'attempts': 1, 'centered': False, 'error_arcmin': None, 'rotation': None, ...}
        E       assert None is True

    Mutation 'the path in the message' (``({e})`` put back in the
    ``SolveFrameTransient`` message) went red here alone:

        >       assert not any(p.stem in failed[0] for p in tried), failed[0]
        E       AssertionError: centering: plate solve failed (the solve frame could not be written: another process held the file on every try, each under a new name ([WinError 32] The process cannot access the file because it is being used by another process: 'C:\\\\Users\\\\bear\\\\AppData\\\\Local\\\\Temp\\\\pytest-of-bear\\\\pytest-26626\\\\test_an_exhausted_retry_carrie0\\\\_solve\\\\solve-b4b7a11d68b0.fits') (a Win
        E       assert not True
    """
    tried = _failing_writes(monkeypatch, _sharing_violation)

    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["centered"] is False and result["solve_failed"] is True
    assert result.get("solve_transient") is True, result
    assert len(tried) == hub_module.SOLVE_WRITE_ATTEMPTS, tried
    assert len(set(tried)) == len(tried), tried
    assert not any(p.exists() for p in tried), tried
    # THE FAILURE IS IN WORDS: a failed solve's message can become a hold's
    # reason, and the names change on every try, so none of them is in it.
    failed = [m for _, m, src in bus_lines
              if src == "solve" and m.startswith("centering: plate solve failed")]
    assert len(failed) == 1 and "sharing violation" in failed[0], bus_lines
    assert not any(p.stem in failed[0] for p in tried), failed[0]


async def test_a_rotate_frame_held_on_every_try_carries_it_too(
        sim_hub, monkeypatch, quick_backoff):
    """Only the rotate's frames are held: the rotation is skipped (a
    connected rotator that tried and failed), the centring then centres, and
    the result still says a solve failed for this computer's reasons, since
    the engine's rotation check would otherwise count it against the panel.

    Mutation 'rotate transient dropped' (the ``solve_transient =
    isinstance(e, SolveFrameTransient)`` line removed from the rotate
    block) went red here alone:

        >       assert result.get("solve_transient") is True, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'rotation': None, ...}
        E       assert None is True
    """
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg, rig.rotator_mech_deg = 20.0, 10.0
    _failing_writes(monkeypatch, lambda p: _sharing_violation(p)
                    if p.name.startswith("rotate-") else None)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert result["centered"] is True, result
    assert result.get("rotation_skipped") is True, result
    assert result.get("solve_transient") is True, result


@pytest.mark.parametrize("error", [
    OSError(errno.ENOSPC, "No space left on device"),
    PermissionError(errno.EACCES, "Access is denied"),
], ids=["ENOSPC", "EACCES without a sharing violation"])
async def test_a_failure_that_is_not_a_sharing_violation_is_not_transient(
        sim_hub, monkeypatch, quick_backoff, error):
    """Control: a full disk, or a real permission problem, fails the solve
    as it always did, at once: one try, no ``solve_transient``. Retrying it
    would only delay the same answer, and it is a fault the operator must
    see, not one the engine should forgive.

    Mutation 'every OSError is transient' (``_sharing_violation`` returns
    True for any ``OSError``, and ``except OSError`` in the write) went red
    on both cases:

        >       assert "solve_transient" not in result, result
        E       AssertionError: {'attempts': 1, 'centered': False, 'error_arcmin': None, 'rotation': None, ...}
        E       assert 'solve_transient' not in {'attempts': 1, 'centered': False, 'error_arcmin': None, 'rotation': None, ...}
    """
    tried = _failing_writes(monkeypatch, lambda p: error)

    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["centered"] is False and result["solve_failed"] is True
    assert "solve_transient" not in result, result
    assert len(tried) == 1, tried


# ------------------------------------------------------------ nothing left


async def test_solve_holds_no_leftovers(sim_hub, monkeypatch, tmp_path):
    """Every kind of solve frame, several times over, with a solver that
    leaves ASTAP's sidecars (``.ini`` and ``.wcs``) beside each frame, as
    ASTAP does when it gives no result: afterwards ``_solve`` holds only the
    one inspection copy per kind.

    Mutation 'frames left behind' (``_retire_solve_frame`` returns before it
    does anything) went red here:

        >       assert left == expected, left
        E       AssertionError: ['guide_offset_guide-a638bbf68a73.fits', 'guide_offset_guide-a638bbf68a73.ini', 'guide_offset_guide-a638bbf68a73.wcs', 'guide_offset_main-10c113f209dd.fits', 'guide_offset_main-10c113f209dd.ini', 'guide_offset_main-10c113f209dd.wcs', ...]

    Mutation 'sidecars left' (the sidecar loop in
    ``_retire_solve_frame_sync`` given nothing to loop over) went red here
    alone:

        >       assert left == expected, left
        E       AssertionError: ['guide_offset_guide-4edb80620857.ini', 'guide_offset_guide-4edb80620857.wcs', 'guide_offset_main-0a8d24d15d7c.ini', 'guide_offset_main-0a8d24d15d7c.wcs', 'latest-guide_offset_guide.fits', 'latest-guide_offset_main.fits', ...]
    """
    real = SimSolver.solve

    async def astap_like(self, fits_path, **kw):
        p = Path(fits_path)
        p.with_suffix(".ini").write_text("PLTSOLVD=F\n")
        p.with_suffix(".wcs").write_text("END\n")
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", astap_like)
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg, rig.rotator_mech_deg = 20.0, 10.0

    for _ in range(3):
        await sim_hub.solve_and_sync(0.05)
    await sim_hub.rotate_to_pa(45.0)
    await sim_hub.sync_rotator_to_sky()
    await sim_hub.measure_guide_offset(exposure_s=0.05, guide_exposure_s=0.05)

    left = sorted(p.name for p in (tmp_path / "_solve").iterdir())
    expected = sorted(f"latest-{k}.fits" for k in (
        "solve", "rotate", "rotsync", "guide_offset_main",
        "guide_offset_guide"))
    assert left == expected, left


async def test_a_killed_solve_s_frame_is_swept_by_the_next(sim_hub, tmp_path):
    """A server killed mid-solve leaves its frame behind, and a version
    before #532 left its fixed ``solve.fits``: the next solve of that kind
    removes both. A frame of that kind younger than
    ``SOLVE_LEFTOVER_AGE_S`` may be a solve still running, and stays.

    Mutation 'no sweep' (``_write_solve_frame`` skips
    ``_sweep_solve_leftovers``) went red here:

        >       assert not old.exists() and not legacy.exists(), \\
        E       AssertionError: ['latest-solve.fits', 'solve-0123456789ab.fits', 'solve-0123456789ab.ini', 'solve-fedcba987654.fits', 'solve.fits']
        E       assert (not True)

    Mutation 'the sweep ignores age' (the ``st_mtime < cutoff`` test made
    ``True``) went red here:

        >       assert fresh.exists(), "a solve that may still be running lost its frame"
        E       AssertionError: a solve that may still be running lost its frame
        E       assert False
    """
    import os
    import time
    folder = tmp_path / "_solve"
    folder.mkdir()
    old = folder / "solve-0123456789ab.fits"
    old_ini = folder / "solve-0123456789ab.ini"
    legacy = folder / "solve.fits"
    fresh = folder / "solve-fedcba987654.fits"
    for p in (old, old_ini, legacy, fresh):
        p.write_bytes(b"x")
    stale = time.time() - hub_module.SOLVE_LEFTOVER_AGE_S - 60.0
    for p in (old, old_ini):
        os.utime(p, (stale, stale))

    await sim_hub.solve_and_sync(0.05)

    assert not old.exists() and not legacy.exists(), \
        sorted(p.name for p in folder.iterdir())
    assert not old_ini.exists()
    assert fresh.exists(), "a solve that may still be running lost its frame"
