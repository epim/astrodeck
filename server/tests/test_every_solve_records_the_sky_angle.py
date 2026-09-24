"""Every plate solve of an imaging-camera frame goes through the sky-angle
recorder.

The owner's requirement (2026-09-23): whenever a plate solve runs anywhere in
AstroDeck, the camera's sky position angle it measured is recorded AND used to
calibrate the rotator, so the rotator's reported sky angle is always as fresh as
the latest solve. Before it, six functions called a solver and two of them fed
the angle to the rotator; the goto centring, the resume re-centre, polar
alignment, the guide-scope offset and the per-frame WCS stamp measured it and
dropped it. ``astrodeck.sky_angle.note_solved_rotation`` is the one function
that records and calibrates. This is the test that finds its callers.

A STATIC scan, for the reason ``test_every_site_consumer_asks_whether_there_is_
a_site`` gives: a runtime test only reaches the solve paths somebody thought to
drive, and the defect being guarded against is the seventh path nobody thought
about.

Reading this when it fails: you have added, or moved, a call to some solver's
``.solve(...)``. The function that makes the call must also call
``note_solved_rotation`` after it (with an ``exposure_context`` read before the
exposure), or be listed in ``NOT_ROUTED`` below with the reason it must not be.
A nested helper does not inherit its parent's call: it is judged on its own
body, so a helper that solves and returns is either routed itself or listed.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "astrodeck"

RECORDER = "note_solved_rotation"

#: Solve call sites that deliberately do NOT record a sky angle, with why. May
#: shrink freely; grows only by a deliberate edit that says why. Checked for
#: staleness both ways.
NOT_ROUTED: dict[str, str] = {
    "hub.py:Hub.measure_guide_offset._solve_guide_frame":
        "the GUIDE camera's frame. Its position angle is the guide train's, "
        "which says nothing about the imaging camera the rotator turns; "
        "recording it would publish the wrong camera's angle and calibrating "
        "from it would rotate every later framing by the angle between the two "
        "cameras. The imaging half of the same measurement is routed, in "
        "measure_guide_offset itself.",
}

#: The call sites that existed when this test was written, all routed. Held as
#: a KNOWN POSITIVE: if the scan stops finding any of these, the detector has
#: gone blind and every other case in this file passes vacuously.
KNOWN_ROUTED = {
    "hub.py:Hub._solve_and_stamp",
    "hub.py:Hub.measure_guide_offset",
    "hub.py:Hub.solve_and_sync",
    "hub.py:Hub.sync_rotator_to_sky",
    "hub.py:Hub._rotate_to_pa_attempts",
    "polar/native.py:_capture_and_solve",
}

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _is_solve_call(node: ast.AST) -> bool:
    """``<anything>.solve(...)``. Loose on the receiver on purpose: ``solver``,
    ``self._solver``, ``AstapSolver(exe)`` are all spellings a new caller could
    use, and a false positive costs one allowlist line."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "solve")


def _is_recorder_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Attribute) and f.attr == RECORDER)
            or (isinstance(f, ast.Name) and f.id == RECORDER))


def _own_body(fn: ast.AST):
    """Every node in ``fn``'s own body, NOT descending into nested functions,
    lambdas or classes: those are judged as scopes of their own."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _functions(tree: ast.AST, prefix: str = ""):
    """``(qualname, node)`` for every function, with class and enclosing
    function names folded into the qualname."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            yield from _functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = f"{prefix}{node.name}"
            yield q, node
            yield from _functions(node, f"{q}.")
        else:
            yield from _functions(node, prefix)


def _scan_tree(tree: ast.AST, rel: str) -> dict[str, dict]:
    """``{"rel:qualname": {"solves": [line...], "routed": bool}}`` for every
    function whose own body calls ``.solve(``. Routed means a recorder call
    follows EACH solve call in the same body."""
    out: dict[str, dict] = {}
    for qual, fn in _functions(tree):
        solves = [n.lineno for n in _own_body(fn) if _is_solve_call(n)]
        if not solves:
            continue
        notes = [n.lineno for n in _own_body(fn) if _is_recorder_call(n)]
        routed = all(any(n > s for n in notes) for s in solves)
        out[f"{rel}:{qual}"] = {"solves": sorted(solves), "routed": routed}
    return out


def _scan() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.update(_scan_tree(tree, path.relative_to(ROOT).as_posix()))
    return found


