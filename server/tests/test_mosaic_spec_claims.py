"""The mosaic spec says what S1 and its hardening round built (#189 A10, A11).

``docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md`` is
what every later slice is planned from, and S1 moved under it. The card chip
it promised pointed at a "section 3.8" that was never written. The step id it
gave was the spelling S1's hardening round replaced. CONTINUE picked its
session by a rule the code no longer uses. And the rulings made while
hardening S1 lived only in code comments. A spec that disagrees with the code
is a claim nothing keeps, and the next agent to plan from it "fixes" the code
back.

So each test below holds one edited claim to the code it describes, and takes
the claim's numbers and spellings FROM that code wherever it can: the step
spelling from ``identity.step_signature``, the name-key prefix from
``identity``, the ADOPT bound from ``continuation``, the retry clock and the
hop seed from the engine, the route from its decorator in ``api/app.py``, and
the chip's keys from a real ``flow_progress`` answer. When either side moves,
the test goes red and says which side to fix.

WHAT THIS CANNOT DO. It reads words, so it proves the spec SAYS a thing, not
that the thing holds everywhere the spec implies. Where a claim describes
code, the code half is asserted too, as the shape of that code (a call made,
a predicate asked, a flag read); whether the code then behaves is its own
suite's question (the cloud hold's is test_cloud_hold_watch.py).

Every test names the mutation of the spec it guards and quotes the failure
that mutation produced, each run from a byte backup of the spec and the spec
checked byte-identical afterwards. Where a claim takes a value from code, a
code mutant is quoted as well: that module global, or that engine method,
rebound inside the test process only, never an edit of the source file. The
control shows that every section a test reads is really there, so an
assertion that a phrase is ABSENT cannot pass on an empty section; and an
edit to the spec that no claim covers (section 7's engine heading) left every
test green.
"""
from __future__ import annotations

import inspect
import json
import re
import uuid
from pathlib import Path

import astrodeck.config as config_mod
from astrodeck.flows import continuation, identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence import engine as engine_mod
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import SessionStore

# server/tests/<this> -> parents[1] is server/, parents[2] the git root.
_SERVER = Path(__file__).resolve().parents[1]
_ROOT = Path(__file__).resolve().parents[2]
SPEC = (_ROOT / "docs" / "superpowers" / "specs"
        / "2026-09-23-flows-mosaic-target-block-design.md")
APP = _SERVER / "astrodeck" / "api" / "app.py"
UI_SRC = _ROOT / "ui" / "src"


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _section(label: str) -> str:
    """The text under the ``### <label> ...`` heading, up to the next ``##``
    or ``###`` heading. ``label`` is the heading's first word, matched
    exactly, so "5.1" never finds "5.10" and "S1:" never finds "S1b"."""
    lines = _spec().splitlines()
    starts = [i for i, line in enumerate(lines)
              if line.startswith("### ")
              and line[4:].split(" ", 1)[0] == label]
    assert len(starts) == 1, (
        f"the spec has {len(starts)} '### {label}' headings; the claims below "
        f"are read from exactly one")
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## ") or line.startswith("### "):
            break
        body.append(line)
    return "\n".join(body)


def _line(text: str, prefix: str) -> str:
    """The one line of ``text`` that starts with ``prefix``. The spec writes a
    bullet or a numbered item as one line, so a claim is one line."""
    hits = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(hits) == 1, (
        f"expected exactly one line starting {prefix!r}, found {len(hits)}")
    return hits[0]


# ------------------------------------------------------------------ control

#: One sentence per section that this round did NOT edit, so finding it
#: proves the section reader returned the real section and not an empty
#: string, against which every "not in" below would pass.
_ANCHORS = {
    "1.2": "The type id stays `target`, category SOURCE, label TARGET.",
    "3.3": "**Identity.** `NS_FLOWS` is a fixed uuid constant in `to_plan`.",
    "3.6": "`_migrate` **refuses** `schema_version > FLOW_SCHEMA`.",
    "5.6": "Every hop is a slew through the one motion path.",
    "5.8": "Holds are not pauses",
    "5.9": "**The critical section.**",
    "5.10": "`_set_state`",
    "S1:": "`_hop_focus_is_owed` (5.6 step 6)",
}


