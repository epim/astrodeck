# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
r"""Every set-aside promise the copy makes names the tests that keep it
(#208; #189 S2; spec 3.4, 5.3 part 2, 6.7).

A set-aside used to live in engine memory, and five surfaces promised more
than that. The reject guard's tooltip (SequenceView.tsx) and its hint in the
new UI (automationModel.ts) said the shortfall stayed owed "for another
night". The Tonight brief (tonight.py) said a floored target was "not retried
tonight". The POOL node (nodes.py, nodeDefs.ts) said it was retried "next
night". A restart or an auto-resume the same night took every one of them
straight back (#208). S2 made the promise true: each set-aside is a
``Session.set_aside`` record under tonight's night key, which a restart
tonight reads back and the next night's run does not
(test_group_set_aside_persisted.py). The copy now says what S2 does, in the
engine's own words wherever it is prose: set aside for tonight; a restart
tonight does not retry it; the next night does. The POOL's option VALUE,
"Advance now; retry it next night", is stored in saved flows and is never
reworded (compile.py matches only its verb); since S2 it is true.

WP-25 (b), #290: a sixth surface said the same thing in a seventh word the
scan never watched for. The rig Standards panel's `max_consecutive_rejects`
hint said the step was "abandoned", which #208's PROMISE_WORDS does not
contain, so this file carried it unflagged while SequenceView.tsx and
automationModel.ts were reworded around it. standards.ts is added to FILES
and PROMISES below, and "abandon" is deliberately NOT added to PROMISE_WORDS:
the words already there ("does not retry", "next night") catch the reworded
sentence, which is the same proof #208's other four rows use.

This file is the claims table that holds the copy to that. It scans those
six files for the words such a promise is made of, and every hit must fall
inside a row:

- ``PROMISES``: a set-aside promise, citing the T10 test that proves a
  restart the same night does not retry it, the one that proves the next
  night does, and any test that carries the surface's own setting to the
  engine path those two drive.
- ``NOT_A_SET_ASIDE``: the same words saying something else, and why.

It fails when a promise has no row, when a row's words are no longer said as
often as the row says (the copy changed under it), or when a cited test does
not exist or never starts the session again on the night it is cited for.
The scan reads comments as well as strings, because the POOL comment in
nodes.py made the promise as plainly as any string did. It also joins a
sentence across the seams of a concatenated string literal, which is where
the Tonight brief's promise is split.

Every mutant below was applied in a private scratch copy of server/ and of
the three ui/ files (#254), never in the shared tree, and each file was
restored from a byte backup and its sha256 checked. The observed failure is
recorded verbatim: the first ``E   AssertionError`` line of each case that
went red, long lines wrapped.

MUTANT "drop the Tonight brief's row" (the tonight.py entry deleted from
``PROMISES``). RED, two cases, observed:
    test_every_promise_in_the_copy_has_a_row:
    AssertionError: a promise in the copy has no row in the claims table, so
    nothing says which test keeps it: {'server/astrodeck/flows/tonight.py':
    ["'does not retry' in ...l.params.get('minalt')}° floor, it is set aside
    for tonight - a restart tonight does not retry it, the next night does -
    and the next ...", '\'next night\' in ...° floor, it is set aside for
    tonight - a restart tonight does not retry it, the next night does - and
    the next best takes over.") ...']}
    test_the_tonight_brief_says_its_row_when_the_dial_says_advance:
    AssertionError: premise: the Tonight brief has exactly one row: []

MUTANT "cite a test id that does not exist" (``STEP_NEXT_NIGHT`` renamed to
``...::test_step_set_aside_retried_the_next_night``). RED, observed:
    AssertionError: a row cites a test that cannot prove it:
    {'ui/src/views/SequenceView.tsx: that step is set aside for tonight and
    the run moves on to the next step or t...': ['next night:
    tests/test_group_set_aside_persisted.py::test_step_set_aside_retried_the_next_night
    does not exist'],
    'ui/src/next/hubs/session/plan/automation/automationModel.ts: that step
    is set aside for tonight and the run moves on. Its shortfall stays ...':
    ['next night:
    tests/test_group_set_aside_persisted.py::test_step_set_aside_retried_the_next_night
    does not exist']}

MUTANT "cite a test that never restarts tonight" (``STEP_SAME_NIGHT`` set to
the next-night test, which exists). RED, observed:
    AssertionError: a row cites a test that cannot prove it:
    {'ui/src/views/SequenceView.tsx: that step is set aside for tonight and
    the run moves on to the next step or t...': ['same night:
    tests/test_group_set_aside_persisted.py::test_step_set_aside_retried_next_night
    never starts the session again at LATER_TONIGHT'],
    'ui/src/next/hubs/session/plan/automation/automationModel.ts: that step
    is set aside for tonight and the run moves on. Its shortfall stays ...':
    ['same night:
    tests/test_group_set_aside_persisted.py::test_step_set_aside_retried_next_night
    never starts the session again at LATER_TONIGHT']}

MUTANT "restore the S1 tooltip" (SequenceView.tsx's sentence put back to
"...skip to the next step/target. The shortfall stays in the session ledger
for another night."). RED, two cases, observed:
    test_every_promise_in_the_copy_has_a_row:
    AssertionError: a promise in the copy has no row in the claims table, so
    nothing says which test keeps it: {'ui/src/views/SequenceView.tsx':
    ['\'another night\' in ...ep, skip to the next step/target. the shortfall
    stays in the session ledger for another night." /> </span> <span
    classname="inline-fl...']}
    test_every_row_is_said_as_often_as_it_says:
    AssertionError: a row no longer matches the copy it stands for:
    {'ui/src/views/SequenceView.tsx: that step is set aside for tonight and
    the run moves on to the next step or t...': 'said 0 time(s), the row says
    1'}

MUTANT "a new promise with no row" (automationModel.ts's count_mode hint
made to end "...stay on disk and are regraded tomorrow."). RED, observed:
    AssertionError: a promise in the copy has no row in the claims table, so
    nothing says which test keeps it:
    {'ui/src/next/hubs/session/plan/automation/automationModel.ts':
    ['\'tomorrow\' in ...hooting, across nights if needed. rejected frames
    stay on disk and are regraded tomorrow.", max_eccentricity: "rejects a
    frame w...']}

MUTANT "reword the stored option VALUE" ("Advance now; retry it next night"
changed to "Advance now; set it aside for tonight" in nodes.py and
nodeDefs.ts). RED, two cases, observed:
    test_every_row_is_said_as_often_as_it_says:
    AssertionError: a row no longer matches the copy it stands for:
    {'server/astrodeck/flows/nodes.py: Advance now; retry it next night':
    'said 0 time(s), the row says 1', 'ui/src/components/flows/nodeDefs.ts:
    Advance now; retry it next night': 'said 0 time(s), the row says 2'}
    test_the_tonight_brief_says_its_row_when_the_dial_says_advance:
    AssertionError: premise: the campaign's POOL holds the stored Advance
    value: ['Advance now; set it aside for tonight']

MUTANT "no seam join" (``_SEAM`` compiled to a pattern that never matches,
so a concatenated string is read as the separate halves the source writes;
the Tonight brief's "next night" is then split and not seen at all). RED,
two cases, observed:
    test_every_promise_in_the_copy_has_a_row:
    AssertionError: a promise in the copy has no row in the claims table, so
    nothing says which test keeps it:
    {'ui/src/next/hubs/session/plan/automation/automationModel.ts':
    ['\'does not retry\' in ..." + "on. its shortfall stays owed in the
    session ledger: a restart tonight " + "does not retry it, the next night
    does.", max_consecut...', '\'next night\' in ...stays owed in the session
    ledger: a restart tonight " + "does not retry it, the next night does.",
    max_consecutive_rejects_night: ...'], 'server/astrodeck/flows/tonight.py':
    ['\'does not retry\' in ...rams.get(\'minalt\')}° floor, it is set aside
    for " f"tonight - a restart tonight does not retry it, the next "
    f"night does - and the n...']}
    test_every_row_is_said_as_often_as_it_says:
    AssertionError: a row no longer matches the copy it stands for:
    {'ui/src/next/hubs/session/plan/automation/automationModel.ts: that step
    is set aside for tonight and the run moves on. Its shortfall stays ...':
    'said 0 time(s), the row says 1', 'server/astrodeck/flows/tonight.py: it
    is set aside for tonight - a restart tonight does not retry it, the next
    n...': 'said 0 time(s), the row says 1'}

MUTANT "invert the dial's gate" (tonight.py's ``.startswith("advance")``
made ``.startswith("advance") is False``, so the sentence follows "Keep
imaging"). RED, observed:
    AssertionError: the Tonight brief does not say its row's sentence for a
    POOL whose dial says Advance

CONTROLS: the tree as it stands passes every case, in the shared tree and in
the scratch copy before and after the mutants; the brief for the same graph
with the dial on "Keep imaging (not recommended)" says nothing about a
set-aside (the last case); and the three ``NOT_A_SET_ASIDE`` rows cover words
that make no set-aside promise, each with its reason.
"""
from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent

