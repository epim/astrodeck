# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Guard: no NEW `for _ in range(N)` loop around a sub-0.1 s asyncio.sleep
in server/tests (#610).

Such a loop gives Linux and Windows very different real wait time for the
SAME test (Linux sleeps close to what it asks, Windows rounds every sleep
up to its ~15.6 ms timer), so a loop sized on a Windows dev box can get
2-15x LESS real patience on a Linux CI runner and fail there whenever the
runner is loaded. #610 catalogued 27 of them; every one of the 19 files it
named now waits on ``tests/_deadline.py``'s ``wait_until`` instead (a
``time.monotonic()`` deadline in seconds, the same on both platforms).

This test is an AST scan, not a text grep, so it survives reformatting and
does not fire on the word "range" in a comment or a docstring. It walks
every ``for ... in range(...)`` loop in every test file, labelled by the
dotted path of the function defs it sits inside (a loop inside a nested
helper, like a fixture's inner coroutine, is labelled
``outer_func.inner_func``), and flags one whose body awaits
``asyncio.sleep(x)`` with a literal ``x`` strictly between 0 and 0.1
seconds. ``asyncio.sleep(0)`` is excluded on purpose: it is CPython's
yield-the-loop fast path (no timer is armed), so it costs the same on both
platforms and is not this bug's shape at all -- it is how several loops in
this suite spin a fixed number of turns deliberately (see
test_idle_stop_retry_clock.py, test_resume_ladder_stops.py and others)."""
from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: Deliberate round counts, not a poll against a budget: the loop IS the
#: test (produce exactly N ticks while something else runs concurrently),
#: not a wait for a condition that might arrive earlier or later. Each key
#: is "<file>::<dotted path of enclosing function defs>".
DELIBERATE_ROUND_COUNTS = {
    # The loop proves the event loop keeps ticking while a blocking call
    # (fake_mount.info, gated on a threading.Event) holds a real OS thread;
    # it asserts ticks == 5 exactly; converting it to "wait until 5 ticks"
    # would just be this same round count spelled differently.
    "test_asiair_backend.py::"
    "test_the_event_loop_keeps_running_during_a_slow_rpc.ticker",
    # The stand-in scheduler (wave 15, WP-116, #702): it hands the loop back
    # for 40 turns of 5 ms and, at turn 20 exactly, holds the loop's thread
    # for 1.5 bounds, which is what a starved thread looks like to the spin
    # watchdog. The count IS the fixture (the stall must land in the middle
    # of a night that otherwise yields), not a wait for a condition: nothing
    # is being waited on, and "until N turns" would be this same loop spelled
    # differently. Taking this entry out reproduces the red the merged tree
    # showed before it was added: `test_group_harness_watchdog.py::
    # test_a_starved_loop_is_not_a_spin_under_the_non_spin_bound.starved_once
    # at line 293`.
    "test_group_harness_watchdog.py::"
    "test_a_starved_loop_is_not_a_spin_under_the_non_spin_bound.starved_once",
}

#: Loops of #610's exact shape that this scan found OUTSIDE the 19 files
#: #610 named and WP-68's file mandate covers -- found incidentally while
#: writing this guard, not fixed here (WP-68 may not edit these files), and
#: reported as a new defect of the same class for its own issue. Removing a
#: file from this set without converting its loop(s) to ``wait_until``
#: makes the scan below fail on them, which is the point: it is a allowlist
#: for a known, still-open gap, not a blanket exemption.
#:
#: The three entries WP-68 itself could not touch were converted at W5
#: integration (#610 remainder): test_w2_am5_park_pulse_lock.py's two cases
#: and test_w3_relay_drop_rate.py's one now wait on ``wait_until`` like every
#: other file in this suite, so nothing remains here.
KNOWN_UNCONVERTED_GAPS: set[str] = set()


def _is_range_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "range")


def _is_sleep_call(func: ast.AST) -> bool:
    """``asyncio.sleep`` (or ``aio.sleep``/``sleep`` after some other
    import spelling) -- matched on the attribute/name alone, since a test
    file never defines its own unrelated ``.sleep(...)`` call."""
    if isinstance(func, ast.Attribute):
        return func.attr == "sleep"
    if isinstance(func, ast.Name):
        return func.id == "sleep"
    return False


def _has_short_sleep(for_node: ast.For) -> bool:
    """Whether ``for_node``'s body awaits a sleep whose literal argument is
    strictly between 0 and 0.1 seconds. ``sleep(0)`` does not count (see
    the module docstring); a non-literal argument (a variable) is not
    something this static scan can evaluate, so it is left alone too --
    every loop #610 found used a literal."""
    for node in ast.walk(for_node):
        if not (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not (_is_sleep_call(call.func) and call.args):
            continue
        arg = call.args[0]
        if (isinstance(arg, ast.Constant)
                and isinstance(arg.value, (int, float))
                and 0 < arg.value < 0.1):
            return True
    return False


def _range_for_loops(tree: ast.Module):
    """Yield (dotted qualname, for-node) for every ``for ... in range(...)``
    loop in the module, where the qualname is the dotted path of enclosing
    ``def``/``async def`` names (module scope is the empty string)."""
    def walk(node: ast.AST, stack: list[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from walk(child, stack + [child.name])
            elif isinstance(child, ast.For) and _is_range_call(child.iter):
                yield ".".join(stack), child
                yield from walk(child, stack)
            else:
                yield from walk(child, stack)
    yield from walk(tree, [])


def test_no_new_round_count_sleep_loops():
    """MUTANT "a round count comes back" (``test_app_route_concurrency.py``'s
    converted wait put back to ``for _ in range(50): ...; await
    asyncio.sleep(0.01)``, its pre-WP-68 shape): RED, observed verbatim:
        AssertionError: new `for _ in range(N)` loop(s) around a sub-0.1 s
        asyncio.sleep (#610): ... test_app_route_concurrency.py::
        test_spawn_replace_cancels_existing_task at line 107
        assert ['test_app_ro... at line 107'] == []
    Run from a byte backup of that file, restored and SHA-256-compared
    after (#254's convention)."""
    violations: list[str] = []
    known_gaps_seen: set[str] = set()
    for path in sorted(TESTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for qualname, for_node in _range_for_loops(tree):
            if not _has_short_sleep(for_node):
                continue
            key = f"{path.name}::{qualname}"
            if key in DELIBERATE_ROUND_COUNTS:
                continue
            if key in KNOWN_UNCONVERTED_GAPS:
                known_gaps_seen.add(key)
                continue
            violations.append(f"{key} at line {for_node.lineno}")
    assert violations == [], (
        "new `for _ in range(N)` loop(s) around a sub-0.1 s asyncio.sleep "
        "(#610): give Linux and Windows very different real wait time for "
        "the same test. Use tests/_deadline.py's wait_until on a wall-clock "
        "budget instead, or add the loop to DELIBERATE_ROUND_COUNTS in this "
        "file if it is genuinely a fixed round count rather than a wait for "
        "a condition: " + "; ".join(violations))
    # The gap list names loops this WP found but could not fix (outside its
    # file mandate); if one gets fixed elsewhere, its entry here should be
    # removed too, or this test stops proving anything about it.
    missing = KNOWN_UNCONVERTED_GAPS - known_gaps_seen
    assert missing == set(), (
        f"KNOWN_UNCONVERTED_GAPS names loop(s) this scan did not find any "
        f"more (fixed, renamed, or the file is gone) -- remove them from "
        f"the allowlist: {missing}")
