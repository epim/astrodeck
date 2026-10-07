# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A FAILED rotator self-test tells the operator which button re-tests it
(#749; wave 15 integration of WP-114).

WP-114 made the D-05 refusal and ``rotator_self_test``'s docstring name TEST
ROTATOR, the thing on the rotator panel that can run the self-test again (#697).
The warning the self-test writes to the night log when it FAILS still said
rotation stays off "until the next self-test passes": true, and a sentence
nobody can act on, in the one line the owner reads in the morning. It names the
button now, in the words the refusal uses.

Named mutant (run from a byte backup of ``hub.py``, restored byte-identically,
sha256 compared, the mutant text grepped absent): the warning's tail restored to
``"... until the next self-test passes"`` ->
``test_a_failed_self_test_says_to_press_test_rotator``,
``AssertionError: the FAIL line does not name the button that re-tests: 'rotator
self-test FAILED (D-05, #594): the camera followed only ...; rotation is off for
the night and panels will be shot at a fixed angle until the next self-test
passes'``.
"""
from __future__ import annotations

from _simhub import sim_hub  # noqa: F401  (fixture import)
from test_w15_rotator_retest import _slip, at_target  # noqa: F401  (fixtures)


async def test_a_failed_self_test_says_to_press_test_rotator(
        at_target, bus_lines):
    hub = at_target
    _slip(hub)

    await hub.ensure_rotator_ready()

    assert hub._rotation_trusted is False, "premise: the slipping coupling failed"
    said = [m for lv, m, src in bus_lines
            if src == "rotator" and "rotator self-test FAILED" in m]
    assert len(said) == 1, f"premise: one FAIL line, not {len(said)}: {said}"
    assert "TEST ROTATOR" in said[0], (
        f"the FAIL line does not name the button that re-tests: {said[0]!r}")
    assert "until the next self-test passes" not in said[0], said[0]
    # The line still carries what the D-05 readers match on.
    assert "D-05" in said[0] and "rotation is off for the night" in said[0], said[0]


async def test_a_passing_self_test_says_nothing_about_a_button(
        at_target, bus_lines):
    """CONTROL: the pass line is untouched, and it does not point at a button
    the operator has no reason to press."""
    hub = at_target

    await hub.ensure_rotator_ready()

    assert hub._rotation_trusted is True, "premise: a healthy coupling passed"
    said = [m for lv, m, src in bus_lines
            if src == "rotator" and "rotator self-test passed" in m]
    assert len(said) == 1, said
    assert "TEST ROTATOR" not in said[0], said[0]