def test_every_solve_records_the_sky_angle_or_is_listed():
    """The gate.

    MUTATION: delete the ``note_solved_rotation`` call from
    ``Hub.solve_and_sync``. Observed: "AssertionError: these call a plate
    solver and never hand the result to note_solved_rotation, so the sky angle
    they measure is dropped and the rotator's reported PA goes stale:" naming
    ``hub.py:Hub.solve_and_sync``. Same for the polar call site.
    """
    unrouted = {k: v for k, v in _scan().items()
                if not v["routed"] and k not in NOT_ROUTED}
    assert not unrouted, (
        "these call a plate solver and never hand the result to "
        f"{RECORDER}, so the sky angle they measure is dropped and the "
        "rotator's reported PA goes stale:\n"
        + "\n".join(f"  {k} (solve on lines {v['solves']})"
                    for k, v in sorted(unrouted.items()))
        + "\n\nCall astrodeck.sky_angle.note_solved_rotation after the solve "
          "(with an exposure_context read before the exposure), or add the "
          "function to NOT_ROUTED with the reason.")


def test_the_allowlist_does_not_rot():
    """An entry that no longer solves, or that now routes, is a claim nobody is
    checking.

    MUTATION: add a bogus entry ``"hub.py:Hub.nothing"`` to ``NOT_ROUTED``.
    Observed: "AssertionError: listed as deliberately unrouted, but no longer a
    solve site: ['hub.py:Hub.nothing']".

    MUTATION: route ``_solve_guide_frame``'s result through the recorder.
    Observed: "AssertionError: listed as deliberately unrouted, but they record
    the angle now: ['hub.py:Hub.measure_guide_offset._solve_guide_frame']".
    """
    found = _scan()
    gone = sorted(set(NOT_ROUTED) - set(found))
    assert not gone, (
        f"listed as deliberately unrouted, but no longer a solve site: {gone}")
    now_routed = sorted(k for k in NOT_ROUTED if found[k]["routed"])
    assert not now_routed, (
        f"listed as deliberately unrouted, but they record the angle now: "
        f"{now_routed}. Take them off NOT_ROUTED.")


def test_the_scan_still_sees_the_known_solve_sites():
    """The known positive. A detector that matches nothing passes both cases
    above forever.

    MUTATION: make ``_is_solve_call`` return False unconditionally. Observed:
    "AssertionError: the scan no longer sees these solve sites:" listing all
    six. The gate PASSED under that mutation (it has nothing to complain
    about), which is exactly why this case exists; the staleness check failed
    only because its one entry vanished.
    """
    found = _scan()
    missing = sorted(KNOWN_ROUTED - set(found))
    assert not missing, f"the scan no longer sees these solve sites: {missing}"
    unrouted = sorted(k for k in KNOWN_ROUTED if not found[k]["routed"])
    assert not unrouted, f"known solve sites stopped routing: {unrouted}"


def test_the_scan_judges_a_nested_helper_on_its_own_body():
    """The guard on the guard, on the synthetic shapes the real tree uses.

    MUTATION: make ``_own_body`` descend into nested functions (drop the
    ``continue`` for ``_SCOPES``). Observed: "AssertionError: a recorder inside
    a nested helper routed its parent".
    """
    src = (
        "async def plain(solver):\n"
        "    return await solver.solve(p)\n"
        "\n"
        "async def routed(solver, hub, ctx):\n"
        "    r = await solver.solve(p)\n"
        "    await note_solved_rotation(hub, r, source='x', context=ctx)\n"
        "\n"
        "async def recorded_first(solver, hub, ctx, r0):\n"
        "    await _sky_angle.note_solved_rotation(hub, r0, source='x', context=ctx)\n"
        "    return await solver.solve(p)\n"
        "\n"
        "async def outer(solver, hub, ctx):\n"
        "    async def helper(p):\n"
        "        return await solver.solve(p)\n"
        "    r = await solver.solve(p)\n"
        "    await note_solved_rotation(hub, r, source='x', context=ctx)\n"
        "    return await helper(p)\n"
        "\n"
        "async def recorder_in_helper_only(solver, hub, ctx):\n"
        "    r = await solver.solve(p)\n"
        "    async def rec(r):\n"
        "        await note_solved_rotation(hub, r, source='x', context=ctx)\n"
        "    return r\n"
        "\n"
        "class K:\n"
        "    async def m(self):\n"
        "        return await self._solver.solve(p)\n")
    found = _scan_tree(ast.parse(src), "t.py")
    assert found["t.py:plain"]["routed"] is False, "an unrouted solve read as routed"
    assert found["t.py:routed"]["routed"] is True, "a routed solve read as unrouted"
    assert found["t.py:recorded_first"]["routed"] is False, (
        "a recorder call BEFORE the solve cannot be recording that solve")
    assert found["t.py:outer"]["routed"] is True
    assert found["t.py:outer.helper"]["routed"] is False, (
        "a nested helper inherited its parent's recorder call")
    assert found["t.py:recorder_in_helper_only"]["routed"] is False, (
        "a recorder inside a nested helper routed its parent")
    assert "t.py:K.m" in found, "a method's solve call is invisible to the scan"
