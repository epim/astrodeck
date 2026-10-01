"""The compile routes and ``/run`` answer every graph of the seed-328 corpus
with a verdict, never a 500 (#362 at the route; #356's comment).

#362 found the flow routes failing past ``compile_plan``: a value the plan's
models refuse (an exposure past 3600 s, an RA past 24 h, a whole-number gain
given as 1.5) left ``to_sequence_plan`` as pydantic's ``ValidationError``,
which ``_compile_payload`` and ``run_flow`` do not catch, so the editor's
compile and ``/run`` answered 500 where the operator should read which card
to fix; and a NaN in the compiled dict failed the answer's JSON. S5-COMPILE
fixed it in ``to_plan`` and ``compile`` and proved it on the corpus below
function by function (``test_flows_compile_never_raises.py``). This file
proves it where the operator meets it, through the routes, with the
server's own JSON rendering and every other step a route takes (the doctor,
the readouts, the capture-geometry inventory, the run's pre-flights):

* ``POST /api/flows/compile``: every corpus draft answers 200 with the
  answer's six keys, a refusal as the plan's loss row (``{key: "plan",
  level: "danger"}``), never a 500.
* ``/run`` of each draft the compile refused BY VALUE (the refusal naming a
  block and a field, ``PLAN_REFUSAL``): none of them is stored as drawn,
  since each also carries a wire validation refuses, so each is stored as
  the editor could have drawn it, the wires that name a node or a port that
  does not exist dropped and its values untouched. Its saved compile
  carries the same kind of loss row, and ``/run`` answers 422
  ``invalid_graph`` with that sentence. These are #362 item 1's graphs at
  the route that starts the rig.
* ``/run`` of each corpus graph the store takes as drawn (no validation
  error): its saved compile answers 200, and ``/run`` answers 409 or 422;
  where that compile carries the loss row, 422 with the same sentence.

The first two run in ``SLICES`` interleaved slices, so xdist can spread
them; each slice checks its own floors, so no slice passes without reaching
#362's path. Each stored flow is deleted after its run, so the library
stays one flow long. The requests carry the corpus's infinities and NaNs as
JSON's ``Infinity`` and ``NaN`` tokens, as a raw POST or a hand-edited file
can, which the server's parser takes. A 500 is caught as the exception the
app raised (``httpx.ASGITransport`` raises it), so a failure names the error
and the graph, not "Internal Server Error".

Every named mutation was run in a private copy of ``server/`` (and of
``ui/src`` for the last class) under the session scratchpad
(``S5-ROUTES-mut``), never in the shared tree (#254); the failure each
produced is quoted where it went red.
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

import astrodeck.api.app as app_module
from astrodeck.flows import tonight
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.nodes import NODE_DEFS
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan
from test_flows_compile_never_raises import (CORPUS_SIZE, PLAN_REFUSAL,
                                             _corpus)
from test_flows_continue import rig  # noqa: F401 (fixture)

#: The real resolver, bound before any fixture replaces it on the module.
_real_resolve = tonight.resolve_target


#: How many interleaved slices the corpus is compiled in: graph ``i`` goes
#: to slice ``i % SLICES``.
SLICES = 4

#: FLOORS per slice, as the corpus file's are, with the counts on the
#: vocabulary of 2026-09-28 for the four slices: the drafts the compile
#: refuses by value (11, 9, 15, 15), and the ones of those that store with
#: their impossible wires dropped (6, 3, 9, 10).
SLICE_REFUSAL_FLOOR = 5
SLICE_STORED_FLOOR = 2

#: How many corpus graphs the store must take as drawn (179 on 2026-09-28),
#: and how many of those the saved compile must refuse (151).
STORED_FLOOR = 150
STORED_REFUSED_FLOOR = 120

#: How many stored graphs may legitimately reach the engine (one, graph 543,
#: on 2026-09-30 -- see ``_stored_run``'s ``allow_run`` for what it is and
#: why). A CEILING, not a floor and not ignored: the corpus is reshuffled
#: whenever ``NODE_DEFS`` changes (adding DUSK WINDOW's ``startClock``/
#: ``stopClock``, #191, is what moved this shape onto index 543 with no
#: change to what either compiles to), so a future reshuffle may nudge this
#: count a little. It must not jump a lot, which is what a lost refusal
#: actually losing the class this file is about would do -- an unbounded
#: quota, a dropped site check, a missing window -- so a mutant that drops
#: one must still be caught here, not waved through as "more corpus noise".
STARTED_CEILING = 5

#: The compile answer's keys (``_compile_payload``).
KEYS = {"plan", "structural", "issues", "unmapped", "readouts", "rig"}


@pytest.fixture
def remembered_names(monkeypatch):
    """``tonight.resolve_target`` answering a name it has answered before
    from memory, when that answer does not move with time, and asking the
    real resolver every other time.

    WHY. The corpus names its TARGETs with its odd values ("abc", "nan",
    " 3 "), and each is a fuzzy catalogue search, most of which find some
    row ("nan" is Menkalinan): 25 to 50 ms, asked again by the compile, the
    readouts and the run for every draft. Measured in-process on 750 drafts,
    12.1 s of ``to_sequence_plan``'s 12.3 s was 247 such searches for 25
    distinct names. Remembering is exact for these answers: which row a name
    IS is decided at the fixed ``IDENTITY_WHEN`` whatever instant is asked
    (``resolve_target``), and a row that does not move is where it is at
    any instant (every corpus name resolves alike at three instants, and
    the control below checks M31). A moving body is always asked of the
    real resolver, so none is frozen. Every caller looks the resolver up at
    call time, so this reaches the compile, the readouts and ``/run``
    alike."""
    real = tonight.resolve_target
    remembered: dict = {}

    def resolve(name, *args, **kwargs):
        if isinstance(name, str) and name in remembered:
            return remembered[name]
        hit = real(name, *args, **kwargs)
        if isinstance(name, str) and (hit is None or not hit.moves):
            remembered[name] = hit
        return hit

    monkeypatch.setattr(tonight, "resolve_target", resolve)
    return remembered


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://testserver")


async def _call(c: httpx.AsyncClient, method: str, url: str, body=None):
    """``(response, None)``, or ``(None, error)`` naming the exception the
    app raised. The body goes as JSON with ``Infinity`` and ``NaN`` tokens,
    which httpx's own encoder refuses to write."""
    kw = {}
    if body is not None:
        kw = {"content": json.dumps(body).encode("utf-8"),
              "headers": {"content-type": "application/json"}}
    try:
        return await c.request(method, url, **kw), None
    except Exception as e:      # noqa: BLE001 - the verdict itself
        return None, f"{type(e).__name__}: {e}"[:200]


