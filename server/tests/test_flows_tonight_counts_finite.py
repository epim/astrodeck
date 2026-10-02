# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight's CAMPAIGN tab reads a stored count that is no count without
raising, and says so in words (#362 item 4, the #328 class).

``tonight._campaign`` read a POOL's quota as ``max(1, int(_num(quota,
45)))`` and a FILTER CYCLE's ``perCycle`` the same way. ``_num`` hands back
an infinity for the text ``"inf"`` and for a JSON ``1e999`` (Python's parser
reads that as ``float("inf")``), and ``int()`` of an infinity raises
``OverflowError: cannot convert float infinity to integer``; a 400-digit JSON
integer raises ``OverflowError: int too large to convert to float`` from
``_num``'s ``float()`` before ``int()`` is reached. Either escaped
``_campaign`` and took the whole Tonight answer down with it.

Validation refuses those values at the save since #328
(``models.COUNT_PARAMS``, ``COUNT_REFUSAL``), so a new save cannot store one.
A flow saved before that, or a file edited by hand, still holds one, and
Tonight reads stored flows. So the tab now judges a count by validation's
own predicate (``models._not_a_count``): a value validation would refuse is
refused here too, in a sentence in the note, every member reads "not
counted" (``banked`` None, never 0), and ``quota`` is None when the quota is
the one refused. There is no count to clamp to: an infinite quota is no
number of cycles, and the default 45 would draw "12/45 cycles" against a
quota nobody set. Text that is no number at all is read as the default, as
it always was, because validation does not judge it either.

