# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``fixtures/tonight_mosaic_2x3.json``: ``resolve_tonight``'s answer for a
night with a mosaic, which the #/next Tonight sheet's band test reads
(#353 item 7; #189 S3 item 5, spec 1.2, 2.3, 6.9).

WHY A FIXTURE BOTH SIDES READ. ``ui/src/next/hubs/session/flows/tonight/
__tests__/tonightMosaicBand.test.ts`` grades the sheet's reader and the
timeline card against the server's answer: the band is the server's worst
and best panel, drawn across the block's window. S3 embedded that answer in
the UI test as a literal, "recorded, never hand-edited", and nothing failed
when the server's answer drifted from the recording (#353 item 7): the UI
test went on grading a Tonight answer the server no longer gives. Now the
UI test READS this file, and this test keeps the file equal to what
``resolve_tonight`` answers (every string exactly, every number within one
step of its rounding: ``ABS_TOL``), as ``panelLane.test.ts`` and
``test_flows_panel_lane.py`` share ``panel_lane_cases.json``.

WHAT IS RESOLVED. S3-U2's recording, rebuilt here: DUSK -> TARGET M16 (2
rows of 3 columns, a 3x2 at 25% overlap, Rotate to PA 30, 2.0 x 1.33 deg
panels, the loop wire) -> CAPTURE Ha 300 s x 2 -> TARGET M31 (single) ->
CAPTURE -> POOL M13, M92 -> CAPTURE, at 2026-06-15 20:00 UTC with twilight
-12, a measured hop of 160 s and the progress route's answer for a session
with four of the six panels done. The site is SYNTHETIC, 40 N 105 W (the
place ``test_flows_tonight_mosaic`` uses, NOT the observatory's), handed to
the resolver as its ``site``; since #336 the panel altitudes are computed
there, and the hub is pinned to the same place so that no other path can
reach the configured site.

The file is ``{"about", "response"}``, ``response`` being the answer as JSON
renders it. To rewrite it after a deliberate change to the answer:

    cd server && ASTRODECK_REWRITE_TONIGHT_FIXTURE=1 .venv/Scripts/python.exe -m pytest -q -p no:randomly -n0 tests/test_flows_tonight_band_fixture.py

(through pytest, so the suite's config isolation holds while it runs), then
look at the diff, and re-run the UI test. Never hand-edit it.

DELIBERATE PIN CHANGE (#395, S5-TONIGHT; rewritten by the S5/S6
integration, S56-INTEG, with the command above). The brief used to name the
first capture stage only, so ``response.brief`` ended its stages at "It
captures Ha 300 s \xd7 2 (gain 100, bin 1)." Since #395 it names every capture
stage in the compile's flow order, and this graph has three (one per lane),
so the rewrite added "It then captures L 60 s \xd7 5 (gain 100, bin 1)." twice.
That one field is the whole diff; tonightMosaicBand.test.ts stays green on
it. The old fixture, run against this tree, observed:
``response.brief: '... It captures Ha 300 s \\xd7 2 (gain 100, bin 1). It then
captures L 60 s \\xd7 5 (gain 100, bin 1). It then captures L 60 s \\xd7 5
(gain 100, bin 1). If the active target ...' != '... It captures Ha 300 s
\\xd7 2 (gain 100, bin 1). If the active target ...'``. MUTANT "if cyc / elif
cap restored" (S5-TONIGHT's name: tonight.py's brief back to naming one
CYCLE or the first CAPTURE), run in the private copy scratchpad
S56-INTEG-mut: ``test_the_fixture_is_resolve_tonights_answer`` RED
(observed), the same field the other way round:
``response.brief: '... It captures Ha 300 s \\xd7 2 (gain 100, bin 1). If the
active target ...' != '... It captures Ha 300 s \\xd7 2 (gain 100, bin 1). It
then captures L 60 s \\xd7 5 (gain 100, bin 1). It then captures L 60 s \\xd7 5
(gain 100, bin 1). If the active target ...'``.

SECOND DELIBERATE PIN CHANGE (#470, the S5 integration's close of #395;
rewritten with the command above). The pin above was known wrong: read as
one chain, the three stages all followed M16's mosaic sentences, although
M16's lane is the Ha stage alone and the two L stages are M31's and the
pool's. ``tonight._stage_sentences`` now names each lane's block by the
compile's own scoping rule, so the stages read "For M16 it captures Ha 300 s
\xd7 2 (gain 100, bin 1). For M31 it captures L 60 s \xd7 5 (gain 100, bin 1).
For every member of the pool it captures L 60 s \xd7 5 (gain 100, bin 1)."
Again ``response.brief`` is the whole diff. The fixture before the rewrite,
run against the fix, observed (the field cut to its stages):
``response.brief: '... measured on this rig. For M16 it captures Ha 300 s
\\xd7 2 (gain 100, bin 1). For M31 it captures L 60 s \\xd7 5 (gain 100, bin 1).
For every member of the pool it captures L 60 s \\xd7 5 (gain 100, bin 1). If
the acti...'``. MUTANT "every stage read as one chain" (``_stage_sentences``'
``if len(lanes) <= 1:`` made ``if True:``), run in the private copy
scratchpad S5-FINAL-INTEG-mut: ``test_the_fixture_is_resolve_tonights_answer``
RED (observed), the stages back as one chain: ``'... measured on this rig.
It captures Ha 300 s \\xd7 2 (gain 100, bin 1). It then captures L 60 s \\xd7 5
(gain 100, bin 1). It then captures L 60 s \\xd7 5 (gain 100, bin 1). If the
acti...'``. The other two halves of #470 (M31's TARGET earns no arm
sentence, and the pool's select sentence comes first) are older than S5 and
still open; this pin holds them as they are.

