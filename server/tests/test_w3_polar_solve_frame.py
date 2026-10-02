# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-30a (b): the polar alignment solve writes its frame through
``_write_solve_frame`` / ``_retire_solve_frame``, with the same retry every
other solve path already got in H4 (#189, #532 hub half). See
``test_h4_solve_frame_unique_names.py`` for that contract in full; this file
pins the polar half #532's own adjudication comment called out as remaining.

2026-09-29, 0.3.36 on the rig, the NGC 1499 mosaic (#532): an agent
copying the previous solve frame off the rig over scp made the next solve
fail outright. H4 fixed that for every hub-run solve, but the polar alignment
still wrote every TPPA solve frame to the fixed ``captures/_solve/polar.fits``
with a bare ``save_fits`` and no retry. A sharing violation there surfaces as
a bare ``PermissionError``, which ``_solve_until_it_works`` does not retry
because it retries only ``DeviceError`` -- so a reader holding ``polar.fits``
ends the whole polar run instead of costing one retried write.
"""
from __future__ import annotations

import errno
import re
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.hub as hub_module
from astrodeck import providers
from astrodeck.hub import SolveFrameTransient
from astrodeck.polar.native import _capture_and_solve

pytestmark = pytest.mark.asyncio

_UNIQUE = re.compile(r"^(?P<kind>[a-z_]+)-[0-9a-f]{12}\.fits$")


def _sharing_violation(path) -> PermissionError:
    """What Windows raises when another process holds ``path``. The
    ``winerror`` is set by hand, so the case runs on any platform (same
    helper as test_h4_solve_frame_unique_names.py)."""
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


async def _solve(sim_hub):
    return await _capture_and_solve(sim_hub, providers.pick_solver(sim_hub), None)


async def test_a_polar_solve_writes_a_unique_frame_and_retires_it(sim_hub,
                                                                   tmp_path):
    """Two consecutive polar solves hand the solver two different files,
    named ``polar-<token>.fits`` under ``_solve`` -- not the fixed
    ``polar.fits`` every version before this wrote -- and each is retired
    (gone, with ``latest-polar.fits`` left as the inspection copy) once its
    solve is done.

    MUTATION 'fixed polar.fits path' (put back the pre-fix
    ``tmp = CAPTURE_DIR / "_solve" / "polar.fits"`` and a bare
    ``save_fits`` call, with no retirement). Observed: "AssertionError: the
    polar solve still writes the fixed pre-#532 path:
    ...\\_solve\\polar.fits".
    """
    await _solve(sim_hub)
    await _solve(sim_hub)

    folder = tmp_path / "_solve"
    left = sorted(p.name for p in folder.iterdir())
    assert left == ["latest-polar.fits"], (
        f"the polar solve still writes the fixed pre-#532 path or leaves a "
        f"frame behind: {left}")
    assert not (folder / "polar.fits").exists(), (
        "the polar solve still writes the fixed pre-#532 path: "
        f"{folder / 'polar.fits'}")


async def test_a_polar_frame_write_held_once_then_free_still_solves(
        sim_hub, monkeypatch):
    """A single sharing violation on the polar frame's write -- the exact
    #532 scenario (a copy off the rig mid-write) -- is retried under a new
    name, and the solve still succeeds. Before this fix the bare
    ``save_fits`` call had no retry at all: the first hold ended the solve.

    MUTATION 'no retry' (drop the ``_write_solve_frame`` retry loop back to a
    bare ``save_fits`` call). Observed: "PermissionError: [WinError 32] The
    process cannot access the file because it is being used by another
    process: '...\\_solve\\polar-....fits'" raised OUT of ``_capture_and_solve``
    instead of the solve succeeding.
    """
    held = {"n": 0}

    def fail(path):
        if held["n"] < 1:
            held["n"] += 1
            return _sharing_violation(path)
        return None

    tried = _failing_writes(monkeypatch, fail)
    monkeypatch.setattr(hub_module, "SOLVE_WRITE_BACKOFF_S", 0.01)

    frame, result, geom = await _solve(sim_hub)

    assert result.success, result
    assert len(tried) == 2 and tried[0] != tried[1], tried
    assert all(_UNIQUE.match(p.name).group("kind") == "polar" for p in tried), tried


async def test_a_polar_frame_held_on_every_try_raises_a_retryable_device_error(
        sim_hub, monkeypatch):
    """Exhausted retries raise ``SolveFrameTransient`` -- a ``DeviceError`` --
    not the bare ``PermissionError`` the pre-fix code let through. That is
    the point of (b): ``_solve_until_it_works`` already retries any
    ``DeviceError`` it catches from a failed solve (that loop is untouched),
    but it does NOT retry a bare ``PermissionError``, so before this fix a
    reader holding ``polar.fits`` ended the whole polar run (#532) instead of
    costing one retried measurement.

    MUTATION 'no retry' (same as above, a bare ``save_fits``). Observed: the
    write raises plain ``PermissionError`` instead of ``SolveFrameTransient``
    -- "Failed: DID NOT RAISE <class 'astrodeck.hub.SolveFrameTransient'>",
    and separately "AssertionError: assert False" on the
    ``isinstance(..., DeviceError)`` check since a bare ``PermissionError``
    is not one.
    """
    _failing_writes(monkeypatch, _sharing_violation)
    monkeypatch.setattr(hub_module, "SOLVE_WRITE_BACKOFF_S", 0.01)

    with pytest.raises(SolveFrameTransient):
        await _solve(sim_hub)
