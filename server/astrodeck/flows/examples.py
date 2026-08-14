"""The seven Examples flows - read-only fixtures, transcribed from ``PRESETS``.

These are the handoff's seed library (README §2, "Seed flows (fixtures)"), and
the Definition of Done requires all of them to "load, validate, and run
end-to-end on the simulator with no hardware". So they are not decoration: they
are the acceptance corpus. Node ids, positions, wiring and every parameter
override come from the prototype verbatim.

NO EM-DASHES. Every string in this file is shipped: a flow name and a tagline are
both printed on a library card. The 2026-08-14 do-not list bans them outright and
the export writes each of these with a hyphen.

READ-ONLY, and the flag lives on the record rather than being inferred from the
folder name - renaming a folder must never quietly make a fixture writable.

They are also the doctor's own regression corpus: M31, M16, pool and NB are
graphs a careful person built, so the doctor should have little to say about
them. EAA deliberately trips two rules (no guider on 4 s subs is fine; no session
report is a note) - an example that provokes a check is how the wording gets
read by a human before it matters at 03:00.
"""
from __future__ import annotations

from .models import EXAMPLES_FOLDER, FlowEdge, FlowGraph, FlowNode, FlowRecord
from .nodes import default_params


def _graph(nodes: list, edges: list, over: dict | None = None) -> FlowGraph:
    over = over or {}
    return FlowGraph(
        nodes=[FlowNode(id=nid, type=ntype, x=float(x), y=float(y),
                        params={**default_params(ntype), **over.get(nid, {})})
               for nid, ntype, x, y in nodes],
        edges=[FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})
               for a, ap, b, bp in edges])


def _m31() -> FlowRecord:
    return FlowRecord(
        id="example-m31", readonly=True, folder=EXAMPLES_FOLDER,
        name="M31 - LRGB two-night",
        tagline="Dusk-gated deep-sky run: center, focus, guide, 24×120s L with an "
                "HFR watchdog that refocuses and notifies.",
        graph=_graph(
            [("n1", "dusk", 30, 70), ("n2", "target", 30, 230), ("n3", "safety", 30, 420),
             ("n4", "slew", 280, 180), ("n5", "autofocus", 510, 90), ("n6", "guide", 510, 260),
             ("n7", "capture", 745, 160), ("n8", "condition", 745, 400),
             ("n9", "refocus", 995, 380), ("n10", "notify", 995, 520),
             ("n11", "abort", 280, 430), ("n12", "report", 995, 140)],
            [("n1", "window", "n2", "arm"), ("n2", "target", "n4", "run"),
             ("n4", "centered", "n5", "run"), ("n5", "focused", "n6", "run"),
             ("n6", "guiding", "n7", "run"), ("n7", "complete", "n12", "session"),
             ("n7", "frame", "n8", "events"), ("n8", "fire", "n9", "do"),
             ("n8", "fire", "n10", "do"), ("n3", "unsafe", "n11", "do")],
            {"n2": {"name": "M31 - Andromeda", "ra": "00h 42m 44s",
                    "dec": "+41° 16' 09\"", "rotation": 23.4}}))


def _m16() -> FlowRecord:
    """The full-service night - and the DoD's headline acceptance case.

    "The M16 example reproduces the full cloud-dodge choreography from real
    engine events (hold → black slot → darks → bias skip → flats-if-panel →
    clean stop → restore filter → re-center → conditional refocus → resume)."
    Every wire that choreography needs is in the edge list below."""
    return FlowRecord(
        id="example-m16", readonly=True, folder=EXAMPLES_FOLDER,
        name="M16 - full-service night",
        tagline="Dome opens at dusk, lens-cap flats in the twilight window, lights "
                "until clouds - then black slot, darks→bias→flats until quota, and "
                "a clean resume (filter back, re-center, refocus if drifted).",
        graph=_graph(
            [("n1", "dusk", 30, 50), ("n16", "dome", 260, 50), ("n17", "duskflats", 490, 50),
             ("n2", "target", 720, 50), ("n4", "slew", 950, 50),
             ("n5", "autofocus", 160, 320), ("n6", "guide", 390, 320),
             ("n7", "capture", 620, 320), ("n12", "report", 870, 320),
             ("n13", "cloudwatch", 30, 560), ("n14", "holdresume", 330, 590),
             ("n15", "calib", 620, 540), ("n18", "flatpanel", 330, 780),
             ("n10", "notify", 870, 620), ("n3", "safety", 30, 860),
             ("n19", "abort", 620, 880)],
            [("n1", "window", "n16", "run"), ("n16", "open", "n17", "run"),
             ("n17", "done", "n2", "arm"), ("n2", "target", "n4", "run"),
             ("n4", "centered", "n5", "run"), ("n5", "focused", "n6", "run"),
             ("n6", "guiding", "n7", "run"), ("n7", "complete", "n12", "session"),
             ("n13", "in", "n14", "pause"), ("n13", "in", "n15", "do"),
             ("n13", "in", "n10", "do"), ("n13", "clear", "n14", "resume"),
             ("n13", "clear", "n15", "stop"), ("n18", "ready", "n15", "panel"),
             ("n3", "unsafe", "n19", "do")],
            {"n2": {"name": "M16 - Eagle", "ra": "18h 18m 48s",
                    "dec": "-13° 49' 00\"", "rotation": 0},
             "n7": {"filter": "Ha", "exposure": 180, "gain": 100, "bin": "1",
                    "count": 20, "reject": 3.5, "goal": 12},
             "n10": {"sink": "ntfy", "channel": "rig-alerts", "level": "info"},
             # Scoped off clouds because this graph HAS a CLOUD WATCH. Left
             # standalone, safety would abort the night the hold exists to ride
             # out — the cloud-dodge choreography this example is the acceptance
             # case for would never get to run (doctor rule 13).
             "n3": {"watch": "Rain + wind + power (pair with Cloud Watch)"}}))


