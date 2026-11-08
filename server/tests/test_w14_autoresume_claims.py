# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every sentence of the Automatic resume tooltip names the test that keeps it
(#195, owner ruling 7 on #189; WP-85).

The owner's hover text for DUSK WINDOW's Automatic resume promised five things,
and the issue's own table found three of them untrue at the time (#191, #192,
#193). "A test holds a table of the tooltip's claims, each mapped to a named
test of the behaviour, so a claim cannot ship without one." This is that table.
The pattern is ``test_set_aside_promises.py``'s, smaller: the copy is read from
where it ships (``AUTO_RESUME_HELP`` in ``ui/src/components/flows/nodeDefs.ts``),
cut into sentences, and each sentence must have a row.

WHAT FAILS, AND WHAT EACH FAILURE MEANS

* a sentence with no row: someone reworded or added copy. The new sentence is a
  promise nothing keeps until a row names the test that does;
* a row whose sentence is no longer shipped: the copy changed under the row;
* a row that cites a test that does not exist (file, class or function): the
  row is a claim that a test keeps the promise, and no test does;
* the last sentence says what the code does for the roof and the dust cover
  (#601, WP 4.4: with a motorized roof or cover, both are opened before the
  night's first slew, at a run start and in ResumeArm's recovery), and its row
  cites the tests that open them. Until #601 it said the opposite, "does not
  yet open", and its row was a check that nothing opened them; the day the
  opening landed that check went red and the sentence was replaced. The
  owner's own sentence (dome control and the flat panel responding to the
  day/night cycle and the weather) stays out of the copy: it is still untrue.