def test_control_every_section_the_claims_read_is_the_real_section():
    """Not a mutation target: a reader that returned nothing would make the
    absence checks below pass on any spec. Each section is found once, holds
    a sentence this round left alone, and is more than a line long."""
    for label, anchor in _ANCHORS.items():
        text = _section(label)
        assert anchor in text, (label, anchor)
        assert len(text.splitlines()) > 3, label


# ------------------------------------------------------------ 1.2, the chip

_REF = re.compile(r"\bsection (\d+(?:\.\d+)?)\b", re.IGNORECASE)
_HEADING = re.compile(r"^#{2,3} (\d+(?:\.\d+)?)\.? ", re.MULTILINE)
#: Section 6 is a table, and "section 6.9" names its row "| 6.9 |".
_ROW = re.compile(r"^\| (\d+\.\d+) \|", re.MULTILINE)


def test_every_section_reference_names_a_heading_that_exists():
    """1.2 sent the reader to "section 3.8" for the progress route, and the
    spec has no 3.8: section 3 stops at 3.7. Every "section N" or "section
    N.M" in the spec must name a heading, or a numbered row of the section 6
    table, that is there.

    RED under mutant "restore section 3.8" (the 1.2 chip line put back as
    it was):

        AssertionError: the spec sends the reader to section(s) ['3.8'],
        which it does not have
        assert ['3.8'] == []
          Left contains one more item: '3.8'

    Unchanged under the control "unrelated edit in section 7" (all pass).
    """
    text = _spec()
    headings = set(_HEADING.findall(text)) | set(_ROW.findall(text))
    refs = set(_REF.findall(text))
    # The readers read something: the headings run to 3.7 and stop, the
    # table rows include 6.9, and the references include ones this round
    # never touched.
    assert {"0", "3.7", "5.10", "10", "6.9"} <= headings
    assert "3.8" not in headings
    assert {"1.5", "5.6", "9"} <= refs and len(refs) >= 10
    missing = sorted(refs - headings)
    assert missing == [], (
        f"the spec sends the reader to section(s) {missing}, which it does "
        f"not have")


def _real_progress() -> dict:
    """A real ``flow_progress`` answer for one TARGET and one CAPTURE, so the
    keys the spec names are checked against what the route sends."""
    graph = FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={"name": "M31", "ra": "00h 42m 44s",
                                "dec": "+41 16 09", "rotation": -1}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={"filter": "L", "exposure": 60, "gain": 100,
                                "bin": "1", "count": 5, "goal": 0})],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target", "to": "c",
                           "toPort": "run"})])
    compiled = compile_plan(graph, "claims")
    plan, _unmapped = to_sequence_plan(compiled, graph, flow_id="spec-claims")
    return flow_progress(compiled, plan, None, flow_id="spec-claims")


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_the_1_2_chip_reads_the_progress_route_s1_built():
    """The chip line names the route ``api/app.py`` declares, the module and
    function whose answer it is, and only keys that answer really carries.

    RED under mutant "restore section 3.8" as well, on the route:

        AssertionError: 1.2 must name the route app.py declares
        assert '`GET /api/flows/{flow_id}/progress`' in '- A one-line state
        chip from the progress route (section 3.8): `4/6 panels done`, or
        `212/315 subs` for a single target.'
    """
    found = re.search(r'@app\.get\(\s*"(/api/flows/\{flow_id\}/progress)"',
                      APP.read_text(encoding="utf-8"))
    assert found, "app.py no longer declares the progress route this reads"
    chip = _line(_section("1.2"), "- A one-line state chip")
    assert f"`GET {found.group(1)}`" in chip, (
        "1.2 must name the route app.py declares")
    assert "`flow_progress`" in chip and "`flows/progress.py`" in chip
    emitted = _keys(_real_progress())
    for key in ("blocks", "panels", "banked", "owed", "total"):
        assert f"`{key}`" in chip, key
        assert key in emitted, (
            f"1.2 says the chip reads `{key}`, and flow_progress does not "
            f"send it")


# ------------------------------------------------------- 3.3, the identity

#: A recipe whose exposure the old spelling and the new one write
#: differently: ``{:g}`` keeps six significant digits and drops the
#: millisecond.
_RECIPE = {"frame_type": "Light", "filter": "L", "exposure_s": 1234.5678,
           "gain": 100, "binning": 1}