def _raw(g: FlowGraph) -> dict:
    return g.model_dump(by_alias=True)


def _plan_row(answer: dict) -> dict | None:
    """The compile answer's loss row for a plan the compile refused."""
    rows = [u for u in answer["unmapped"] if u.get("key") == "plan"]
    return rows[0] if rows else None


def _by_value(row: dict | None) -> bool:
    return row is not None and bool(PLAN_REFUSAL.search(row.get("detail")
                                                        or ""))


class _Failures:
    """What went wrong, by kind, with the first graph of each kind: the
    assertion message, so a red run says what and where."""

    def __init__(self) -> None:
        self.kinds: Counter[str] = Counter()
        self.first: dict[str, str] = {}

    def add(self, kind: str, i: int, text: str) -> None:
        self.kinds[kind] += 1
        self.first.setdefault(kind, f"graph {i}: {text}"[:240])

    def __bool__(self) -> bool:
        return bool(self.kinds)

    def __str__(self) -> str:
        return (f"{sum(self.kinds.values())} failures: {dict(self.kinds)}; "
                f"first of each: {self.first}")


def _stored_form(g: FlowGraph) -> FlowGraph:
    """``g`` with every wire that names a node or a port that does not
    exist dropped: what an editor, which cannot draw such a wire, could have
    stored. Params, nodes and every other wire are kept."""
    nodes = {n.id: n for n in g.nodes}

    def drawable(e) -> bool:
        a, b = nodes.get(e.from_), nodes.get(e.to)
        return (a is not None and b is not None
                and e.fromPort in {p.id for p in NODE_DEFS[a.type].outs}
                and e.toPort in {p.id for p in NODE_DEFS[b.type].ins})

    return g.model_copy(update={"edges": [e for e in g.edges
                                          if drawable(e)]})