THE INTERIM TEXT versus THE OWNER'S. The owner's text, quoted in the spec
(Revision 2, ruling 7) and in #195, is the TARGET wording. The shipped text
differs from it in exactly the places the issue's table says: "resumes at
sunset" is "resumes when its window opens" (a Clock time start opens it at that
time, never at sunset), "holds during cloudy weather" is worded as the three
sources that hold (#193 closed), and the dome and flat panel sentence is the
one that is true (#192 open). `test_the_shipped_text_does_not_promise_what_192_says_it_cannot`
holds the owner's untrue sentence out of the copy.

NAMED MUTANTS (each run from a byte backup inside this worktree, restored and
sha256-compared, the mutant text grepped out afterwards; the failing assertion
is quoted in the docstring of the test that catches it):

* "a sentence with no row": nodeDefs.ts's help gains a sentence;
* "a row cites a test that does not exist": a row's test renamed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SERVER = TESTS.parent
NODE_DEFS_TS = SERVER.parent / "ui" / "src" / "components" / "flows" / "nodeDefs.ts"

#: The owner's label, exactly (ruling 7 on #189).
LABEL = "Automatic resume on subsequent nights until capture quota is fulfilled"

#: sentence -> the tests of the behaviour it describes, as ``file::[Class::]name``
#: under ``server/tests``.
CLAIMS: dict[str, list[str]] = {
    # RESUMES ON LATER NIGHTS, when the window opens, until nothing is owed.
    "When on, AstroDeck resumes this flow automatically on subsequent nights, "
    "when its window opens, until every frame the flow asks for is taken.": [
        # a plan that never chose Off resumes two real days later
        "test_w4_resume_arm_single_night.py::"
        "test_default_plan_still_resumes_across_nights",
        # ResumeArm starts the armed dormant session when its window opens
        "test_resume_arm.py::test_tick_resumes_when_window_open",
        # a flow that says nothing (and a saved Single night) compiles On
        "test_w14_autoresume_option.py::"
        "test_a_flow_with_no_auto_resume_key_compiles_with_no_resume_key",
        "test_w14_autoresume_option.py::"
        "test_the_single_night_repeat_does_not_read_as_off",
        # a night cut short stays dormant and armed, and a finished one completes
        "test_a_short_night_stays_resumable.py::"
        "test_a_dawn_cut_mid_target_leaves_the_session_resumable",
        "test_a_short_night_stays_resumable.py::"
        "test_a_finished_run_still_completes",
    ],
    # PARKS AT DAWN, with a Dawn stop (#191).
    "With a Dawn stop it parks at dawn.": [
        "test_w1_dusk_window_compile.py::test_dawn_stop_is_unchanged",
        "test_flows_night_ends_parked.py::"
        "TestThePlanEndsTheNightSafely::test_a_compiled_flow_parks_and_warms",
        # a run that ends at its dawn cutoff parks
        "test_wind_down_abort_during_natural_park.py::"
        "test_an_abort_in_the_natural_park_lets_the_park_finish",
    ],
    # HOLDS FOR CLOUD (#193): each of the three sources.
    "While it runs it holds for cloud when something reports cloud: a monitor "
    "that reads cloud, a CLOUD WATCH rule, or, when no monitor reads cloud, "
    "its own frames.": [
        "test_engine_safety.py::test_unsafe_pauses_then_resumes_when_safe_again",
        "test_instructions_sky.py::"
        "TestCloudsAreEdgeTriggered::test_it_fires_once_on_the_way_in_not_every_frame",
        "test_sky_stands_in_for_a_missing_monitor.py::"
        "test_a_closed_sky_holds_instead_of_shooting_through_it",
        "test_w5_monitor_without_clouds.py::"
        "test_a_monitor_that_cannot_see_clouds_holds_on_a_closed_sky",
    ],
    # WHAT OFF DOES (the new behaviour of this change).
    "When off, a crash or restart the same night still resumes, but a "
    "subsequent night does not: CONTINUE it by hand.": [
        "test_w4_resume_arm_single_night.py::"
        "test_single_night_plan_still_resumes_the_same_night",
        "test_w4_resume_arm_single_night.py::"
        "test_single_night_plan_refuses_a_later_night",
        "test_w14_autoresume_option.py::"
        "test_an_off_flow_that_ends_at_dawn_is_left_dormant_and_disarmed",
        "test_w14_autoresume_option.py::"
        "test_a_crash_mid_night_still_resumes_the_same_night",
        "test_w14_autoresume_option.py::"
        "test_an_off_flow_keeps_its_arming_for_every_other_ending",
    ],
    # THE ROOF AND THE COVER OPEN BEFORE THE FIRST SLEW (#601, WP 4.4).
    "On a rig with a motorized roof or dust cover, it opens them before the "
    "first slew of the night, on a resume as on a first start.": [
        # a run start opens the cover, and the roof, before the mount slews
        "test_w4_4_observatory_envelope.py::"
        "test_a_sim_night_opens_the_cover_before_the_first_slew",
        "test_w4_4_observatory_envelope.py::"
        "test_a_closed_roof_is_opened_before_the_first_slew",
        # a rig with neither is never asked
        "test_w4_4_observatory_envelope.py::"
        "test_a_rig_without_actuators_runs_as_it_always_did",
        # a resume's recovery opens both before it solves or slews
        "test_w4_4_observatory_envelope.py::"
        "test_a_resumed_night_opens_the_roof_and_cover_in_recovery",
        # one that will not open is refused by name, and nothing moves
        "test_w4_4_observatory_envelope.py::"
        "test_a_cover_that_will_not_open_stops_the_run_before_it_slews",
    ],
}


def shipped_help() -> str:
    """``AUTO_RESUME_HELP`` as nodeDefs.ts ships it: the string literals of the
    constant's initialiser, joined. Read from the source, not through node, so
    this file needs nothing but the repository."""
    src = NODE_DEFS_TS.read_text(encoding="utf-8")
    m = re.search(r"export const AUTO_RESUME_HELP =(.*?);\s*\n", src, re.S)
    assert m, "AUTO_RESUME_HELP is not in nodeDefs.ts: the tooltip's text moved"
    body = m.group(1)
    pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    assert pieces, "AUTO_RESUME_HELP holds no string literal"
    assert re.fullmatch(r'(\s*"(?:[^"\\]|\\.)*"\s*\+?)+', body.strip() + ""), (
        "AUTO_RESUME_HELP is not a plain concatenation of string literals, "
        "which is all this table reads")
    return "".join(pieces)


def sentences(text: str) -> list[str]:
    """Cut on a full stop and a space. The copy is written for that (nodeDefs.ts
    says so): a colon is inside a sentence, not between two."""
    return [s for s in re.split(r"(?<=\.)\s+", text.strip()) if s]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def exists(test_id: str) -> str | None:
    """None when ``file::[Class::]name`` names a test that exists, else why not."""
    parts = test_id.split("::")
    path = TESTS / parts[0]
    if not path.is_file():
        return f"{parts[0]} does not exist"
    scope = _tree(path).body
    for want in parts[1:]:
        found = next((n for n in scope
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef)) and n.name == want), None)
        if found is None:
            return f"{want} is not defined in {'::'.join(parts[:parts.index(want)]) or parts[0]}"
        scope = found.body if isinstance(found, ast.ClassDef) else []
    last = parts[-1]
    if not last.startswith("test"):
        return f"{last} is not a test"
    return None


# ------------------------------------------------------------------ the table

def test_the_shipped_help_is_the_five_sentences_the_table_knows():
    """THE TABLE IS COMPLETE. Every sentence of the shipped copy has a row, and
    every row's sentence is shipped.

    MUTANT "a sentence with no row" (a sixth sentence added to
    ``AUTO_RESUME_HELP``) turned this red, run from a byte backup and restored
    and sha256-verified afterwards:

        AssertionError: the tooltip says something no test is named for, so
        nothing keeps the promise: ['It also warms the camera.']
        assert not ['It also warms the camera.']
    """
    shipped = sentences(shipped_help())
    assert len(shipped) == len(set(shipped)), "a sentence is shipped twice"
    unkept = [s for s in shipped if s not in CLAIMS]
    orphaned = [s for s in CLAIMS if s not in shipped]
    assert not unkept, (
        "the tooltip says something no test is named for, so nothing keeps "
        f"the promise: {unkept}")
    assert not orphaned, (
        f"a row stands for a sentence the tooltip no longer says: {orphaned}")