def test_the_3_3_step_id_line_computes_the_ids_identity_mints():
    """The step_id line's own formula, evaluated, gives ``identity.step_id``'s
    id; it quotes the signature ``identity.step_signature`` spells for a
    1234.5678 s sub, and the gain spelling it gives for 100.5; and it names
    ``STEP_PLACES`` at the value identity uses. Nothing here is typed out: a
    change of spelling in identity turns this red until the spec follows.

    RED under mutant "revert the 3.3 step_id line" (S1's line, with
    ``{exposure_s:g}``):

        AssertionError: the spec's step_id formula names another step than
        identity.step_id
        assert 'e3069126bbf2...cb25dd230ffbe' == 'dcfb5e722444...ee357807949fc'
          - dcfb5e7224445318af8ee357807949fc
          + e3069126bbf25c9694bcb25dd230ffbe

    RED under mutant "3.3 STEP_PLACES typed as 2":

        assert '`STEP_PLACES` = 3' in '- `step_id = uuid5(NS_FLOWS,
        f"{target_id}/{stage_node_id}/{signature}/{n}").hex`, where
        `signature` is `identity.ste...

    RED under the code mutant "identity.STEP_PLACES = 2" (rebound in the
    test process; identity.py never edited), where the evaluated formula
    agrees because both sides use identity, and the quoted signature does
    not:

        assert '`Light/L/1234.57/100/1`' in '- `step_id = uuid5(NS_FLOWS,
        f"{target_id}/{stage_node_id}/{signature}/{n}").hex`, where
        `signature` is `identity.ste...
    """
    signature = identity.step_signature(**_RECIPE)
    line = _line(_section("3.3"), '- `step_id = uuid5(NS_FLOWS, f"')
    template = re.search(r'f"([^"]+)"', line).group(1)
    named = template.format(target_id="T", stage_node_id="S",
                            signature=signature, n=0, **_RECIPE)
    assert (uuid.uuid5(identity.NS_FLOWS, named).hex
            == identity.step_id("T", "S", n=0, **_RECIPE)), (
        "the spec's step_id formula names another step than identity.step_id")
    assert f"`{signature}`" in line
    gain = identity.step_signature(frame_type="Light", filter="L",
                                   exposure_s=60, gain=100.5,
                                   binning=1).split("/")[3]
    assert f"`{gain}`" in line
    assert f"`STEP_PLACES` = {identity.STEP_PLACES}" in line
    assert "{exposure_s:g}" not in line
    # Last, so a spelling change in identity is reported above as the spec
    # and the code disagreeing: the example must still tell the old spelling
    # from the new, or every check above would pass on either.
    assert f"{_RECIPE['exposure_s']:g}" != signature.split("/")[2], (
        "the example exposure spells the same under {:g} and under identity, "
        "so this test cannot tell the two apart; choose another")


def test_the_3_3_name_keyed_target_rule_is_the_rule_identity_keeps():
    """3.3 says a TARGET with a name and no typed coordinates is keyed on its
    name, with the prefix identity writes; and identity does exactly that: the
    key of a named target does not move when the catalogue's answer does, and
    a typed one is a geometry key.

    RED under mutant "drop the name-keyed TARGET bullet":

        AssertionError: expected exactly one line starting '- **A TARGET with
        a name and no typed coordinates is keyed on its name**', found 0
        assert 0 == 1

    RED under the code mutant "identity.NAME_KEY_PREFIX = 'named:'" (rebound
    in the test process; identity.py never edited):

        assert '`"named:"`' in "- **A TARGET with a name and no typed
        coordinates is keyed on its name** (S1 hardening, #189 A5).
        `identity.target_ke...
    """
    bullet = _line(_section("3.3"), "- **A TARGET with a name and no typed "
                                    "coordinates is keyed on its name**")
    for word in ("`identity.target_key`", "`name_key`",
                 "`identity.typed_coordinates`", "`canonical_name`"):
        assert word in bullet, word
    prefix = identity.NAME_KEY_PREFIX
    assert f'`"{prefix}"`' in bullet
    # The code half of the same sentence.
    here = identity.target_key({"name": "Jupiter"}, 1.0, 2.0, None)
    later = identity.target_key({"name": "Jupiter"}, 1.5, 3.0, None)
    typed = identity.target_key({"name": "M31", "ra": "00h 42m 44s",
                                 "dec": "+41 16 09"}, 0.71, 41.27, None)
    assert here == later and here.startswith(prefix)
    assert not typed.startswith(prefix)


