"""A setting the compile drops must SAY so — `reject` did not (2026-08-17).

`to_plan.losses()` exists so an operator "finds out that their cloud rule is not
running now, from a list on screen, instead of at 3 a.m." (to_plan.py's own
words). It reported nine node params for the NGC 7129 flow built that day and
missed the tenth, because `inert_nodes` skips every type in
`COMPILED_NODE_TYPES` wholesale — reasoning that a compiled node's params
arrive. For CAPTURE and FILTER CYCLE's `reject` that was false.

WORSE THAN SILENT: `tonight.py`'s brief — the plain-English page an operator
reads before pressing Run — asserted the dropped value by name and by number:

    "...so every channel grows evenly; a sub is graded and only counts below
     HFR 3.5″."

Nothing grades on 3.5. The clause was unconditional, so setting `reject` to 0
would have printed "below HFR 0″" rather than removing the claim.

AND IT IS NOT WIRED UP INSTEAD, deliberately. The nearest plan field,
``hfr_reject_factor``, is a MULTIPLIER of the running median of accepted frames
(engine.py: ``hfr > med * factor``, needing four frames first). The node's
`reject` is presented everywhere as an absolute HFR in arcsec. Feeding 3.5 into
a field meaning "3.5x the median" is a different rule wearing the same number.
"""
import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import default_params
from astrodeck.flows.to_plan import INERT_PARAMS, losses, to_sequence_plan
from astrodeck.flows.tonight import brief


def _graph(capture_type: str, reject) -> FlowGraph:
    params = {**default_params(capture_type), "reject": reject}
    return FlowGraph(
        nodes=[
            FlowNode(id="t", type="target", x=0, y=0,
                     params={**default_params("target"), "name": "NGC 7129",
                             "ra": "21h 42m 59s", "dec": "+66° 06′ 47″"}),
            FlowNode(id="c", type=capture_type, x=300, y=0, params=params),
        ],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"})])


def _loss_keys(g):
    _plan, unmapped = to_sequence_plan(compile_plan(g), g)
    return {l["key"] for l in losses(unmapped)}


@pytest.mark.parametrize("ntype", ["cycle", "capture"])
def test_a_reject_the_run_ignores_is_reported(ntype):
    """THE FIX. Both stages carry a `reject` and neither reaches the engine."""
    keys = _loss_keys(_graph(ntype, 3.5))
    assert f"nodes.{ntype}.reject" in keys, (
        f"the {ntype.upper()} node's HFR threshold is dropped in silence — the "
        f"one list whose entire job is that dropped settings are not silent")


@pytest.mark.parametrize("ntype", ["cycle", "capture"])
def test_a_setting_nobody_set_earns_no_warning(ntype):
    """A list that fires on every flow is a list nobody reads. Only a `reject`
    the operator actually set is worth a line."""
    for off in (0, 0.0, None, ""):
        assert f"nodes.{ntype}.reject" not in _loss_keys(_graph(ntype, off)), off


@pytest.mark.parametrize("ntype", ["cycle", "capture"])
def test_the_number_never_reaches_the_plan(ntype):
    """The claim the loss line makes, checked rather than asserted. If someone
    later wires `reject` through, this test fails and the loss line must go."""
    g = _graph(ntype, 3.5)
    plan, _ = to_sequence_plan(compile_plan(g), g)
    assert plan.hfr_reject_factor is None, (
        "`reject` now reaches the plan — but as a MULTIPLE of the median, which "
        "is not what the node's number means. Check the units before removing "
        "the loss line")
    for step in plan.targets[0].steps:
        assert not getattr(step, "reject_hfr", None)


@pytest.mark.parametrize("ntype", ["cycle", "capture"])
def test_the_brief_no_longer_promises_a_grade_that_does_not_happen(ntype):
    """The sentence an operator reads before pressing Run. It named the
    threshold in arcsec, unconditionally, for a rule nothing implements."""
    text = brief(_graph(ntype, 3.5))
    assert text, "no brief at all"
    assert "3.5" not in text, text
    assert "graded" not in text and "grading" not in text, text
    # ...and it still says what the stage DOES do, or the removal went too far.
    assert "captures" in text or "interleaves" in text, text


def test_the_inert_param_table_covers_every_node_that_has_one():
    """`reject` exists on exactly the two capture-ish nodes, and both are in
    COMPILED_NODE_TYPES — i.e. both are invisible to the whole-node loop. A
    third capture stage added later must not inherit the silence."""
    from astrodeck.flows.nodes import NODE_DEFS
    from astrodeck.flows.to_plan import COMPILED_NODE_TYPES
    have = {t for t, d in NODE_DEFS.items() if "reject" in (d.params or {})}
    covered = {t for (t, p) in INERT_PARAMS if p == "reject"}
    assert have <= covered, f"{have - covered} carry a reject nothing reports"
    assert have <= COMPILED_NODE_TYPES, (
        "a reject-bearing node left COMPILED_NODE_TYPES — the whole-node loop "
        "now reports it and INERT_PARAMS would double up")