THIRD DELIBERATE PIN CHANGE (#470 items 1 and 2, S7-TONIGHT; rewritten with
the command above). The pin above held both halves as they were: the brief
wrote one block sentence, the pool's, straight after the arming sentence,
and M31 was never named. ``tonight.brief`` now walks the blocks in flow
order (``_brief_walk``, ``compile.flow_order``), each block's sentence where
the cursor reaches it and each lane after its block, so the field reads "It
then arms M16. M16 is a 3x2 mosaic [...] For M16 it captures Ha 300 s \xd7 2
(gain 100, bin 1). It then arms M31. For M31 it captures L 60 s \xd7 5 (gain
100, bin 1). It then selects the best of M13, M92 - above 30\xb0, [...] For
every member of the pool it captures L 60 s \xd7 5 (gain 100, bin 1)." Once
more ``response.brief`` is the whole diff, and tonightMosaicBand.test.ts
stays green on it. The fixture before the rewrite, run against the fix,
observed (cut):
``response.brief: 'This flow arms at astronomical dusk (\\u221230 min). It
then arms M16. M16 is a 3x2 mosaic [...] For M16 it captures Ha 300 s \\xd7
2 (gain 100, bin 1). It then arms M31. For M31 it captures L 60 s \\xd7 5
(gain 100, bin 1). It then selects the best of M13, M92 - above 30\\xb0,
[...]' != 'This flow arms at astronomical dusk (\\u221230 min). It then
selects the best of M13, M92 - above 30\\xb0, [...] M16 is a 3x2 mosaic
[...]'``. Each mutant below ran in the private copy scratchpad
S7-TONIGHT-mut, from a byte backup restored with its sha256 checked.
MUTANT "first block only" (a block sentence for the first block the walk
reaches alone): ``test_the_fixture_is_resolve_tonights_answer`` RED
(observed), M31 and the pool unnamed again: ``response.brief: '... For M16
it captures Ha 300 s \\xd7 2 (gain 100, bin 1). For M31 it captures L 60 s
\\xd7 5 (gain 100, bin 1). For every member of the pool it captures L 60 s
\\xd7 5 (gain 100, bin 1). If the active target ...'``. MUTANT "pool
sentence first" (every POOL sorted to the front of the walk):
``test_the_fixture_is_resolve_tonights_answer`` RED (observed): ``'This
flow arms at astronomical dusk (\\u221230 min). It then selects the best of
M13, M92 - above 30\\xb0, [...] It then arms M16. M16 is a 3x2 mosaic
[...]'``. ``test_s7_tonight_brief_blocks.py`` holds the same two halves on
this graph sentence by sentence.

Every test names the mutation it guards and quotes the failure it produced,
each run in a private copy of ``server/`` (scratchpad ``s4-tonight-mut``,
from byte backups), never in the shared tree.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
from pathlib import Path

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import resolve_tonight
from astrodeck.hub import Hub
from astrodeck.sequence.session import Session, SessionFrame

FIXTURE = Path(__file__).parent / "fixtures" / "tonight_mosaic_2x3.json"
REWRITE = os.environ.get("ASTRODECK_REWRITE_TONIGHT_FIXTURE") == "1"

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
TWILIGHT = -12.0
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()
FLOW = "flow-s3u2-readouts"
FOV = (2.0, 1.33)