# ------------------------------------------------ 3.6, the migration note

def _v2_file(directory: Path, fid: str) -> None:
    """A flow file as a v2 build left it, holding the 23.4 palette default."""
    directory.mkdir(parents=True, exist_ok=True)
    graph = {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                        "params": {"name": "M31", "ra": "00h 42m 44s",
                                   "dec": "+41 16 09", "rotation": 23.4}},
                       {"id": "c", "type": "capture", "x": 100, "y": 0,
                        "params": {"filter": "L", "exposure": 60,
                                   "gain": 100, "bin": "1", "count": 5,
                                   "goal": 0}}],
             "edges": [{"from": "t", "fromPort": "target", "to": "c",
                        "toPort": "run"}]}
    (directory / f"{fid}.json").write_text(json.dumps(
        {"schema_version": 2, "id": fid,
         "flow": {"id": fid, "name": "old", "folder": "My flows",
                  "graph": graph}}), encoding="utf-8")


def test_3_6_says_the_note_is_shown_on_every_read_until_a_save(
        tmp_path, monkeypatch):
    """The store says the 23.4 note on every read until the operator saves
    (only ``save()`` stamps FLOW_SCHEMA), and the spec said "shown once". It
    must say what the store does, nowhere "once", and the store must still
    do it.

    RED under mutant "restore 'shown once' in 3.6":

        AssertionError: the spec still says the migration note is said once
        ('shown once'); the store says it on every read until the operator
        saves
        assert not True
    """
    text = _spec().lower()
    for stale in ("shown once", "shows once", "one-time note"):
        # A bool, not ``stale not in text``: pytest would print the spec.
        said = stale in text
        assert not said, (
            f"the spec still says the migration note is said once "
            f"({stale!r}); the store says it on every read until the "
            f"operator saves")
    assert "every read until the operator saves" in _section("3.6")
    # The code half: two reads both carry the note, and a save retires it.
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    store = FlowStore(tmp_path / "flows")
    _v2_file(store.dir, "f1")
    first, second = store.get("f1"), store.get("f1")
    assert first.migrated and second.migrated
    store.save(second)
    assert store.get("f1").migrated == []


# ----------------------------------------------------- 5.6, the stand-down

def test_5_6_and_s1_record_the_plan_guide_ruling_the_engine_keeps():
    """The stand-down before a slew happens only when ``plan.guide`` is set: a
    guider the operator started under a guiding-off plan is theirs. 5.6 step
    2 records it as a RULING, S1 item 2 says it, and ``_setup_target`` keeps
    it.

    RED under mutant "drop the plan.guide ruling from 5.6 step 2":

        AssertionError: 5.6 step 2 must record the plan.guide ruling
        assert ('RULING' in '2. **Stand the guider down first**, bounded,
        via `_stand_down_guider` (`engine.py:4394`). The native
        `start_guiding` ...ive.py:871`). This is PLAUSIBLE and unverified on
        the rig. A simulator test must demonstrate it before the fix
        (I-02).')
    """
    step2 = _line(_section("5.6"), "2. ")
    assert "RULING" in step2 and "`plan.guide`" in step2, (
        "5.6 step 2 must record the plan.guide ruling")
    assert "`plan.guide`" in _line(_section("S1:"), "2. ")
    source = inspect.getsource(SequenceEngine._setup_target)
    assert re.search(r"if self\.plan\.guide:\s*\n\s*await "
                     r"self\._stand_down_guider\(\)", source), (
        "_setup_target no longer stands the guider down only under "
        "plan.guide; the ruling in 5.6 step 2 needs revisiting")


# ------------------------------------------------------ 5.9, CONTINUE