SEQUENCE_VIEW = "ui/src/views/SequenceView.tsx"
AUTOMATION = "ui/src/next/hubs/session/plan/automation/automationModel.ts"
TONIGHT = "server/astrodeck/flows/tonight.py"
NODES = "server/astrodeck/flows/nodes.py"
NODE_DEFS = "ui/src/components/flows/nodeDefs.ts"
#: The rig Standards panel's `max_consecutive_rejects` hint (#290), added to
#: the scan by WP-25 (b): it makes the same per-step set-aside promise as
#: SEQUENCE_VIEW and AUTOMATION, and was not one of #208's five files because
#: S2 had not yet reworded the other three when this one was last touched.
STANDARDS = "ui/src/lib/standards.ts"
#: The files whose set-aside copy #208 listed, plus STANDARDS (#290).
FILES = (SEQUENCE_VIEW, AUTOMATION, TONIGHT, NODES, NODE_DEFS, STANDARDS)

#: The words a set-aside promise is made of. The first four are #208's
#: list; "does not retry" is the same-night half in the words the copy now
#: uses, and the last two are the other ways to say "next".
PROMISE_WORDS = ("another night", "next night", "not retried tonight",
                 "tomorrow", "does not retry", "following night",
                 "later night")

#: A string literal continued on the next line: Python's implicit
#: concatenation (an f, r, b or u prefix allowed) and TypeScript's ``+`` at
#: the end of the first line or the start of the second. Deleting the match
#: joins the text of the two halves, so a sentence split at a seam is read
#: as the one sentence an operator sees.
_SEAM = re.compile(
    r"""(["'`])[ \t]*\+?[ \t]*\r?\n\s*\+?[ \t]*[fFrRbBuU]{0,2}(["'`])""")