ABOUT = ("flows/tonight.py resolve_tonight's answer for the graph, instant, "
         "synthetic site (40 N 105 W, not the observatory's), hop and "
         "progress test_flows_tonight_band_fixture.py builds; that test "
         "keeps it equal to the answer. Read, never copied, by "
         "tonightMosaicBand.test.ts (#353 item 7). Never hand-edit it.")


@pytest.fixture(autouse=True)
def synthetic_hub(monkeypatch):
    """The hub on the synthetic site, the resolver's own, so nothing the
    answer is built from can come from the configured one."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph() -> FlowGraph:
    """dusk -> TARGET M16 (2 rows of 3, rotating) -> CAPTURE Ha 300 s x 2
    -> TARGET M31 (single) -> CAPTURE L 60 s x 5 -> POOL M13, M92 ->
    CAPTURE L 60 s x 5: S3-U2's recorded graph, node for node."""
    nodes = [
        _n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
        _n("t", "target", x=100, name="M16", ra="18h 18m 48s",
           dec="-13 49 00", rotation=30, angle="Rotate to PA", rows=2,
           cols=3, overlap=25, fovX=FOV[0], fovY=FOV[1], skip="", passes=1,
           counts="Accepted subs"),
        _n("c", "capture", x=200, filter="Ha", exposure=300, gain=100,
           bin="1", count=2, goal=0),
        _n("u", "target", x=300, name="M31", ra="00h 42m 44s",
           dec="+41 16 09", rotation=-1, counts="Accepted subs"),
        _n("k", "capture", x=400, filter="L", exposure=60, gain=100,
           bin="1", count=5, goal=0),
        _n("p", "pool", x=500, members="M13, M92", counts="Accepted subs"),
        _n("q", "capture", x=600, filter="L", exposure=60, gain=100,
           bin="1", count=5, goal=0),
    ]
    edges = [_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
             _e("c", "pass", "t", "next"),
             _e("c", "complete", "u", "arm"), _e("u", "target", "k", "run"),
             _e("k", "complete", "p", "arm"), _e("p", "target", "q", "run")]
    return FlowGraph(nodes=nodes, edges=edges)


def _progress(graph: FlowGraph) -> dict:
    """The progress route's answer for a dormant accepted-mode session of
    the flow: panels 1-1, 1-3, 2-1 and 2-2 done (2 of 2), 1-2 part-banked
    (1), 2-3 untouched; 3 frames on M31 and 2 on the pool's first member."""
    frames_on = {(0, 0): 2, (0, 1): 1, (0, 2): 2, (1, 0): 2, (1, 1): 2}
    compiled = compile_plan(graph, "n")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
    frames = []
    for t in plan.targets:
        if t.mosaic_group:
            n = frames_on.get((t.panel_row, t.panel_col), 0)
        elif t.name.startswith("M31"):
            n = 3
        elif t.name.startswith("M13"):
            n = 2
        else:
            n = 0
        frames += [SessionFrame(target_id=t.id, step_id=t.steps[0].id)
                   for _ in range(n)]
    session = Session(id="s-1", status="dormant", nights=["n1", "n2"],
                      plan=plan.model_copy(update={"count_mode": "accepted"}),
                      frames=frames, origin="flow", origin_id=FLOW)
    return flow_progress(compiled, plan, session, flow_id=FLOW)


def _answer() -> dict:
    """``resolve_tonight``'s answer, as JSON renders it."""
    graph = _graph()
    progress = _progress(graph)
    out = resolve_tonight(graph, SITE, now=JUNE, twilight_deg=TWILIGHT,
                          hop_cost_s=160.0, progress=lambda: progress)
    return json.loads(json.dumps(out))


def _write(answer: dict) -> None:
    text = json.dumps({"about": ABOUT, "response": answer}, indent=1,
                      ensure_ascii=True) + "\n"
    FIXTURE.write_bytes(text.encode("utf-8"))


def _fixture() -> dict:
    return json.loads(FIXTURE.read_bytes().decode("utf-8"))


#: How far a NUMBER in the answer may sit from the file's before the file is
#: called stale: one step of the answer's finest rounding (the curves'
#: altitudes, to 0.01 deg), and a billionth of the value, which is under 2 s
#: on a unix time. The answer is an ephemeris: astropy's AltAz frame reads
#: the IERS table it downloads (``auto_download`` is on, 30-day cache), and
#: CI installs ``astropy>=6.0`` unpinned on ubuntu-latest, so a machine with
#: another table or release can land a 2-decimal altitude one step away, and
#: a full-precision time (a meridian flip is ``now + hours x 3600``) in its
#: last digits. Exact equality would then fail on a runner for no change to
#: the code. Every drift a mutant below makes is ten times wider than this,
#: and every string (the brief, the story, a panel label) must match exactly.
ABS_TOL = 0.011
REL_TOL = 1e-9