def test_5_9_names_the_session_continue_reads_and_the_rules_around_it():
    """CONTINUE's session is the flow's newest by ``created_ts`` of any
    status, read through ``current_for_flow``; ADOPT also needs the two
    fields within ``ADOPT_MAX_SEPARATION_ARCMIN`` (the value from
    ``continuation``); a continued plan keeps the session's ``cool_to``; and
    starts are refused, 409 ``resume_recovering``, while
    ``ResumeArm.recovering`` (#189 A7), which ``api/app.py`` answers.

    RED under mutant "revert the 5.9 session sentence" (S1's "newest session
    with ... status == dormant"):

        AssertionError: 5.9 must say which session CONTINUE reads:
        '`SessionStore.current_for_flow`'
        assert '`SessionStore.current_for_flow`' in '**Across nights
        (CONTINUE).** `run_flow` compiles with `flow_id` and looks for the
        newest session with `origin == "flow"`, `origin_id == flow_id` and
        `status == "dormant"`. If that session\\'s plan shares step ids with
        the new compile:'

    RED under mutant "5.9 ADOPT bound typed as 5":

        AssertionError: 5.9 must give ADOPT's bound as continuation has it:
        `ADOPT_MAX_SEPARATION_ARCMIN = 10`
        assert False

    RED under the code mutant "continuation.ADOPT_MAX_SEPARATION_ARCMIN =
    5.0" (rebound in the test process; continuation.py never edited):

        AssertionError: 5.9 must give ADOPT's bound as continuation has it:
        `ADOPT_MAX_SEPARATION_ARCMIN = 5`
        assert False

    RED under mutant "5.9 without the resume_recovering refusal":

        assert '409 `resume_recovering`' in '\\n**Within a session** (a
        crash, `/recover`, or auto-resume):\\n\\n- Every banked frame is an
        atomic ledger save (`engi...
    """
    s59 = _section("5.9")
    which = _line(s59, "**Across nights (CONTINUE).**")
    for phrase in ("`SessionStore.current_for_flow`", "`created_ts`",
                   "of any status", "abandoned"):
        assert phrase in which, (
            f"5.9 must say which session CONTINUE reads: {phrase!r}")
    bound = (f"`ADOPT_MAX_SEPARATION_ARCMIN = "
             f"{continuation.ADOPT_MAX_SEPARATION_ARCMIN:g}`")
    found = bound in s59
    assert found, (
        f"5.9 must give ADOPT's bound as continuation has it: {bound}")
    assert "`cool_to`" in s59 and "`ResumeArm.recovering`" in s59
    assert "409 `resume_recovering`" in s59
    # The code halves the sentences name.
    assert callable(SessionStore.current_for_flow)
    assert isinstance(inspect.getattr_static(ResumeArm, "recovering"),
                      property)
    assert '"code": "resume_recovering"' in APP.read_text(encoding="utf-8"), (
        "app.py no longer refuses a start with resume_recovering; 5.9 says "
        "it does")


def test_s1_item_8_carries_the_skipped_panel_exemption_as_debt():
    """S1 promised CONTINUE with "skipped panels exempt", and S1 has no groups:
    no ``TargetGroup.skipped_ids`` to read and no ``skip`` param to set one.
    Item 8 says it is debt for S2/S3, and the code agrees there is none yet.

    WHEN THIS GOES RED ON THE CODE HALF, S2 or S3 has built the exemption:
    rewrite item 8 to say so, and this test with it.

    RED under mutant "restore S1 item 8's 'skipped panels exempt'":

        AssertionError: S1 item 8 must name the debt and where it lands
        assert ('`TargetGroup.skipped_ids`' in '8. `run_flow` CONTINUE,
        dormant sessions only (5.9): the factored plan replace plus
        `engine.start(session=)` inside o...parately; `accept_dropped`
        (skipped panels exempt), `accept_recount`, `fresh`, and the ADOPT
        path for pre-S1 sessions.')
    """
    item8 = _line(_section("S1:"), "8. ")
    assert ("`TargetGroup.skipped_ids`" in item8 and "`skip`" in item8
            and "debt carried to S2/S3" in item8), (
        "S1 item 8 must name the debt and where it lands")
    assert "(skipped panels exempt)" not in item8
    built = sorted(str(p.relative_to(_SERVER))
                   for p in (_SERVER / "astrodeck").rglob("*.py")
                   if "skipped_ids" in p.read_text(encoding="utf-8"))
    assert built == [], (
        f"skipped_ids now exists in {built}: the exemption is no longer debt, "
        f"so S1 item 8 is stale")


# ----------------------------------------------------- 5.10, the finish clock