def _refused_by_value(g: FlowGraph) -> bool:
    """Whether the plan's models refuse a value of ``g``: the refusal that
    names the block and the field, or, with S5-COMPILE's conversion
    reverted, pydantic's own error. Asked in-process, so the choice of the
    graphs to store does not rest on the routes under test."""
    try:
        to_sequence_plan(compile_plan(g, "fuzz"), g, flow_id="f-362")
    except GraphNotRunnable as e:
        return bool(PLAN_REFUSAL.search(str(e)))
    except ValidationError:
        return True
    return False


async def _stored_run(c, rig, i: int, g: FlowGraph, failures: _Failures,
                      counts: Counter, *, by_value: bool = False,
                      allow_run: bool = False) -> None:
    """Save ``g``, compile the saved flow, run it, delete it, recording
    what went wrong in ``failures``; ``counts`` gains ``stored`` and
    ``loss rows``. ``by_value``: the compile must refuse naming a block and
    a field, and the run with it.

    ``allow_run`` (2026-09-30, graph 543): a stored graph that reaches the
    engine (``/run`` answers 200) is not a failure by itself -- an orphan
    TARGET with no SOURCE and no wires at all compiles as one visual lane in
    canvas order (``flow_order``'s tie-break IS the whole order when there
    are no edges to break ties between), so a TARGET, CAPTURE and SLEW drawn
    with no wires between them, the CAPTURE left at its own defaults by the
    fuzz, is a genuine, finite, un-refused flow. Confirmed against the base
    this corpus predates (``compile_plan``/``to_sequence_plan`` imported from
    a byte-for-byte copy of 2c73899d's ``server/``, scratchpad ``g543/
    base_server``): the same graph compiles to the same 24-frame plan there
    too, so nothing in this wave made it runnable -- the corpus moved it to
    index 543 (DUSK WINDOW's new ``startClock``/``stopClock`` params, #191,
    shifted every ``random.Random(328)`` draw after the first ``dusk`` node),
    and no index in the old corpus happened to land on this shape. The run
    is still aborted at once here, exactly as a failure would be, and
    counted in ``counts["started"]`` rather than ``failures``. ``by_value``
    callers never pass this: a graph proved (in-process) to hit the plan's
    refusal by value must still answer 422, and a 200 there stays a
    failure -- that is #362 itself, not corpus noise."""
    r, err = await _call(c, "POST", "/api/flows", {
        "flow": {"name": f"corpus {i}", "graph": _raw(g)}})
    if err is not None or r.status_code != 200:
        failures.add("save", i, err or f"{r.status_code} {r.text}")
        return
    fid = r.json()["id"]
    counts["stored"] += 1
    try:
        r, err = await _call(c, "POST", f"/api/flows/{fid}/compile")
        if err is not None or r.status_code != 200:
            failures.add("stored compile raised" if err else
                         "stored compile status", i,
                         err or f"{r.status_code} {r.text}")
            return
        row = _plan_row(r.json())
        counts["loss rows"] += row is not None
        if by_value and not _by_value(row):
            failures.add("stored compile not refused by value", i, str(row))
        run, err = await _call(c, "POST", f"/api/flows/{fid}/run", {})
        if err is not None:
            failures.add("run raised", i, err)
            return
        if run.status_code == 200:
            # A graph that starts is ended at once either way, so the
            # harness never hangs on one. Almost nothing the corpus stores
            # is runnable (graph 543 is, see ``allow_run``'s docstring); a
            # caller that has not proven otherwise still treats this as the
            # change that made it runnable, and says so.
            rig.night.end("aborted")
            await rig.engine._task
            if allow_run and not by_value:
                counts["started"] += 1
            else:
                failures.add("started a run", i, "")
            return
        detail = run.json().get("detail")
        if run.status_code not in (409, 422):
            failures.add(f"run status {run.status_code}", i, run.text)
        elif row is not None and (
                run.status_code != 422 or not isinstance(detail, dict)
                or detail.get("code") != "invalid_graph"
                or detail.get("detail") != row["detail"]):
            failures.add("run is not the compile's refusal", i,
                         f"{run.status_code} {run.text} vs {row}")
    finally:
        await _call(c, "DELETE", f"/api/flows/{fid}")