def _drift(got, want, path: str = "response") -> list[str]:
    """Every place ``got`` differs from ``want``: a key, a length, a string,
    a flag or a null that is not the file's, or a number outside the
    tolerance above. Empty when the answer is the file's."""
    if isinstance(want, dict) and isinstance(got, dict):
        if set(got) != set(want):
            return [f"{path}: keys {sorted(got)} != {sorted(want)}"]
        return [d for k in want for d in _drift(got[k], want[k],
                                                f"{path}.{k}")]
    if isinstance(want, list) and isinstance(got, list):
        if len(got) != len(want):
            return [f"{path}: {len(got)} items != {len(want)}"]
        return [d for i, (g, w) in enumerate(zip(got, want))
                for d in _drift(g, w, f"{path}[{i}]")]
    number = (int, float)
    if (isinstance(got, number) and isinstance(want, number)
            and not isinstance(got, bool) and not isinstance(want, bool)):
        return [] if math.isclose(got, want, rel_tol=REL_TOL,
                                  abs_tol=ABS_TOL) else \
            [f"{path}: {got!r} != {want!r}"]
    return [] if got == want and type(got) is type(want) else \
        [f"{path}: {got!r} != {want!r}"]


def test_the_fixture_is_resolve_tonights_answer():
    """The file's ``response`` IS ``resolve_tonight``'s answer for the
    inputs above, every string exactly and every number within one step of
    its rounding (``ABS_TOL``, ``REL_TOL``).

    RED under mutant "server answer drifts" (``tonight._band`` names the
    highest peak as the worst and the lowest as the best: a band field
    changed), observed on the exact comparison S4-TONIGHT wrote:

        AssertionError: the fixture is not what resolve_tonight answers;
        rewrite it (see the module docstring)
        assert {'brief': 'Th...s': None, ...} == {'brief': 'Th...s': None,
        ...}
          Omitting 11 identical items, use -vv to show
          Differing items:
          {'targets': [{'coords_from': 'node', 'curve': [[1781582100.0,
          14.65], [1781582700.0, 16.25], [1781583300.0, 17.81], [1...], ...,
          'dec_deg': 43.1359, 'label': 'M92', ...}]} != {'targets': [...]}

    and on the tolerant one (S4-TONIGHT verifier, scratchpad
    ``s4-tonight-verify-mut``):

        AssertionError: the fixture is not what resolve_tonight answers;
        rewrite it (see the module docstring):
          response.targets[0].mosaic.band.worst.panel: '1-3' != '2-1'
          response.targets[0].mosaic.band.worst.row: 0 != 1
          response.targets[0].mosaic.band.worst.col: 2 != 0
          response.targets[0].mosaic.band.worst.transit_alt: 37.4 != 35.0

    RED under mutant "fixture band edited" (the file's M16 band worst
    ``transit_alt`` 35.0 made 35.1, the drift the other way round),
    observed on the tolerant comparison:

          response.targets[0].mosaic.band.worst.transit_alt: 35.0 != 35.1

    and in the test below.

    RED under mutant "rows x cols" of the brief (the S3 spelling "M16 is a
    2x3 mosaic" restored), observed on the tolerant comparison: the same
    assertion, ``response.brief`` differing.

    RE-RECORDED IN BACKLOG WP-09 (#191, 2026-09-30,
    ``ASTRODECK_REWRITE_TONIGHT_FIXTURE=1``): the flow's DUSK WINDOW picks
    "Astro dusk" (the unset-Start default), which now resolves its own Sun
    altitude (-18) instead of the rig's -12, so the night is shorter and
    every time-bearing field moves with it.
    """
    answer = _answer()
    if REWRITE:
        _write(answer)
    drift = _drift(answer, _fixture()["response"])
    assert not drift, (
        "the fixture is not what resolve_tonight answers; rewrite it (see "
        "the module docstring):\n  " + "\n  ".join(drift[:12]))