def test_every_row_names_tests_that_exist():
    """A row is the claim that a test keeps the promise.

    MUTANT "a row cites a test that does not exist"
    (``test_a_finished_run_still_completes`` renamed in the table) turned this
    red, run from a byte backup and restored and sha256-verified afterwards:

        AssertionError: a row cites a test that cannot keep its promise:
        {'When on, AstroDeck resumes this flow automatically on subseq...':
        {'test_a_short_night_stays_resumable.py::
        test_a_finished_run_still_completes_x':
        'test_a_finished_run_still_completes_x is not defined in
        test_a_short_night_stays_resumable.py'}}
    """
    broken = {}
    for sentence, ids in CLAIMS.items():
        assert ids, f"a row cites no test: {sentence!r}"
        bad = {i: why for i in ids if (why := exists(i))}
        if bad:
            broken[sentence[:60] + "..."] = bad
    assert not broken, f"a row cites a test that cannot keep its promise: {broken}"


# ----------------------------------------------------------------- the label

def test_the_label_is_the_owners_exactly():
    src = NODE_DEFS_TS.read_text(encoding="utf-8")
    m = re.search(r'export const AUTO_RESUME_LABEL =\s*"([^"]*)";', src)
    assert m, "AUTO_RESUME_LABEL is not in nodeDefs.ts"
    assert m.group(1) == LABEL


# ------------------------------------------------------ what the copy may not say

def test_the_shipped_text_does_not_promise_what_192_says_it_cannot():
    """The owner's own sentences that the code does not keep, held out of the
    copy until #192 closes. Each is checked as the words it is made of, so a
    light rewording of the same promise is caught too."""
    text = shipped_help().lower()
    for phrase, why in (
        ("resumes at sunset", "the window opens at the rig's twilight angle "
                              "plus the offset, or at a clock time (#191)"),
        ("flat panel, dome control", "the roof and the cover are opened for "
                                     "the night and the dome is bound, but "
                                     "neither follows the day/night cycle or "
                                     "the weather (#192)"),
        ("respond to the day/night cycle", "nothing does (#192)"),
        ("all work", "nothing does (#192)"),
    ):
        assert phrase not in text, f"the tooltip says {phrase!r}: {why}"


# ------------------------------------------ where a roof or a cover is opened

#: The calls that open a roof or a dust cover, by name.
_OPENERS = {"open_cover", "open_shutter", "open_roof", "open_dome"}


def _opener_calls(path: Path) -> list[tuple[str, str]]:
    """``(opener, enclosing function)`` for every call of an opener in ``path``."""
    found: list[tuple[str, str]] = []

    class Visit(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if name in _OPENERS:
                found.append((name, self.stack[-1] if self.stack else "<module>"))
            self.generic_visit(node)

    Visit().visit(_tree(path))
    return found


def test_a_roof_or_cover_is_opened_only_where_the_copy_accounts_for_it():
    """The last sentence of the copy says the roof and cover open before the
    night's first slew. They are opened in ONE module, ``sequence/roof.py``
    (``open_for_the_night``), which ``SequenceEngine._open_the_observatory``
    calls at a run start and ``ResumeArm._recover`` calls in its recovery
    (#601, WP 4.4). Anywhere else that opens one is something the sentence does
    not account for, so it fails here until the copy and its row do.

    The engine's two direct openers are not for the night, and are listed by
    their function: the reopen after an unsafe close (``_await_safe_and_reopen``,
    opt-in), and the DUSK FLATS stage (``_dusk_flats``, backlog WP-134), which
    re-opens the dust cover AFTER its flats, only a cover that was open when the
    stage began (the flats leave it shut and the lights that follow must not be
    taken through it): restoring what it found, held by
    ``test_w17_dusk_flats_panel.py::test_a_cover_that_was_shut_is_left_shut``.
    ``ResumeArm`` and ``DuskArm`` open nothing themselves."""
    root = SERVER / "astrodeck"
    engine = _opener_calls(root / "sequence" / "engine.py")
    assert engine == [("open_shutter", "_await_safe_and_reopen"),
                      ("open_cover", "_dusk_flats")], (
        f"the engine opens a roof or cover somewhere new: {engine}; the "
        f"tooltip does not account for it")
    assert _opener_calls(root / "sequence" / "roof.py") == [
        ("open_cover", "_open_cover")]
    for rel in ("sequence/resume_arm.py", "dusk_arm.py"):
        calls = _opener_calls(root / rel)
        assert calls == [], (
            f"{rel} opens a roof or cover ({calls}) itself: they open through "
            f"roof.open_for_the_night, and the tooltip accounts for that one")


# --------------------------------------------------------------- the parser

def test_the_sentence_cutter_cuts_where_the_copy_is_written_to_be_cut():
    assert sentences("One, two: THREE it. Four five. Six.") == [
        "One, two: THREE it.", "Four five.", "Six."]
    # the real copy: five sentences, in the order the claims table lists them
    assert [s for s in sentences(shipped_help())] == list(CLAIMS), (
        "the table lists the sentences in a different order from the copy")


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_copy_has_no_sentences(text):
    assert sentences(text) == []