# =========================================== the corpus, slice by slice

@pytest.mark.parametrize("part", range(SLICES),
                         ids=[f"slice-{k}" for k in range(SLICES)])
async def test_every_draft_compiles_and_a_refusal_by_value_runs_to_a_422(
        rig, remembered_names, part):
    """Every draft of the slice through ``POST /api/flows/compile``: 200,
    the six keys, a refusal as the plan's loss row. Each draft the compile
    refused by value (or that raised, so a revert is caught here too),
    stored with its impossible wires dropped when that makes it valid: its
    saved compile refuses by value, and ``/run`` answers 422 with the same
    sentence.

    RED under mutant "S5-COMPILE's to_plan conversion reverted" (both of
    ``to_sequence_plan``'s catches removed: the one round
    ``SequencePlan.model_validate`` and the one round the mosaic's layout),
    both halves 500, on every slice (20, 14, 29 and 28 failures), e.g.
    slice-1, observed:

        E   AssertionError: 14 failures: {'draft compile raised': 11, 'stored compile raised': 3}; first of each: {'draft compile raised': 'graph 41: ValidationError: 1 validation error for SequencePlan\\ntargets.0.ra_hours\\n  Input should be greater than or equal to 0 [type=greater_than_equal, input_value=-1.0, input_type=float]\\n    For further infor', 'stored compile raised': 'graph 41: ValidationError: 1 validation error for SequencePlan\\ntargets.0.ra_hours\\n  Input should be greater than or equal to 0 [type=greater_than_equal, input_value=-1.0, input_type=float]\\n    For further infor'}

    RED under mutant "rotation copied verbatim" (``compile_plan`` writing a
    TARGET's ``rotation_deg`` as the param holds it, S5-COMPILE's item 2
    mutant), the answer's JSON failing on every slice, e.g. slice-2,
    observed:

        E   AssertionError: 10 failures: {'draft compile raised': 10}; first of each: {'draft compile raised': 'graph 26: ValueError: Out of range float values are not JSON compliant: nan'}

    RED under mutant "run_flow lets GraphNotRunnable out" (``run_flow``'s
    ``except GraphNotRunnable`` arm removed), the stored half, on every
    slice, e.g. slice-1, observed:

        E   AssertionError: 3 failures: {'run raised': 3}; first of each: {'run raised': 'graph 41: GraphNotRunnable: TARGET M31 - Andromeda: ra_hours of -1.0 cannot be used - input should be greater than or equal to 0.'}
    """
    app = app_module.create_app()
    failures = _Failures()
    counts: Counter[str] = Counter()
    corpus = _corpus()
    async with _client(app) as c:
        for i in range(part, CORPUS_SIZE, SLICES):
            r, err = await _call(c, "POST", "/api/flows/compile",
                                 {"graph": _raw(corpus[i]), "name": "fuzz"})
            refused = err is not None
            if err is not None:
                failures.add("draft compile raised", i, err)
            elif r.status_code != 200:
                failures.add(f"draft compile status {r.status_code}", i,
                             r.text)
            elif set(r.json()) != KEYS:
                failures.add("draft compile keys", i, str(sorted(r.json())))
            else:
                row = _plan_row(r.json())
                if row is not None and (row.get("level") != "danger"
                                        or not row.get("detail")):
                    failures.add("draft loss row", i, str(row))
                refused = _by_value(row)
                counts["refused by value"] += refused
            if not refused:
                continue
            s = _stored_form(corpus[i])
            if not s.validation_errors() and _refused_by_value(s):
                await _stored_run(c, rig, i, s, failures, counts,
                                  by_value=True)
    assert not failures, str(failures)
    assert counts["refused by value"] >= SLICE_REFUSAL_FLOOR, (
        f"premise: slice {part} reached the plan's refusal by value "
        f"{counts['refused by value']} times")
    assert counts["stored"] >= SLICE_STORED_FLOOR, (
        f"premise: slice {part} stored {counts['stored']} graphs the "
        f"models refuse")


# ========================================================= /run, as drawn