def test_control_the_tolerance_is_one_rounding_step():
    """Control on the comparison itself: an answer whose every number moved
    by less than one rounding step (each altitude 0.004 deg, each unix time
    a millisecond) is still the file's, and one whose single altitude moved
    by 0.1 deg, or whose single label changed, is not.

    Each mutant was run in the S4-TONIGHT verifier's private copy of
    ``server/`` (scratchpad ``s4-tonight-verify-mut``, from byte backups).

    RED under mutant "exact equality" (``ABS_TOL`` and ``REL_TOL`` 0, the
    comparison S4-TONIGHT wrote), at the first assertion, observed:

        AssertionError: a sub-step nudge reads as drift:
        ['response.now_unix: 1781553600.001 != 1781553600.0',
        'response.twilight_deg: -11.996 != -12.0',
        'response.night.dusk_unix: 1781581509.7513661 != 1781581509.7503662']

    RED under mutant "tolerance swallows a step" (``ABS_TOL`` 0.2), at the
    second, observed:

        AssertionError: a 0.1 deg move of one panel's peak went unseen
    """
    fx = _fixture()["response"]

    def nudge(v, small):
        if isinstance(v, dict):
            return {k: nudge(x, small) for k, x in v.items()}
        if isinstance(v, list):
            return [nudge(x, small) for x in v]
        if isinstance(v, float):
            return v + (0.001 if abs(v) > 1e8 else small)
        return v

    near = _drift(nudge(fx, 0.004), fx)
    assert near == [], f"a sub-step nudge reads as drift: {near[:3]}"
    moved = json.loads(json.dumps(fx))
    moved["targets"][0]["mosaic"]["panels"][0]["transit_alt"] += 0.1
    assert _drift(moved, fx), "a 0.1 deg move of one panel's peak went unseen"
    renamed = json.loads(json.dumps(fx))
    renamed["targets"][0]["mosaic"]["band"]["worst"]["panel"] = "2-2"
    assert _drift(renamed, fx), "a changed panel label went unseen"


def test_the_file_holds_what_the_ui_test_reads():
    """The premises ``tonightMosaicBand.test.ts`` states about the file, held
    here so a rewrite that moves one fails on this side first: M16 is the
    first row and a 3x2 whose band runs from 2-1 at 35.0 to 1-3 at 37.4,
    with 1-1 at 35.8 between them (so a band read off one panel cannot
    pass for the spread); M31 and the two pool members follow, each with a
    window and no mosaic key. The file is UTF-8 with no BOM. (That it was
    resolved for a synthetic site is the module docstring's premise, which
    ``_answer`` keeps; nothing in the file can show it.)

    ITS LINE ENDS ARE NOT GRADED. This repository runs with
    ``core.autocrlf=true`` and no ``.gitattributes`` line for this file, so
    a fresh Windows clone or worktree checks it out with CRLF, and both
    readers parse it the same either way. This test first also asserted
    ``b"\\r\\n" not in raw``, which went red on exactly that checkout for no
    change to the answer (S4-TONIGHT re-verifier, scratchpad
    ``s4-tonight-reverify-mut``, the file rewritten CRLF: this test red,
    the other two here and ``tonightMosaicBand.test.ts`` 7/7 green). That
    CRLF file is now the control: all three here green. A BOM is still
    refused, because ``json.loads`` and the UI's ``JSON.parse`` refuse one.
    RED under mutant "fixture with a BOM" (U+FEFF put before the file's
    first byte), in the same scratchpad, observed:

        AssertionError: the fixture carries a BOM

    (and the other two here, on "json.decoder.JSONDecodeError: Unexpected
    UTF-8 BOM (decode using utf-8-sig)").

    RED under mutant "fixture band edited", observed:

        AssertionError: assert {'best': {'co...t_alt': 35.1}} == {'best':
        {'co...t_alt': 35.0}}
          Differing items:
          {'worst': {'col': 0, 'panel': '2-1', 'row': 1, 'transit_alt':
          35.1}} != {'worst': {'col': 0, 'panel': '2-1', 'row': 1,
          'transit_alt': 35.0}}
    """
    raw = FIXTURE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the fixture carries a BOM"
    fx = _fixture()
    assert set(fx) == {"about", "response"}
    targets = fx["response"]["targets"]
    mosaic = targets[0]["mosaic"]
    assert (targets[0]["label"], mosaic["rows"], mosaic["cols"]) == (
        "M16", 2, 3)
    assert mosaic["band"] == {
        "worst": {"panel": "2-1", "row": 1, "col": 0, "transit_alt": 35.0},
        "best": {"panel": "1-3", "row": 0, "col": 2, "transit_alt": 37.4}}
    first = mosaic["panels"][0]
    assert first["panel"] == "1-1" and 35.0 < first["transit_alt"] < 37.4
    assert [t["label"] for t in targets[1:]] == ["M31", "M13", "M92"]
    assert all(t["window"] and "mosaic" not in t for t in targets[1:])
    assert "M16 is a 3x2 mosaic of 6 panels" in fx["response"]["brief"]
