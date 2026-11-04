# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Ratchets: allowlists with counts that may only shrink (#1121).

A RATCHET is for a defect class that is too widespread to fix in one change
and too dangerous to leave open-ended. The scan counts the instances, the
allowlist below records the count each place had when the ratchet was set, and
the test fails in BOTH directions:

* a count that went UP fails, naming the place: a new instance of the class
  landed, and the author must remove it (or, for a place that truly needs one,
  add it to the table in the same change, where a reviewer sees it);
* a count that went DOWN fails too, asking for the table to be lowered: a
  ratchet that is not tightened when the work is done is how an allowlist
  outlives its entries and later hides a new instance behind an old one.

Three classes are ratcheted here, each by walking syntax trees rather than
matching text:

``SILENT_HANDLERS`` (#1019). ``except Exception: pass`` and bare
``except: pass`` under ``server/astrodeck`` whose body says nothing and whose
author said nothing either. A best-effort guard with a bare ``pass`` on a path
whose failure matters is the class behind #811, #936, #964 and #993; each wave
pinned only the file it fixed. A handler is NOT silent when a comment of three
or more words of prose sits on any line from the ``except`` to the ``pass``, or
on the line just above the ``except``: the reason beside the ``pass`` is the
convention #993 set, and a ``# noqa: BLE001`` alone is a lint code, not a
reason. Keyed ``(module, function, exception type)``.

``GETSOURCE_USES`` and ``AST_WALK_USES`` (#978). Tests that grade a subject by
reading its source (``inspect.getsource``) or by walking its tree
(``ast.walk``) grade where the code sits and not what it does: such a pin
stays green when behaviour breaks and goes red on a refactor that kept it.
Keyed by test file. Replace the pin with a behavioural case; keep a source
read only where the claim really is a doc-to-code mapping, and then it stays
in the table with its count.

The tables hold what main had when the ratchet was set; they shrink as later
work touches the files they name.
"""
from __future__ import annotations

import ast
import functools
import io
import re
import tokenize
from pathlib import Path
from typing import Mapping

TESTS = Path(__file__).resolve().parent
PACKAGE = TESTS.parent / "astrodeck"

#: The files that define the ratchet name the shapes it counts, so they are not
#: scanned.
_SELF = frozenset({"_ratchet.py", "test_w2_ratchets.py"})

_BROAD = frozenset({"Exception", "BaseException"})
#: ``noqa`` and the lint code after it: a tag, not prose.
_LINT_TAG = re.compile(r"noqa(?::\s*[A-Z]+[0-9]*(?:\s*,\s*[A-Z]+[0-9]*)*)?",
                       re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]{2,}")
#: How many words of prose make a comment a reason.
REASON_WORDS = 3

SILENT_HANDLERS: dict[tuple[str, str, str], int] = {
    ('api/slow_requests.py', '_tick', 'Exception'): 2,
    ('api/slow_requests.py', 'leave', 'Exception'): 1,
    ('api/slow_requests.py', 'report', 'Exception'): 1,
    ('comhost/manager.py', '_kill_pid', 'Exception'): 1,
    ('comhost/manager.py', 'stop', 'Exception'): 1,
    ('comhost/server.py', 'close', 'Exception'): 1,
    ('devices/alpaca.py', 'connect', 'Exception'): 5,
    ('devices/alpaca.py', 'rotate_axis', 'Exception'): 1,
    ('devices/backends/asiair_backend.py', 'probe_asiair', 'Exception'): 1,
    ('devices/backends/player_one.py', 'discover', 'Exception'): 1,
    ('devices/backends/wanderer_snowflake.py', 'close', 'Exception'): 1,
    ('devices/backends/zwo_asi.py', 'discover', 'Exception'): 1,
    ('devices/backends/zwo_usb.py', 'discover', 'Exception'): 2,
    ('devices/cameras/player_one_sdk.py', 'open', 'Exception'): 1,
    ('devices/cameras/zwo_asi_sdk.py', 'open', 'Exception'): 1,
    ('devices/nina.py', '_detail', 'Exception'): 2,
    ('devices/nina.py', '_poll_graph', 'Exception'): 1,
    ('devices/nina.py', 'flip_calibration', 'Exception'): 1,
    ('devices/nina.py', 'guide_frame', 'Exception'): 1,
    ('devices/orchestrator.py', 'connect_profile', 'Exception'): 2,
    ('guide/flight_recorder.py', 'add_pulse', 'Exception'): 1,
    ('guide/flight_recorder.py', 'walk_legs', 'Exception'): 1,
    ('guide/flight_recorder.py', 'walk_step', 'Exception'): 1,
    ('guide/native.py', '_record_cycle', 'Exception'): 1,
    ('guide/native.py', 'dump_flight_recorder', 'Exception'): 1,
    ('guide/phd2.py', '_reconnect', 'Exception'): 1,
    ('guide/phd2.py', 'disconnect', 'Exception'): 1,
    ('hub.py', '_note_solve_exposure', 'Exception'): 1,
    ('licensing.py', '_player_one_source', 'Exception'): 1,
    ('locations.py', '_location_from_disk', 'Exception'): 1,
    ('polar/native.py', '_capture_and_solve', 'Exception'): 1,
    ('polar/session.py', 'pause', 'Exception'): 1,
    ('polar/session.py', 'resume', 'Exception'): 1,
    ('polar/session.py', 'stop', 'Exception'): 1,
    ('providers.py', '_override_family', 'Exception'): 1,
    ('providers.py', 'override_with_layer', 'Exception'): 1,
    ('tracking_probe.py', 'run_probe', 'Exception'): 1,
}

GETSOURCE_USES: dict[str, int] = {
    'test_1071_model_step.py': 1,
    'test_absent_means_unchanged_not_unwritable.py': 1,
    'test_backfill_returns_before_the_relay_gives_up.py': 1,
    'test_busy_lanes_routes.py': 2,
    'test_cooler_gate.py': 1,
    'test_cooling_restore.py': 1,
    'test_defocus.py': 1,
    'test_dither_settle_hold.py': 2,
    'test_engine_guide_quiet.py': 1,
    'test_flat_metering_no_panel.py': 1,
    'test_flip_before_the_limit.py': 2,
    'test_flows_compile_payload_docstring.py': 3,
    'test_gallery_thumb_warm.py': 2,
    'test_goto_rotation.py': 1,
    'test_group_rules.py': 1,
    'test_guide_camera_settings_route.py': 1,
    'test_mosaic_spec_claims.py': 57,
    'test_native_guider_relocks.py': 1,
    'test_offer_matches_resolver.py': 1,
    'test_rotator_sky_sync.py': 1,
    'test_run_start_owns_the_camera.py': 1,
    'test_session_stack_backfill.py': 1,
    'test_site_gate.py': 1,
    'test_start_paths_refuse_camera_owned.py': 2,
    'test_sync_push.py': 2,
    'test_sync_runner.py': 2,
    'test_w15_dusk_flats_claim.py': 1,
}

AST_WALK_USES: dict[str, int] = {
    'test_850_engine_sync_refused.py': 2,
    'test_a_run_without_a_temperature_says_so.py': 2,
    'test_catalog_copy_rules.py': 4,
    'test_compile_route_never_500.py': 1,
    'test_cooling_restore.py': 1,
    'test_dome_block_is_about_the_roof.py': 1,
    'test_every_site_consumer_asks_whether_there_is_a_site.py': 6,
    'test_failed_solve_says_no_light.py': 1,
    'test_flat_metering_no_panel.py': 2,
    'test_flows_compile_payload_docstring.py': 1,
    'test_flows_cooling.py': 1,
    'test_flows_rig_facts_route.py': 1,
    'test_flows_vocabulary_s3.py': 1,
    'test_flows_wizard_mosaic.py': 3,
    'test_group_rules.py': 1,
    'test_h4_sessions_list_counts_nights.py': 2,
    'test_hips_local.py': 1,
    'test_mosaic_spec_claims.py': 133,
    'test_no_task_await_eats_its_callers_cancel.py': 3,
    'test_offer_matches_resolver.py': 2,
    'test_panel_order.py': 1,
    'test_resume_ladder_stops.py': 1,
    'test_s7_wizard_unguided_cap.py': 1,
    'test_set_aside_promises.py': 1,
    'test_start_paths_refuse_camera_owned.py': 2,
    'test_sync_push.py': 1,
    'test_sync_runner.py': 2,
    'test_w14_no_light_audit.py': 1,
    'test_w15_rig_beacon.py': 1,
    'test_w1_thread_platform_ratchet.py': 1,
    'test_w4_no_engine_fast_test_read.py': 1,
    'test_w5_no_round_count_waits.py': 1,
}


def judge(found: Mapping, allowed: Mapping) -> tuple[dict, dict]:
    """``(grown, shrunk)``: by how much each count is over, and under, its
    allowance. A place not in ``allowed`` has an allowance of zero."""
    grown = {k: n - allowed.get(k, 0) for k, n in found.items()
             if n > allowed.get(k, 0)}
    shrunk = {k: n - found.get(k, 0) for k, n in allowed.items()
              if found.get(k, 0) < n}
    return grown, shrunk


def ratchet_problems(what: str, found: Mapping, allowed: Mapping,
                     *, remedy: str) -> str:
    """Empty when ``found`` equals ``allowed``; otherwise a message naming each
    place that grew (with ``remedy``) and each that shrank."""
    grown, shrunk = judge(found, allowed)
    out: list[str] = []
    if grown:
        out.append(f"{what}: more than the ratchet allows. {remedy}\n  "
                   + "\n  ".join(f"{k}: +{n}" for k, n in sorted(grown.items())))
    if shrunk:
        out.append(f"{what}: fewer than the ratchet allows. Lower the table in "
                   f"tests/_ratchet.py to the new count:\n  "
                   + "\n  ".join(f"{k}: -{n}" for k, n in sorted(shrunk.items())))
    return "\n".join(out)


# ------------------------------------------------------------ silent handlers

def _comments(source: str) -> dict[int, str]:
    """``{line: text}`` of every comment, from the tokenizer, so a ``#`` inside
    a string is not one."""
    return {t.start[0]: t.string
            for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type == tokenize.COMMENT}


def _prose_words(comment: str) -> int:
    return len(_WORD.findall(_LINT_TAG.sub("", comment.lstrip("#"))))


def _type_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return ["bare"]
    elts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [ast.unparse(e) for e in elts]


def _is_silent(handler: ast.ExceptHandler, comments: Mapping[int, str],
               lines: list[str]) -> bool:
    if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Pass):
        return False
    if not set(_type_names(handler)) & (_BROAD | {"bare"}):
        return False
    first, last = handler.lineno, handler.body[0].end_lineno
    said = [comments[n] for n in range(first, last + 1) if n in comments]
    if first - 1 in comments and lines[first - 2].lstrip().startswith("#"):
        said.append(comments[first - 1])
    return not any(_prose_words(c) >= REASON_WORDS for c in said)


def silent_handlers(source: str) -> dict[tuple[str, str], int]:
    """``{(function, exception type text): count}`` for each silent handler in
    ``source``. The function is the innermost enclosing one, ``<module>`` at
    module level."""
    comments = _comments(source)
    lines = source.splitlines()
    out: dict[tuple[str, str], int] = {}

    def walk(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func)
            if isinstance(child, ast.ExceptHandler) and _is_silent(
                    child, comments, lines):
                kind = ast.unparse(child.type) if child.type else "bare"
                out[(func, kind)] = out.get((func, kind), 0) + 1
            walk(child, inner)

    walk(ast.parse(source), "<module>")
    return out


def scan_silent_handlers(root: Path = PACKAGE) -> dict[tuple[str, str, str], int]:
    """Every silent handler under ``root``, keyed ``(module, function, type)``
    with the module as a path relative to ``root``."""
    out: dict[tuple[str, str, str], int] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        found = silent_handlers(path.read_text(encoding="utf-8"))
        for (func, kind), n in found.items():
            out[(rel, func, kind)] = n
    return out


# ------------------------------------------------------- source-reading tests

_GETSOURCE = frozenset({"getsource", "getsourcelines"})


@functools.lru_cache(maxsize=None)
def _parse(source: str) -> ast.AST:
    """One parse per file for the two counters below: the biggest test file is
    a megabyte."""
    return ast.parse(source)


def _calls(tree: ast.AST, owner: str | None, names: frozenset[str]) -> int:
    """Calls of ``owner.<name>(...)`` (or ``<name>(...)`` when ``owner`` is
    None) for a name in ``names``."""
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if owner is None:
            if isinstance(fn, ast.Name) and fn.id in names:
                n += 1
            elif isinstance(fn, ast.Attribute) and fn.attr in names:
                n += 1
        elif (isinstance(fn, ast.Attribute) and fn.attr in names
              and isinstance(fn.value, ast.Name) and fn.value.id == owner):
            n += 1
    return n


def getsource_uses(source: str) -> int:
    """Calls of ``inspect.getsource`` / ``getsourcelines`` (any receiver)."""
    if "getsource" not in source:           # no name, no call: skip the parse
        return 0
    return _calls(_parse(source), None, _GETSOURCE)


def ast_walk_uses(source: str) -> int:
    """Calls of ``ast.walk``."""
    if "walk" not in source:                # no name, no call: skip the parse
        return 0
    return _calls(_parse(source), "ast", frozenset({"walk"}))


def scan_tests(counter, root: Path = TESTS) -> dict[str, int]:
    """``{test file (relative to root): count}`` of ``counter(source)`` over
    every ``*.py`` under ``root`` except this module and its test, files with
    none left out."""
    out: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        if path.name in _SELF:
            continue
        n = counter(path.read_text(encoding="utf-8"))
        if n:
            out[path.relative_to(root).as_posix()] = n
    return out
