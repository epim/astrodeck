"""Guard: production code must not branch on a test flag (WP-31 integration
follow-up, backlog wave 4, owner-approved 2026-09-30).

WP-31 wrote two real-time holds in engine.py (``_await_target_window``,
``_hold_for_light``) as ``os.environ.get("ASTRODECK_FAST_TEST") == "1"``
reads -- the same env var ``devices/sim.py`` and ``solve/simsolver.py`` use
for their own pacing. Those two are device/solver SIMULATORS, built only to
stand in for hardware that is not there, where faking elapsed time is the
whole point (their own docstrings: "read LIVE on every call"). A probe or a
simulator SERVER started with that flag set, to get the sim's fast pacing,
would then also silently skip holding for a target's own window or for
light -- a behaviour change a deployment flag must never cause.

engine.py now reads an explicit module switch
(``astrodeck.sequence.engine._SKIP_TARGET_HOLDS_FOR_TEST``) instead, which
only ``server/tests/conftest.py``'s autouse fixture and
``test_w4_target_window_and_light_hold.py``'s own opt-out test ever touch.
This file is the backstop: every ``.py`` file under ``server/astrodeck``,
except ``devices/sim.py`` and ``solve/simsolver.py``, must be free of the
literal string ``"ASTRODECK_FAST_TEST"`` in its own source.

String CONSTANTS only (via ``ast``), not raw text: a plain ``#`` comment
documenting the exemption (engine.py's own, at ``_SKIP_TARGET_HOLDS_FOR_
TEST``'s definition) is not part of the parsed tree and never trips this,
so the guard cannot be defeated by turning a real read into a comment that
still describes one.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ASTRODECK_DIR = Path(__file__).resolve().parents[1] / "astrodeck"
_FLAG = "ASTRODECK_FAST_TEST"
#: The two device/solver SIMULATORS this flag exists for (see module
#: docstring). Every other module under server/astrodeck is production
#: code and must decide nothing by whether a test harness is running.
_EXEMPT = {
    (_ASTRODECK_DIR / "devices" / "sim.py").resolve(),
    (_ASTRODECK_DIR / "solve" / "simsolver.py").resolve(),
}


def _string_constants(path: Path) -> set[str]:
    """Every string literal (code, not a ``#`` comment) in ``path``'s own
    source: docstrings count, since a docstring is itself a string
    constant, but a comment never reaches the parsed tree at all."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_no_module_except_the_two_simulators_reads_the_fast_test_flag():
    """RED under mutant "the read creeps back into engine.py"
    (``_await_target_window`` changed back to
    ``if os.environ.get("ASTRODECK_FAST_TEST") == "1":``, run from a byte
    backup, restored and sha256-verified), observed:

        AssertionError: ['astrodeck/sequence/engine.py']
        assert ['astrodeck/sequence/engine.py'] == []

    The CONTROL is the two exempt files themselves: both still carry the
    flag's name in their own source (sim.py's env read, simsolver.py's
    comment naming the same mechanism), and neither trips this -- proving
    the exemption list, not an accidentally-empty directory walk, is what
    keeps the assertion green today.
    """
    assert _FLAG in _string_constants(
        _ASTRODECK_DIR / "devices" / "sim.py"), (
        "the control itself lost the flag -- this test would pass for the "
        "wrong reason")
    offenders = []
    for path in sorted(_ASTRODECK_DIR.rglob("*.py")):
        if path.resolve() in _EXEMPT:
            continue
        if _FLAG in _string_constants(path):
            offenders.append(
                str(path.relative_to(_ASTRODECK_DIR.parent)).replace("\\", "/"))
    assert offenders == []