def test_5_10_says_hops_costed_is_returned_and_not_yet_rendered():
    """``compute_eta`` returns ``hops_costed`` and prices the hops from a seed
    of ``HOP_COST_S`` until one is measured, and no UI file reads the flag
    yet: saying "hops not yet costed" is S5's. When a UI starts rendering it,
    the code half goes red and the spec line needs updating.

    RED under mutant "restore the 5.10 compute_eta line" (S1's
    "remaining_visits x hop EMA" line):

        AssertionError: 5.10 must say hops_costed is returned and not yet
        rendered
        assert False
    """
    line = _line(_section("5.10"), "- `compute_eta`")
    said = "`hops_costed`" in line and "not yet rendered" in line
    assert said, "5.10 must say hops_costed is returned and not yet rendered"
    assert "S5" in line
    assert f"`HOP_COST_S` = {engine_mod.HOP_COST_S:g} s" in line
    assert '"hops_costed"' in inspect.getsource(SequenceEngine.compute_eta)
    assert UI_SRC.is_dir(), "the UI tree this reads is missing"
    rendered = sorted(str(p.relative_to(UI_SRC)) for p in UI_SRC.rglob("*")
                      if p.suffix in {".ts", ".tsx"}
                      and "hops_costed" in p.read_text(encoding="utf-8",
                                                       errors="replace"))
    assert rendered == [], (
        f"{rendered} now read hops_costed: 5.10's 'not yet rendered' is stale")


# ---------------------------------------------- 5.8 and 6.17, the clocks

def test_5_8_and_6_17_put_the_hold_checks_and_the_stop_retry_on_own_clocks():
    """The cloud hold keeps tracking on with no frame loop, so its floor, flip
    and tracking checks run on the hold's own clock (#203, #205); an idle stop
    the mount did not confirm is asked again on its own clock, at most once
    per ``IDLE_STOP_RETRY_S`` (the engine's value). Both 5.8 and 6.17 say
    both.

    RED under mutant "drop the cloud-hold bullet from 5.8":

        AssertionError: expected exactly one line starting '- **A cloud hold
        watches the mount on its own clock**', found 0
        assert 0 == 1

    RED under mutant "6.17 row without the stop retry":

        AssertionError: ('6.17', '`IDLE_STOP_RETRY_S` (60 s)')
        assert '`IDLE_STOP_RETRY_S` (60 s)' in "| 6.17 | Idle mount | A mount
        left tracking with no frame loop watching it is park-held on the idle
        clock (`WAIT_TEAR...ich keeps tracking on by design, runs its floor,
        flip and tracking checks on the hold's own clock (5.8, #203, #205). |"

    RED under the code mutant "engine.IDLE_STOP_RETRY_S = 30.0" (rebound in
    the test process; engine.py never edited):

        AssertionError: ('5.8', '`IDLE_STOP_RETRY_S` (30 s)')
        assert '`IDLE_STOP_RETRY_S` (30 s)' in "- **A cloud hold watches the
        mount on its own clock** (#203, #205). The hold keeps tracking on by
        design, for up to `...t does not confirm is asked again at most once
        per `IDLE_STOP_RETRY_S` (60 s), never from the wait loop's tick
        (6.17)."
    """
    retry = f"`IDLE_STOP_RETRY_S` ({engine_mod.IDLE_STOP_RETRY_S:g} s)"
    hold = _line(_section("5.8"),
                 "- **A cloud hold watches the mount on its own clock**")
    row = _line(_spec(), "| 6.17 |")
    for where, text in (("5.8", hold), ("6.17", row)):
        for word in ("floor", "flip", "tracking", "hold's own clock",
                     "#203", "#205", retry):
            assert word in text, (where, word)