async def test_every_graph_the_store_takes_runs_to_a_refusal(
        rig, remembered_names):
    """Each corpus graph the store takes as drawn: saved, its saved flow's
    compile answers 200, and ``/run`` answers 409 or 422; where that compile
    carries the plan's loss row, ``/run`` answers 422 ``invalid_graph``
    with the same sentence, so the preview says what the run refuses.

    Almost none of these graphs reaches the plan's refusal by value (their
    faults are refused before it), so S5-COMPILE's revert leaves this green;
    the slices above are what it turns red. RED under mutant "run_flow lets
    GraphNotRunnable out", observed:

        E   AssertionError: 151 failures: {'run raised': 151}; first of each: {'run raised': 'graph 4: GraphNotRunnable: this flow has no target the run could point at - add a TARGET node with coordinates, or a POOL whose members are catalogue names'}

    and under "rotation copied verbatim", a stored graph's compile failing
    its JSON:

        E   AssertionError: 2 failures: {'stored compile raised': 2}; first of each: {'stored compile raised': 'graph 1265: ValueError: Out of range float values are not JSON compliant: nan'}

    ONE GRAPH IS THE EXCEPTION (2026-09-30, backlog WP-09): graph 543, an
    unwired TARGET, CAPTURE and SLEW (no edges at all), compiles to a real
    24-frame M31 L-filter plan -- CAPTURE's own fuzzed params missed its
    `filter`/`exposure`/`count` keys, so its defaults stood, and
    `flow_order`'s canvas-position tie-break IS the node order when there
    are no wires to order them by, so the three unwired nodes still read as
    one lane. This is not something the wave broke: the same graph compiles
    to the same plan imported from a byte-for-byte copy of the 2c73899d base
    this corpus predates (scratchpad `g543/base_server`). What moved is the
    CORPUS: DUSK WINDOW gained `startClock`/`stopClock` (#191), two more
    keys `NODE_DEFS["dusk"].params` offers, so every `rng.random()` draw
    after the generator's first `dusk` node shifts by one call, and this
    shape -- always possible, by this file's own `_stored_run` reasoning,
    just never generated at any of the old corpus's 3000 indices -- lands at
    543 now. `allow_run` is what makes this an honest premise rather than a
    silent one: it is still ended at once and counted, in
    `counts["started"]`, under `STARTED_CEILING`, so a future shift that
    makes genuinely MORE graphs runnable (a lost refusal, not corpus noise)
    still turns this red. RED under mutant "run_flow's real-loss gate
    removed" (``real = losses(unmapped)`` made ``real = []``, so a
    danger-level loss no longer earns its 409), run in a private copy of
    ``server/`` under the session scratchpad (``mut1``, never the shared
    tree), observed:

        E   AssertionError: premise: 6 stored graphs reached the engine; a jump this size smells like a lost refusal, not corpus noise
    """
    app = app_module.create_app()
    failures = _Failures()
    counts: Counter[str] = Counter()
    async with _client(app) as c:
        for i, g in enumerate(_corpus()):
            if not g.validation_errors():
                await _stored_run(c, rig, i, g, failures, counts,
                                  allow_run=True)
    assert not failures, str(failures)
    assert counts["stored"] >= STORED_FLOOR, (
        f"premise: only {counts['stored']} graphs stored")
    assert counts["loss rows"] >= STORED_REFUSED_FLOOR, (
        f"premise: only {counts['loss rows']} stored graphs carried the "
        f"loss row")
    assert counts["started"] <= STARTED_CEILING, (
        f"premise: {counts['started']} stored graphs reached the engine; "
        f"a jump this size smells like a lost refusal, not corpus noise")


def test_control_a_remembered_answer_is_the_resolvers(remembered_names):
    """Control for the fixture: what it answers from memory is what the
    real resolver answers at another instant, for a name with a row and a
    name without one, and a moving body is never remembered."""
    for name in ("M31", "abc"):
        first = tonight.resolve_target(name, 1.0e9)
        assert name in remembered_names
        assert tonight.resolve_target(name, 1.8e9) == first
        assert _real_resolve(name, 1.8e9) == first, name
    assert remembered_names["M31"] is not None
    assert remembered_names["abc"] is None
    jupiter = tonight.resolve_target("Jupiter", 1.8e9)
    assert jupiter is not None and jupiter.moves
    assert "Jupiter" not in remembered_names