def _pool() -> FlowRecord:
    return FlowRecord(
        id="example-pool", readonly=True, folder=EXAMPLES_FOLDER,
        name="Best-of-four pool night",
        tagline="One lane, four candidates - the pool hands the flow whichever "
                "target scores best on altitude × moon separation, and re-evaluates "
                "when one completes.",
        graph=_graph(
            [("n1", "dusk", 30, 80), ("n20", "pool", 260, 80), ("n4", "slew", 500, 80),
             ("n5", "autofocus", 730, 80), ("n6", "guide", 960, 80),
             ("n7", "capture", 500, 330), ("n12", "report", 960, 350),
             ("n8", "condition", 500, 560), ("n9", "refocus", 750, 580)],
            [("n1", "window", "n20", "arm"), ("n20", "target", "n4", "run"),
             ("n4", "centered", "n5", "run"), ("n5", "focused", "n6", "run"),
             ("n6", "guiding", "n7", "run"), ("n7", "complete", "n12", "session"),
             ("n7", "frame", "n8", "events"), ("n8", "fire", "n9", "do")],
            {"n7": {"filter": "Ha", "exposure": 180, "gain": 100, "bin": "1",
                    "count": 20, "reject": 3.5, "goal": 12}}))


def _nb() -> FlowRecord:
    return FlowRecord(
        id="example-nb", readonly=True, folder=EXAMPLES_FOLDER,
        name="NGC 7000 - Ha narrowband",
        tagline="Moon-tolerant Ha: longer subs, guide-RMS rule pauses for wind "
                "gusts instead of wasting 300s frames.",
        graph=_graph(
            [("n1", "dusk", 30, 90), ("n2", "target", 30, 260), ("n4", "slew", 280, 170),
             ("n5", "autofocus", 520, 100), ("n6", "guide", 520, 270),
             ("n7", "capture", 760, 170), ("n8", "condition", 760, 400),
             ("n10", "notify", 1000, 400), ("n12", "report", 1000, 150)],
            [("n1", "window", "n2", "arm"), ("n2", "target", "n4", "run"),
             ("n4", "centered", "n5", "run"), ("n5", "focused", "n6", "run"),
             ("n6", "guiding", "n7", "run"), ("n7", "complete", "n12", "session"),
             ("n7", "frame", "n8", "events"), ("n8", "fire", "n10", "do")],
            {"n2": {"name": "NGC 7000 - North America", "ra": "20h 59m 17s",
                    "dec": "+44° 31' 44\"", "rotation": 0},
             "n7": {"filter": "Ha", "exposure": 300, "gain": 100, "bin": "1",
                    "count": 12, "reject": 3.5},
             "n8": {"when": "Guide RMS above", "threshold": 1.8,
                    "window": "3 frames", "once": "Every time"}}))


def _eaa() -> FlowRecord:
    return FlowRecord(
        id="example-eaa", readonly=True, folder=EXAMPLES_FOLDER,
        name="EAA quick look",
        tagline="No guiding, no rules: slew, focus, short subs for a live view of "
                "tonight's picks.",
        last_result="",
        graph=_graph(
            [("n2", "target", 30, 160), ("n4", "slew", 280, 140),
             ("n5", "autofocus", 520, 120), ("n7", "capture", 760, 140),
             ("n12", "report", 1000, 160)],
            [("n2", "target", "n4", "run"), ("n4", "centered", "n5", "run"),
             ("n5", "focused", "n7", "run"), ("n7", "complete", "n12", "session")],
            {"n2": {"name": "M27 - Dumbbell", "ra": "19h 59m 36s",
                    "dec": "+22° 43' 16\"", "rotation": 0},
             "n7": {"filter": "L", "exposure": 4, "gain": 300, "bin": "2",
                    "count": 60, "reject": 5, "goal": 0}}))