def test_5_8_says_the_hold_watch_is_built_and_the_engine_has_it():
    """5.8 says this round built the hold's watch (``_hold_watch``), and the
    engine has it: ``_hold_for_clear`` looks at the mount between probes, the
    looks ask the two predicates 5.8 names, and the mount's tracking is read
    before the check frame. The first draft of this revision said "Today the
    hold runs none of the three" while the round was building it (#203,
    #205), so the claim is held to the code in both directions: a spec that
    says "not built" over a hold that watches, or "built" over one that does
    not, goes red.

    RED under mutant "5.8 says the hold is not yet built" (the first draft's
    sentence put back):

        AssertionError: 5.8 must say the hold's watch is built, as
        _hold_for_clear has it
        assert False

    Each code mutant below is one engine method rebuilt from a scratch copy
    and rebound onto SequenceEngine inside the test process; engine.py was
    never edited.

    RED under the code mutant "_hold_for_clear as HEAD had it" (S1's body,
    with no watch and no tracking read):

        AssertionError: _hold_for_clear no longer looks at the mount between
        probes; 5.8 says it does (#203)
        assert 'self._hold_watch' in 'async def _hold_for_clear(self,
        reason: str, target: Target | None) -> None:\\n    \"\"\"Hold the
        run until the sky clear...sumed after {held_min:.0f} min of
        cloud")\\n            return\\n    finally:\\n
        self._holding_for_clear = False\\n'

    RED under the code mutant "no tracking read before the probe" (today's
    ``_hold_for_clear`` with ``await self._tracking_now() is False`` made
    ``False``):

        AssertionError: _hold_for_clear no longer reads tracking before its
        check frame; 5.8 says it does (#205)
        assert 0 <= -1

    RED under the code mutant "the flip watch never asks _idle_flip_due"
    (today's ``_hold_flip_watch`` with that call made ``False``):

        AssertionError: the hold's looks no longer ask _idle_flip_due; 5.8
        names it
        assert 'self._idle_flip_due(' in '    async def
        _hold_for_clear(self, reason: str, target: Target | None) -> None:...

    Unchanged under the control "a sentence appended after the Revision 3
    table" (all 12 pass).
    """
    hold = _line(_section("5.8"),
                 "- **A cloud hold watches the mount on its own clock**")
    # "none of the three" may follow "Before it,", which is history; before
    # it, it would be a claim about today.
    said = ("`_hold_watch`" in hold and "none of the three" not in
            hold.split("Before it,")[0] and "not yet built" not in hold)
    assert said, (
        "5.8 must say the hold's watch is built, as _hold_for_clear has it")
    for word in ("`_altitude_limit_verdict`", "`_idle_flip_due`"):
        assert word in hold, word
    # The code half. The watch's helpers are the engine's ``_hold_*``
    # methods; the predicates may be asked from any of them.
    source = inspect.getsource(SequenceEngine._hold_for_clear)
    assert "self._hold_watch" in source, (
        "_hold_for_clear no longer looks at the mount between probes; 5.8 "
        "says it does (#203)")
    helpers = "".join(inspect.getsource(fn)
                      for name, fn in vars(SequenceEngine).items()
                      if name.startswith("_hold_") and inspect.isfunction(fn))
    for predicate in ("_altitude_limit_verdict", "_idle_flip_due"):
        assert f"self.{predicate}(" in helpers, (
            f"the hold's looks no longer ask {predicate}; 5.8 names it")
    tracked = source.find("self._tracking_now()")
    probed = source.find("self._cloud_probe(")
    assert 0 <= tracked < probed, (
        "_hold_for_clear no longer reads tracking before its check frame; "
        "5.8 says it does (#205)")