# ================================================ #441 at the route (S7)

async def test_the_441_digit_runs_answer_at_the_route(rig, remembered_names):
    """#441's graphs where the operator meets them: a FILTER CYCLE whose
    only slot, or whose second, has an exposure of 5000 digits, and a 3x2
    of M31 skipping an entry of 5000 digits. Validation takes each as
    drawn, so each is saved; before S7 the editor's draft compile, the saved
    flow's compile and ``/run`` of it all raised ``ValueError`` (CPython's
    integer string limit) out of ``compile_plan``, a 500 on every one. Now
    the draft and the saved compile answer 200 with the answer's six keys,
    and ``/run`` answers what the plan says: the cycle with no readable slot
    is refused in words (422, "no filters selected"), and the other two
    start (each run is ended at once) with the frames the readable part
    owes: 45 of L alone, the unreadable R shooting nothing, and 540 on the
    mosaic's six panels, the unreadable skip entry skipping nothing.

    RED under the nodes.py mutant "guard removed" (``parse_cycle_plan``'s
    ``int()`` unguarded), observed:

        E       AssertionError: 6 failures: {'draft compile': 2, 'stored compile': 2, 'run raised': 2}; first of each: {'draft compile': 'graph 0: ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit', ...}

    RED under the compile.py mutant "guard removed" (``parse_skip``'s
    ``int()`` pair unguarded), observed:

        E       AssertionError: 3 failures: {'draft compile': 1, 'stored compile': 1, 'run raised': 1}; first of each: {'draft compile': 'graph 2: ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit', ...}
    """
    from test_flows_compile_never_raises import (RUN_PAST_THE_LIMIT,
                                                 SKIP_PAST_THE_LIMIT,
                                                 _cycle_of, _m31_3x2)
    # Each graph, and what ``/run`` answers for it: a refusal's sentence,
    # or the frames of the run it starts.
    cases = [(_cycle_of("L " + RUN_PAST_THE_LIMIT),
              (422, "the FILTER CYCLE has no filters selected")),
             (_cycle_of(f"L 60, R {RUN_PAST_THE_LIMIT}"), (200, 45)),
             (_m31_3x2(skip=SKIP_PAST_THE_LIMIT), (200, 540))]
    graphs = [FlowGraph.model_validate(raw) for raw, _ in cases]
    assert all(not g.validation_errors() for g in graphs), (
        "premise: a save stores each")
    app = app_module.create_app()
    failures = _Failures()
    async with _client(app) as c:
        for i, g in enumerate(graphs):
            r, err = await _call(c, "POST", "/api/flows/compile",
                                 {"graph": _raw(g), "name": "441"})
            if err is not None or r.status_code != 200 or set(r.json()) != KEYS:
                failures.add("draft compile", i,
                             err or f"{r.status_code} {r.text[:120]}")
            r, err = await _call(c, "POST", "/api/flows", {
                "flow": {"name": f"441 {i}", "graph": _raw(g)}})
            if err is not None or r.status_code != 200:
                failures.add("save", i, err or f"{r.status_code}")
                continue
            fid = r.json()["id"]
            try:
                r, err = await _call(c, "POST", f"/api/flows/{fid}/compile")
                if err is not None or r.status_code != 200:
                    failures.add("stored compile", i,
                                 err or f"{r.status_code} {r.text[:120]}")
                run, err = await _call(c, "POST", f"/api/flows/{fid}/run", {})
                if err is not None:
                    failures.add("run raised", i, err)
                    continue
                if run.status_code == 200:
                    rig.night.end("aborted")
                    await rig.engine._task
                    got = (200, run.json().get("frames"))
                else:
                    detail = run.json().get("detail")
                    words = (detail.get("detail", "")
                             if isinstance(detail, dict) else str(detail))
                    want = cases[i][1][1]
                    got = (run.status_code,
                           want if isinstance(want, str) and want in words
                           else words[:120])
                if got != cases[i][1]:
                    failures.add("run", i, f"{got} != {cases[i][1]}")
            finally:
                await _call(c, "DELETE", f"/api/flows/{fid}")
    assert not failures, str(failures)