def _campaign() -> FlowRecord:
    """The month-scale campaign, and the only fixture that survives its own dawn.

    Everything unusual about it is one idea: the run does not end at sunrise, it
    HOLDS. Three wires carry that, and none of them is in any single-night graph:

    * ``n12 done -> n20 advance`` — the loop. SESSION REPORT fires when the
      active target's quota is met; the pool marks it done in the ledger and
      re-scores the remaining members. It points BACKWARDS and that is legal,
      because it is an event wire and the flow lane stays acyclic.
    * ``n1 nightend -> n21 do`` — the shutdown lane. PARK + CLOSE is not an
      abort; it parks, closes, and leaves the cursor where it was.
    * ``n21 closed -> n15 do`` — the day shift. The closure is shut and the
      cooler is held cold, so the calibration queue can spend daylight on darks
      that actually match the night's frames.
    """
    return FlowRecord(
        id="example-campaign", readonly=True, folder=EXAMPLES_FOLDER,
        name="Campaign - best of 4, month-scale",
        tagline="The pool hands out targets until every quota is met; 'target "
                "done' loops back to advance it. Dawn parks + closes with the "
                "cooler held cold for day darks, and each dusk resumes "
                "mid-cycle from the ledger.",
        graph=_graph(
            [("n1", "dusk", 30, 60), ("n16", "dome", 270, 60),
             ("n20", "pool", 510, 40), ("n4", "slew", 770, 60),
             ("n5", "autofocus", 1010, 60), ("n6", "guide", 160, 340),
             ("n7", "cycle", 410, 340), ("n12", "report", 680, 330),
             ("n13", "cloudwatch", 30, 590), ("n14", "holdresume", 310, 630),
             ("n15", "calib", 580, 610), ("n18", "flatpanel", 30, 810),
             ("n21", "parkclose", 950, 570), ("n10", "notify", 950, 770),
             ("n3", "safety", 580, 850), ("n19", "abort", 830, 900)],
            [("n1", "window", "n16", "run"), ("n16", "open", "n20", "arm"),
             ("n20", "target", "n4", "run"), ("n4", "centered", "n5", "run"),
             ("n5", "focused", "n6", "run"), ("n6", "guiding", "n7", "run"),
             ("n7", "complete", "n12", "session"),
             ("n12", "done", "n20", "advance"),
             ("n13", "in", "n14", "pause"), ("n13", "in", "n15", "do"),
             ("n13", "in", "n10", "do"), ("n13", "clear", "n14", "resume"),
             ("n13", "clear", "n15", "stop"), ("n18", "ready", "n15", "panel"),
             ("n1", "nightend", "n21", "do"), ("n21", "closed", "n15", "do"),
             ("n3", "unsafe", "n19", "do"), ("n20", "floor", "n10", "do")],
            {"n1": {"repeat": "Nightly until pool complete"},
             "n20": {"members": "M33, NGC 7331, IC 1396, M45", "quota": 45},
             "n3": {"watch": "Rain + wind + power (pair with Cloud Watch)"}}))


def _cycle() -> FlowRecord:
    """One sub per filter per pass, forty-five passes.

    The point of interleaving is what a night cut short leaves behind. Forty-five
    L and then clouds is a mono image; one of each forty-five times is an image
    at every prefix, because all seven channels grow together.
    """
    return FlowRecord(
        id="example-cycle", readonly=True, folder=EXAMPLES_FOLDER,
        name="M33 - LRGBSHO cycle ×45",
        tagline="One sub per filter per pass - 60s LRGB, 180s SHO - looped 45 "
                "times so every channel grows evenly, with refocus watchdog and "
                "cloud-dodge wired to the loop.",
        graph=_graph(
            [("n1", "dusk", 30, 60), ("n2", "target", 30, 230),
             ("n4", "slew", 270, 140), ("n5", "autofocus", 510, 60),
             ("n6", "guide", 510, 240), ("n7", "cycle", 750, 140),
             ("n12", "report", 1000, 120), ("n8", "condition", 750, 420),
             ("n9", "refocus", 1000, 400), ("n13", "cloudwatch", 30, 500),
             ("n14", "holdresume", 300, 560), ("n15", "calib", 540, 580),
             ("n18", "flatpanel", 300, 760), ("n10", "notify", 1000, 580)],
            [("n1", "window", "n2", "arm"), ("n2", "target", "n4", "run"),
             ("n4", "centered", "n5", "run"), ("n5", "focused", "n6", "run"),
             ("n6", "guiding", "n7", "run"), ("n7", "complete", "n12", "session"),
             ("n7", "frame", "n8", "events"), ("n8", "fire", "n9", "do"),
             ("n13", "in", "n14", "pause"), ("n13", "in", "n15", "do"),
             ("n13", "in", "n10", "do"), ("n13", "clear", "n14", "resume"),
             ("n13", "clear", "n15", "stop"), ("n18", "ready", "n15", "panel")],
            {"n2": {"name": "M33 - Triangulum", "ra": "01h 33m 51s",
                    "dec": "+30° 39' 37\"", "rotation": 0}}))


def examples() -> list[FlowRecord]:
    """Fresh copies of the seven fixtures, in the README's listed order.

    Fresh, not cached: they are handed to callers that may mutate them (the
    editor loads one to "save as" into My flows), and a shared instance would
    let one session's edit leak into the next reader's Examples folder."""
    return [_campaign(), _m31(), _m16(), _cycle(), _pool(), _nb(), _eaa()]