def test_5_8_says_a_stopped_hold_slews_back_and_the_engine_does():
    """5.8 says a cloud hold that has stopped the mount slews it back to the
    held target before it judges the sky, after the zenith keep-out and when
    the mount was stopped on another target (#225), behind the slew gate and
    the Sun check; and that the case of a mount still tracking the last
    target is #224, waiting on the owner. The engine does both slews, from
    the two places 5.8 names, through ``_hold_repoint``, which asks the slew
    gate and the Sun check before it moves anything.

    Found by the spec-fidelity review of the hardening round: the round built
    both slews (T8, and the #225 fix), and the spec said nothing of a hold
    moving the mount, while #224 says that shape needs an owner ruling.

    Each spec mutant below ran against a scratch copy of the spec, with this
    module's ``SPEC`` pointed at it; the spec itself was never edited, and
    its sha256 was the same before and after.

    RED under mutant "drop the 5.8 re-point bullet":

        AssertionError: expected exactly one line starting '- **A hold that
        has stopped the mount slews it back to the target before it judges
        the sky**', found 0
        assert 0 == 1
         +  where 0 = len([])

    RED under mutant "drop owner list item 5":

        AssertionError: the owner list must carry the #224 question 5.8
        hands it
        assert False

    RED under the code mutant "_hold_repoint without the slew gate" (the
    method rebuilt from a scratch copy with its ``_safety_gate`` line made
    ``pass``, rebound onto SequenceEngine in the test process; engine.py
    never edited):

        AssertionError: _hold_repoint no longer asks the slew gate before it
        moves the mount; 5.8 says each slew goes through it
        assert 'self._safety_gate(context="slew"' in 'async def
        _hold_repoint(self, target: Target, *,\\n
        elsewhere: bool = False) -> None:\\n    \"\"\"P...ld for cloud - back
        on the target after the "\\n                           "zenith
        keep-out; checking the sky again")\\n'

    RED under the code mutant "the hold resumes in place whatever the mount
    is on" (``_hold_for_clear`` rebuilt with ``elsewhere=True`` removed from
    its ``_hold_repoint`` call):

        AssertionError: _hold_for_clear no longer slews a mount stopped on
        another target to the held target; 5.8 says it does (#225)
        assert 'self._hold_repoint(target, elsewhere=True)' in 'async def
        _hold_for_clear(self, reason: str, target: Target | None) ->
        None:\\n    \"\"\"Hold the run until the sky clear... set
        aside")\\n        raise\\n    finally:\\n
        self._holding_for_clear = False\\n        self._hold_parked =
        None\\n'

    Unchanged under the control "unrelated edit in section 7" (both this
    test and the status-line test pass on that copy).
    """
    bullet = _line(_section("5.8"), "- **A hold that has stopped the mount "
                                    "slews it back to the target before it "
                                    "judges the sky**")
    for word in ("`_hold_repoint`", "zenith keep-out", "slew gate",
                 "Sun check", "#225", "#224", "owner"):
        assert word in bullet, word
    owner = _section("Still")
    asked = "5. #224 and #225" in owner
    assert asked, "the owner list must carry the #224 question 5.8 hands it"
    # The code halves: the two call sites 5.8 names, and the gates.
    repoint = inspect.getsource(SequenceEngine._hold_repoint)
    for gate, what in (('self._safety_gate(context="slew"', "the slew gate"),
                       ("self.hub._check_solar(", "the Sun check")):
        assert gate in repoint, (
            f"_hold_repoint no longer asks {what} before it moves the "
            f"mount; 5.8 says each slew goes through it")
    hold = inspect.getsource(SequenceEngine._hold_for_clear)
    assert "self._hold_repoint(target, elsewhere=True)" in hold, (
        "_hold_for_clear no longer slews a mount stopped on another target "
        "to the held target; 5.8 says it does (#225)")
    watch = inspect.getsource(SequenceEngine._hold_watch)
    assert "self._hold_repoint(target)" in watch, (
        "_hold_watch no longer slews back after the keep-out; 5.8 says it "
        "does")


def test_the_status_line_scopes_what_describes_the_code_as_built():
    """The status line said that wherever the body describes S0 and S1, it
    describes the code after the hardening round. Revision 3 edited the
    sections in its table and no others, so 5.6 step 3 still called the
    fabricated ``centered: True`` a live defect, 5.6 step 4 the silent
    dropped rotator, 5.6 step 9 the dither counter that spans targets, and
    section 9 the uuid4 ids, all four removed by S0 or S1. The line must
    scope its claim to Revision 3's sections and say what "today" means
    elsewhere, and the Revision 3 preamble must not claim the whole body.

    Found by the spec-fidelity review of the hardening round. The mutants
    ran against a scratch copy of the spec, as in the test above.

    RED under mutant "restore the status line's blanket claim":

        AssertionError: the status line claims the whole body describes the
        code as built; Revision 3 edited only the sections in its table
        assert not True

    RED under mutant "restore 'This revision brings the body in line'":

        AssertionError: the Revision 3 preamble claims the whole body; it
        edited only the sections in its table
        assert not True
    """
    status = _line(_spec(), "- Status:")
    blanket = "where the text below describes them, it describes the code" \
        in status
    assert not blanket, (
        "the status line claims the whole body describes the code as built; "
        "Revision 3 edited only the sections in its table")
    assert "The sections Revision 3 lists" in status
    assert "before S0" in status
    preamble = _line(_spec(), "2026-09-24. S0 and S1 are built")
    whole = "brings the body in line" in preamble
    assert not whole, (
        "the Revision 3 preamble claims the whole body; it edited only the "
        "sections in its table")
    # The four sentences the status line warns about are still there, so the
    # warning is still needed; when a later revision rewrites them, drop the
    # examples from the status line and this check with them.
    s56 = _section("5.6")
    for stale in ('fabricated `{"centered": True', "Today that case is silent",
                  "Today the counter spans targets"):
        assert stale in s56, stale
    assert "Today every compile mints uuid4" in _spec()
