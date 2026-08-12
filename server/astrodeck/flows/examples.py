"""The five Examples flows — read-only fixtures, transcribed from ``PRESETS``.

These are the handoff's seed library (README §2, "Seed flows (fixtures)"), and
the Definition of Done requires all five to "load, validate, and run end-to-end
on the simulator with no hardware". So they are not decoration: they are the
acceptance corpus. Node ids, positions, wiring and every parameter override come
from the prototype verbatim.

READ-ONLY, and the flag lives on the record rather than being inferred from the
folder name — renaming a folder must never quietly make a fixture writable.

They are also the doctor's own regression corpus: M31, M16, pool and NB are
graphs a careful person built, so the doctor should have little to say about
them. EAA deliberately trips two rules (no guider on 4 s subs is fine; no session
report is a note) — an example that provokes a check is how the wording gets
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
        name="M31 — LRGB two-night",
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
            {"n2": {"name": "M31 — Andromeda", "ra": "00h 42m 44s",
                    "dec": "+41° 16' 09\"", "rotation": 23.4}}))


def _m16() -> FlowRecord:
    """The full-service night — and the DoD's headline acceptance case.

    "The M16 example reproduces the full cloud-dodge choreography from real
    engine events (hold → black slot → darks → bias skip → flats-if-panel →
    clean stop → restore filter → re-center → conditional refocus → resume)."
    Every wire that choreography needs is in the edge list below."""
    return FlowRecord(
        id="example-m16", readonly=True, folder=EXAMPLES_FOLDER,
        name="M16 — full-service night",
        tagline="Dome opens at dusk, lens-cap flats in the twilight window, lights "
                "until clouds — then black slot, darks→bias→flats until quota, and "
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
            {"n2": {"name": "M16 — Eagle", "ra": "18h 18m 48s",
                    "dec": "-13° 49' 00\"", "rotation": 0},
             "n7": {"filter": "Ha", "exposure": 180, "gain": 100, "bin": "1",
                    "count": 20, "reject": 3.5, "goal": 12},
             "n10": {"sink": "ntfy", "channel": "rig-alerts", "level": "info"}}))


def _pool() -> FlowRecord:
    return FlowRecord(
        id="example-pool", readonly=True, folder=EXAMPLES_FOLDER,
        name="Best-of-four pool night",
        tagline="One lane, four candidates — the pool hands the flow whichever "
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
        name="NGC 7000 — Ha narrowband",
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
            {"n2": {"name": "NGC 7000 — North America", "ra": "20h 59m 17s",
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
            {"n2": {"name": "M27 — Dumbbell", "ra": "19h 59m 36s",
                    "dec": "+22° 43' 16\"", "rotation": 0},
             "n7": {"filter": "L", "exposure": 4, "gain": 300, "bin": "2",
                    "count": 60, "reject": 5, "goal": 0}}))


def examples() -> list[FlowRecord]:
    """Fresh copies of the five fixtures.

    Fresh, not cached: they are handed to callers that may mutate them (the
    editor loads one to "save as" into My flows), and a shared instance would
    let one session's edit leak into the next reader's Examples folder."""
    return [_m31(), _m16(), _pool(), _nb(), _eaa()]