#: A comment continued on the next line: ``#``, ``#:``, ``//`` or a doc
#: comment's ``*``.
_LEADER = re.compile(r"\r?\n[ \t]*(?:#:?|//+|\*(?!/))")


def _prose(text: str) -> str:
    """``text`` as one line of lower-case prose: string seams joined,
    comment leaders dropped, every run of whitespace one space."""
    text = _SEAM.sub("", text)
    text = _LEADER.sub(" ", text)
    return re.sub(r"\s+", " ", text).lower()


@lru_cache(maxsize=None)
def prose(rel: str) -> str:
    return _prose((REPO / rel).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Promise:
    """A set-aside promise, in the words ``file`` says it, ``count`` times.
    ``same_night`` is the test that restarts the session later tonight and
    finds it not retried, ``next_night`` the one that starts it the next
    night and finds it tried; ``also`` carries this surface's setting to
    the path those two drive."""
    file: str
    says: str
    count: int
    same_night: str
    next_night: str
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotASetAside:
    """The promise words saying something that is not a set-aside."""
    file: str
    says: str
    count: int
    why: str


# ------------------------------------------------------------ the T10 proofs

_PERSISTED = "tests/test_group_set_aside_persisted.py::"
#: The per-step reject guard, on the clocked simulator.
STEP_SAME_NIGHT = _PERSISTED + "test_step_set_aside_not_retried_same_night"
STEP_NEXT_NIGHT = _PERSISTED + "test_step_set_aside_retried_next_night"
#: A floor advance: one test restarts the session later tonight and again
#: the next night.
FLOOR_BOTH_NIGHTS = _PERSISTED + "test_floor_set_aside_persisted_for_tonight"
#: The engine's own lines, whose phrases the prose copy borrows.
STEP_LINES = ("tests/test_set_aside_copy.py::"
              "test_the_step_lines_say_tonight_and_the_nights_bear_them_out")
FLOOR_LINE = ("tests/test_set_aside_copy.py::"
              "test_the_altitude_floor_line_says_what_the_code_does")
#: The POOL's dial reaching ``Schedule.on_floor == "advance"``, which is
#: the setting the floor test drives.
POOL_DIAL = ("tests/test_altitude_floor.py::TestTheCampaignFlowNowCarriesIt::"
             "test_every_pool_member_carries_the_dial")

#: Which constant each role must name in the cited test's own body: a test
#: cited for the same night has to start the session again later tonight,
#: one cited for the next night has to start it the next night.
RESTARTS_AT = {"same night": "LATER_TONIGHT", "next night": "NEXT_NIGHT"}


# --------------------------------------------------------------- the table

PROMISES: tuple[Promise, ...] = (
    # The per-step reject guard's tooltip, classic UI.
    Promise(SEQUENCE_VIEW,
            'that step is set aside for tonight and the run moves on to the next step or target. Its remaining frames stay pending in the session log: a restart tonight does not retry it, the next night does.',
            1, STEP_SAME_NIGHT, STEP_NEXT_NIGHT, (STEP_LINES,)),
    # The same guard's hint in the new UI's Plan automation section.
    Promise(AUTOMATION,
            "that step is set aside for tonight and the run moves on. Its "
            "remaining frames stay pending in the session log: a restart tonight "
            "does not retry it, the next night does.",
            1, STEP_SAME_NIGHT, STEP_NEXT_NIGHT, (STEP_LINES,)),
    # The same guard's hint on the rig Standards panel (#290): it used to say
    # the step was "abandoned", a word PROMISE_WORDS does not scan for, which
    # is how this surface went unnoticed when #208 reworded the other two.
    Promise(STANDARDS,
            "the step is set aside for tonight and the run moves on. Its "
            "remaining frames stay pending in the session log: a restart tonight does "
            "not retry it, the next night does.",
            1, STEP_SAME_NIGHT, STEP_NEXT_NIGHT, (STEP_LINES,)),
    # The Tonight brief, for a POOL whose dial says Advance.
    Promise(TONIGHT,
            "it is set aside for tonight - a restart tonight does not retry "
            "it, the next night does - and the next best takes over.",
            1, FLOOR_BOTH_NIGHTS, FLOOR_BOTH_NIGHTS, (FLOOR_LINE, POOL_DIAL)),
    # The POOL node's comment on its `floor` port.
    Promise(NODES,
            "a restart tonight does not retry it and the next night does",
            1, FLOOR_BOTH_NIGHTS, FLOOR_BOTH_NIGHTS, (POOL_DIAL,)),
    # The dial's stored value: the POOL default in nodes.py, and in
    # nodeDefs.ts the default and the select's option.
    Promise(NODES, "Advance now; retry it next night",
            1, FLOOR_BOTH_NIGHTS, FLOOR_BOTH_NIGHTS, (POOL_DIAL,)),
    Promise(NODE_DEFS, "Advance now; retry it next night",
            2, FLOOR_BOTH_NIGHTS, FLOOR_BOTH_NIGHTS, (POOL_DIAL,)),
    # The POOL node's rail description.
    Promise(NODE_DEFS,
            "sets that target aside for tonight (NOT done - a restart "
            "tonight does not retry it; the next night does)",
            1, FLOOR_BOTH_NIGHTS, FLOOR_BOTH_NIGHTS, (POOL_DIAL,)),
)

_RUN_COPY = ("why the running-plan note is a note and not a lock: an operator "
             "edits the NEXT run's settings while a run is live. It says "
             "nothing about a set-aside.")

NOT_A_SET_ASIDE: tuple[NotASetAside, ...] = (
    NotASetAside(SEQUENCE_VIEW,
                 "preparing tomorrow night's automation while tonight runs",
                 1, _RUN_COPY),
    NotASetAside(AUTOMATION,
                 "preparing tomorrow night's automation while tonight runs",
                 1, _RUN_COPY),
    NotASetAside(TONIGHT, "the session ledger seeds the next night", 1,
                 "the Dawn line's truth table: a session remaining at dawn "
                 "resumes from its ledger the following dusk. That is the "
                 "multi-night resume, whatever was or was not set aside."),
)


# ----------------------------------------------------------------- helpers

def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = text.find(needle)
    while i >= 0:
        out.append((i, i + len(needle)))
        i = text.find(needle, i + 1)
    return out


def _hits(text: str) -> list[tuple[int, int, str]]:
    return sorted((m.start(), m.end(), w) for w in PROMISE_WORDS
                  for m in re.finditer(re.escape(w), text))


def _label(row: Promise | NotASetAside) -> str:
    says = row.says if len(row.says) <= 80 else row.says[:77] + "..."
    return f"{row.file}: {says}"


def _find_test(test_id: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The test function ``test_id`` names (``path::name`` or
    ``path::Class::name``, the path relative to server/), or None."""
    path, *names = test_id.split("::")
    file = SERVER / path
    if not names or not file.is_file():
        return None
    node: ast.AST = ast.parse(file.read_text(encoding="utf-8"))
    for name in names:
        node = next((n for n in getattr(node, "body", ())
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))
                     and n.name == name), None)
        if node is None:
            return None
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
            or not node.name.startswith("test"):
        return None
    return node


def _names(fn: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name
               for n in ast.walk(fn))


# ------------------------------------------------------------------- cases

def test_every_promise_in_the_copy_has_a_row():
    """Every promise word in the five files falls inside a row's words:
    a set-aside promise with its proofs, or the same words saying something
    else, with why."""
    rows = PROMISES + NOT_A_SET_ASIDE
    unrowed: dict[str, list[str]] = {}
    for rel in FILES:
        text = prose(rel)
        hits = _hits(text)
        assert hits, (f"premise: {rel} carries no promise word at all, so the "
                      f"scan is not reading the copy #208 listed")
        covered = [span for r in rows if r.file == rel
                   for span in _spans(text, _prose(r.says))]
        for start, end, word in hits:
            if not any(a <= start and end <= b for a, b in covered):
                unrowed.setdefault(rel, []).append(
                    f"{word!r} in ...{text[max(0, start - 80):end + 40]}...")
    assert unrowed == {}, (
        f"a promise in the copy has no row in the claims table, so nothing "
        f"says which test keeps it: {unrowed}")


def test_every_row_is_said_as_often_as_it_says():
    """A row whose words the file no longer says, or says a different
    number of times, stands for copy that changed under it."""
    wrong: dict[str, str] = {}
    for r in PROMISES + NOT_A_SET_ASIDE:
        says = _prose(r.says)
        if not any(w in says for w in PROMISE_WORDS):
            wrong[_label(r)] = "carries no promise word, so it covers nothing"
            continue
        if isinstance(r, NotASetAside) and not r.why.strip():
            wrong[_label(r)] = "is set apart from the promises with no reason"
            continue
        n = len(_spans(prose(r.file), says))
        if n != r.count:
            wrong[_label(r)] = f"said {n} time(s), the row says {r.count}"
    assert wrong == {}, (
        f"a row no longer matches the copy it stands for: {wrong}")


def test_every_cited_test_exists_and_restarts_on_the_night_it_is_cited_for():
    """Each promise cites tests that exist. The one cited for the same night
    starts the session again at ``LATER_TONIGHT`` in its own body, and the
    one cited for the next night at ``NEXT_NIGHT``."""
    wrong: dict[str, list[str]] = {}
    for r in PROMISES:
        cited = [("same night", r.same_night), ("next night", r.next_night),
                 *(("also", t) for t in r.also)]
        for role, test_id in cited:
            fn = _find_test(test_id)
            must = RESTARTS_AT.get(role)
            if fn is None:
                wrong.setdefault(_label(r), []).append(
                    f"{role}: {test_id} does not exist")
            elif must is not None and not _names(fn, must):
                wrong.setdefault(_label(r), []).append(
                    f"{role}: {test_id} never starts the session again at "
                    f"{must}")
    assert wrong == {}, f"a row cites a test that cannot prove it: {wrong}"


def _campaign():
    from astrodeck.flows import examples as ex
    rec = next(f for f in ex.examples() if f.id == "example-campaign")
    return copy.deepcopy(rec.graph)


def test_the_tonight_brief_says_its_row_when_the_dial_says_advance():
    """The row's words are what the Tonight brief says for the shipped
    campaign, whose POOL dial holds the stored "Advance" value, so the scan
    is not grading a sentence nothing emits. With the dial on "Keep imaging"
    the brief says nothing about a set-aside (the control)."""
    from astrodeck.flows.tonight import brief
    rows = [r for r in PROMISES if r.file == TONIGHT]
    assert len(rows) == 1, (
        f"premise: the Tonight brief has exactly one row: {rows}")
    graph = _campaign()
    pools = [n for n in graph.nodes if n.type == "pool"]
    assert pools and all(n.params.get("onFloor")
                         == "Advance now; retry it next night"
                         for n in pools), (
        f"premise: the campaign's POOL holds the stored Advance value: "
        f"{[n.params.get('onFloor') for n in pools]}")
    assert _prose(rows[0].says) in _prose(brief(graph)), (
        "the Tonight brief does not say its row's sentence for a POOL whose "
        "dial says Advance")
    for n in pools:
        n.params["onFloor"] = "Keep imaging (not recommended)"
    kept = _prose(brief(graph))
    assert "set aside" not in kept and not [
        w for w in PROMISE_WORDS if w in kept], (
        f"with the dial on Keep imaging the brief still promises a "
        f"set-aside: {kept}")