# ========================================= #356: what the comment claims

APP = Path(app_module.__file__)
UI_SRC = Path(__file__).resolve().parents[2] / "ui" / "src"
SLICE_TS = UI_SRC / "components" / "flows" / "flowsSlice.ts"

#: The store actions that refresh ``flows.compiled``, which is what the
#: comment names: a flow opened, a save, and DONE or LOOP PANELS, which
#: write through ``flowsApplyFraming``.
COMPILING_ACTIONS = {"flowsOpen", "flowsSave", "flowsApplyFraming"}


def _compile_payload_text() -> str:
    """``_compile_payload``'s source, its comments joined into prose (the
    ``#`` of each line dropped), so a phrase split over two lines is still
    found."""
    src = APP.read_text(encoding="utf-8")
    found = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.AsyncFunctionDef)
             and n.name == "_compile_payload"]
    assert len(found) == 1, "premise: one _compile_payload"
    segment = ast.get_source_segment(src, found[0]) or ""
    return " ".join(line.strip().lstrip("#").strip()
                    for line in segment.splitlines())


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """``(line number, text)`` of each line that is not a comment."""
    return [(n, line) for n, line in
            enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if not line.strip().startswith(("//", "*", "/*"))]


def _enclosing(path: Path, n: int, definition: str) -> str:
    """The name the nearest line at or above ``n`` matching ``definition``
    (a regex with one group) defines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines[:n]):
        m = re.match(definition, line)
        if m:
            return m.group(1)
    return "?"


class TestTheCommentSaysWhenTheEditorCompiles:
    def test_it_no_longer_claims_a_compile_on_every_edit(self):
        """#356: the refusal's comment said "the canvas asks for a compile
        on every edit", and no code did that; the readouts' said "every
        compile the editor asks on every edit". Neither may say so, and the
        refusal's names the moments the canvas compiles.

        RED under mutant "the comment restored" (the refusal's comment put
        back as it was), observed:

            E   AssertionError: _compile_payload still claims a compile on every edit
            E   assert 'every edit' not in 'async def _...eadout(rig)}'
            E     'every edit' is contained here:
            E       ompile on every edit. the refusal is reported in the same list as every other loss. unmapped = [{"key": "plan", ...
        """
        text = _compile_payload_text().lower()
        assert "every edit" not in text, (
            "_compile_payload still claims a compile on every edit")
        for words in ("when a flow opens", "after each save", "done",
                      "#356"):
            assert words in text, words

    def test_the_moments_it_names_are_the_ones_the_editor_has(self):
        """The UI half of the same claim, so it cannot go stale: the store
        actions that call ``flowsCompile`` are exactly a flow opened, a
        save and ``flowsApplyFraming`` (DONE, LOOP PANELS), and outside the
        slice only the Target modal's DONE (``commit``) calls it, for a
        DONE that changed only the flow setting. A compile added to any
        other action, a param edit say, makes the comment false again.

        RED under mutant "a compile on every param edit" (``void
        get().flowsCompile();`` added to ``flowsSetParam`` in a copy of
        ``ui/src``), observed:

            E   AssertionError: assert {'flowsApplyF...lowsSetParam'} == {'flowsApplyF..., 'flowsSave'}
            E     Extra items in the left set:
            E     'flowsSetParam'
        """
        assert SLICE_TS.is_file(), f"premise: {SLICE_TS} is the slice"
        callers = {_enclosing(SLICE_TS, n, r"^    (flows\w+): ")
                   for n, line in _code_lines(SLICE_TS)
                   if "get().flowsCompile()" in line}
        assert callers == COMPILING_ACTIONS
        elsewhere: dict[str, set[str]] = {}
        for path in sorted(UI_SRC.rglob("*.ts*")):
            if "__tests__" in path.parts or path == SLICE_TS:
                continue
            for n, line in _code_lines(path):
                if "flowsCompile" in line or "compileFlow(" in line:
                    elsewhere.setdefault(path.name, set()).add(
                        _enclosing(path, n, r"^  const (\w+) = "))
        assert elsewhere == {"TargetFramingSheet.tsx": {"compileFlow",
                                                        "commit"}}