Every mutant below was run in a private copy of ``server/`` (scratchpad
``S5-TONIGHT-mut``, from byte backups), never in the shared tree.
"""
from __future__ import annotations

import json

import pytest

from astrodeck.flows.examples import examples
from astrodeck.flows.models import COUNT_PARAMS, FlowGraph, _not_a_count
from astrodeck.flows.tonight import _campaign

#: The three stored shapes #362 names, each as ``json.loads`` gives it back
#: from a flow file: the text, the float a JSON ``1e999`` parses to, and a
#: 400-digit integer. ``(value, how the note quotes it)``.
STORED = {
    "the text inf": ("inf", "'inf'"),
    "a JSON 1e999": (json.loads("1e999"), "inf"),
    "a 400-digit integer": (json.loads("1" + "0" * 399),
                            "a number of more than 15 digits"),
}

#: The node each count lives on, in the Campaign Example (a POOL of four
#: with a DUSK that repeats, and one FILTER CYCLE of seven filters).
WHERE = {"quota": ("pool", "TARGET POOL"), "perCycle": ("cycle",
                                                        "FILTER CYCLE")}

#: A ledger that has every filter at 20 but Ha at 12: M33 has banked 12
#: complete cycles when the counts are read (the control below).
LEDGER = {"M33": {"L": 20, "R": 20, "G": 20, "B": 20, "Ha": 12, "OIII": 20,
                  "SII": 20}}


def _campaign_graph(key: str | None = None, value=None) -> FlowGraph:
    """The Campaign Example, with ``key`` set to ``value`` on its node,
    through ``model_validate`` of the dumped graph as a stored file is
    read."""
    ex = next(e for e in examples() if e.id == "example-campaign")
    raw = ex.graph.model_dump(by_alias=True)
    if key is not None:
        ntype = WHERE[key][0]
        (node,) = [n for n in raw["nodes"] if n["type"] == ntype]
        node["params"][key] = value
    return FlowGraph.model_validate(raw)


def _node_id(graph: FlowGraph, key: str) -> str:
    (node,) = [n for n in graph.nodes if n.type == WHERE[key][0]]
    return node.id


class TestAStoredCountThatIsNoCount:
    @pytest.mark.parametrize("key", ["quota", "perCycle"])
    @pytest.mark.parametrize("shape", list(STORED))
    def test_is_refused_in_words_and_raises_nothing(self, shape, key):
        """``"inf"``, a JSON ``1e999`` and a 400-digit integer, as the
        POOL's quota and as the FILTER CYCLE's ``perCycle``, with a ledger
        to count from: the tab answers, no member is counted, the note says
        which count and why, and the answer is JSON the route can send
        (``allow_nan=False``, as Starlette renders it).

        RED under mutant "int() of the raw count" (the two reads restored to
        ``max(1, int(_num(pool.params.get("quota"), 45)))`` and ``max(1,
        int(_num(cyc.params.get("perCycle"), 1)))``, the refusal gone),
        observed, all six cases raising out of ``_campaign`` (27 failed, 13
        passed in this file before the quoting cases below were added;
        ``test_flows_campaign_brief`` stayed green):

            E       OverflowError: cannot convert float infinity to integer
            (four times: the text inf and a JSON 1e999, as quota and as
            perCycle)
            E           OverflowError: int too large to convert to float
            (twice: a 400-digit integer, as quota and as perCycle)

        RED under mutant "the default for no count" (a refused value read
        as ``default`` with no sentence, the clamp this file argues
        against), observed, all six cases (27 failed, 13 passed, before the
        quoting cases were added):

            E       AssertionError: the TARGET POOL 'n20' holds 'inf' as its
            quota: [{'name': 'M33', 'banked': 12, 'quota': 45, 'done':
            False, 'pct': 27}, {'name': 'NGC 7331', 'banked': 0, 'quota':
            45, 'done': False, 'pct': 0}, ...
            E       AssertionError: the FILTER CYCLE 'n7' holds a number of
            more than 15 digits as its perCycle: [{'name': 'M33', 'banked':
            12, 'quota': 45, ...
        """
        value, shown = STORED[shape]
        graph = _campaign_graph(key, value)
        assert _not_a_count(graph.node(_node_id(graph, key)).params[key]), \
            "premise: validation refuses this value at the save"
        assert key in COUNT_PARAMS[WHERE[key][0]], \
            "premise: validation judges this param"
        c = _campaign(graph, lambda: LEDGER)
        label = WHERE[key][1]
        where = f"{label} {_node_id(graph, key)!r} holds {shown} as its {key}"
        assert all(m["banked"] is None and m["pct"] is None
                   and m["done"] is False for m in c["members"]), (
            f"the {where}: {c['members']}")
        assert (f"{where}, and a count must be a finite number above 0, so "
                f"no member's cycles are counted against it.") in c["note"], \
            c["note"]
        assert c["quota"] == (None if key == "quota" else 45), c["quota"]
        assert all(m["quota"] == c["quota"] for m in c["members"])
        json.dumps(c, allow_nan=False)

    @pytest.mark.parametrize("value, shown", [
        ("1" * 400, "'1111111111111111111...'"), (0, "0"), (-3, "-3"),
        ("-2.5", "'-2.5'"), (float("nan"), "nan")],
        ids=["400 digits of text", "0", "-3", "the text -2.5", "a NaN"])
    def test_the_note_quotes_the_value_short(self, value, shown):
        """The note quotes the stored value so the operator can find it:
        text in quotes, a number as written, and anything too long to read
        cut short (text) or described (a 400-digit integer, above), since
        400 digits printed in full would be most of the note.

        RED under mutant "the whole value quoted" (``_shown`` returning
        ``repr(value)`` for everything), observed, in the long text here and
        the 400-digit integer above, as quota and as perCycle (3 failed, 42
        passed; the short values here read the same either way):

            E       AssertionError: TARGET POOL 'n20' holds '11111111111111
            1111111111... (the 402 characters of the repr, in full)
            E       AssertionError: TARGET POOL 'n20' holds 10000000000000
            0000000000... (all 400 digits)
            E       AssertionError: FILTER CYCLE 'n7' holds 10000000000000
            0000000000... (all 400 digits)
        """
        graph = _campaign_graph("quota", value)
        note = _campaign(graph, lambda: LEDGER)["note"]
        assert (f"TARGET POOL {_node_id(graph, 'quota')!r} holds {shown} as "
                f"its quota, and a count must be") in note, note

    def test_both_refused_say_both(self):
        """Both counts refused: two sentences, the quota's first, and one
        answer.

        RED under mutant "int() of the raw count", observed:

            E       OverflowError: cannot convert float infinity to integer

        RED under mutant "the default for no count", observed:

            E       AssertionError: 168 cycles left across the pool (1176
            subs). Nights to finish are not forecast - ...
            E       assert 0 <= -1
        """
        graph = _campaign_graph("quota", "inf")
        (cyc,) = [n for n in graph.nodes if n.type == "cycle"]
        cyc.params["perCycle"] = json.loads("1e999")
        note = _campaign(graph, lambda: LEDGER)["note"]
        quota = note.find("as its quota,")
        per = note.find("as its perCycle,")
        assert 0 <= quota < per, note


class TestTheTabRefusesWhatASaveRefuses:
    @pytest.mark.parametrize("value", [
        "inf", "-inf", "nan", "1e999", json.loads("1e999"),
        json.loads("1" + "0" * 399), 0, -3, "0", "-2.5",
        45, "45", 2.5, "abc", "", None],
        ids=lambda v: (f"int of {len(str(v))} digits" if isinstance(v, int)
                       and abs(v) > 10 ** 15 else f"{type(v).__name__} {v!r}"))
    @pytest.mark.parametrize("key", ["quota", "perCycle"])
    def test_one_predicate(self, key, value):
        """For every value, the CAMPAIGN tab refuses the count exactly when
        the graph's validation lists it as no count (``COUNT_REFUSAL``), so
        a flow the tab refuses is one a save refuses too, and the reverse.

        RED under mutant "only an infinity or a NaN refused" (the
        predicate ``not math.isfinite(float(value))``, 0 and negatives read
        as before, ``max(1, ...)``), observed, for 0, -3, "0" and "-2.5",
        as quota and as perCycle (8 failed, 32 passed), the first:

            E       AssertionError: validation refuses 0 as the quota: True;
            the tab refuses it: False
            E       assert False == True

        RED under mutant "the default for no count", observed, every value
        validation refuses, ten for each count (20 cases), the first:

            E       AssertionError: validation refuses 'inf' as the quota:
            True; the tab refuses it: False

        and under "int() of the raw count" the same four as "only an
        infinity or a NaN refused" plus an ``OverflowError`` or, for
        ``"nan"``, ``ValueError: cannot convert float NaN to integer`` from
        each of the rest.
        """
        graph = _campaign_graph(key, value)
        saved = any(f"{key!r} is" in e and "a count must be" in e
                    for e in graph.validation_errors())
        note = _campaign(graph, lambda: LEDGER)["note"]
        refused = f"as its {key}, and a count must be" in note
        assert refused == saved, (
            f"validation refuses {value!r} as the {key}: {saved}; the tab "
            f"refuses it: {refused}")


class TestControls:
    def test_a_count_that_is_a_count_reads_as_before(self):
        """Controls, green on the code and under every mutant above: the
        Campaign Example as shipped (quota 45, one sub a pass) counts M33's
        12 complete cycles against 45 and states the work left; a quota
        typed as text ``"30"`` is 30; and text that is no number is the
        default 45, unrefused, as before (validation does not judge it)."""
        c = _campaign(_campaign_graph(), lambda: LEDGER)
        m33 = c["members"][0]
        assert (c["quota"], m33["banked"], m33["pct"]) == (45, 12, 27), c
        assert "cycles left across the pool" in c["note"], c["note"]
        assert "a count must be" not in c["note"], c["note"]
        assert _campaign(_campaign_graph("quota", "30"),
                         lambda: LEDGER)["quota"] == 30
        text = _campaign(_campaign_graph("quota", "abc"), lambda: LEDGER)
        assert text["quota"] == 45 and "a count must be" not in text["note"]
