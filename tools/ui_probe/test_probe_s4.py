"""Self-tests for the S4 frame probe (#189 S4, routes_s4_frame.json). Run with
the probe's Playwright Python, like test_probe_isolation.py:

    cd tools/ui_probe && python -m unittest test_probe_s4

No AstroDeck server, no UI build and no rig: every page is a loopback fixture
that stands in for the flows screen and the Target modal, walked with the REAL
route file. The fixture's GOOD page satisfies every check the route makes, so
each negative page below differs from it in ONE thing and has to fail for that
one thing. A blank page, and a page with everything but the view marker, must
not pass: that is the probe's whole point (verify-on-the-real-thing).

Every case names the mutation of probe.py, the route file or framing.css that
turns it red, with the failure observed when that mutation was run in a
private copy (scratchpad s4-uprobe-mut, 2026-09-26). The resumed run of
2026-09-28 (scratchpad s4-uprobe-r2-mut) ran "marker assertion removed" again
on the current probe, and it failed as recorded below in both marker cases;
WaitingCardTest's and the waiting walks' inset mutants were run there too.

FramingCssTest grades the three framing.css rules the real-page probe found
broken on 2026-09-26, against the real file in a browser, so they are held
by a test that needs no server.

Mosaic slice H4 (#535, #495, #440) added the sky's layout to the fixture (the
square's slot, the compass letters and the degraded line under the canvas,
as framing.css lays them out), the cases for `contained` and `apart` on the
phone walk and on their own (ContainedCheckTest), FramingSkyCssTest (the real
framing.css holding the sky whole with both lines under the canvas), and
takes every browser from `probe._Browsers`, so the desktop walk here draws
its scrollbars as the real one does. Those mutants ran in a private mirror
(scratchpad H4-PROBE-LAYOUT-mut, 2026-09-29; mutate.py and every verbatim
output are kept there).
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import copy
import json
import re
import tempfile
import threading
import unittest

# Importable from anywhere, and a loud SKIP where Playwright is missing: the
# same arrangement as test_probe_isolation.py (issue #119).
import sys as _sys

HERE = Path(__file__).resolve().parent
_sys.path.insert(0, str(HERE))

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:      # pragma: no cover - environment, not logic
    raise unittest.SkipTest(
        f"Playwright is not importable here ({exc}). This test needs the system "
        f"python, per tools/ui_probe/README.md; run it with "
        f"`cd tools/ui_probe && python -m unittest test_probe_s4`.")

import probe  # noqa: E402

ROUTES_PATH = HERE / "routes_s4_frame.json"
FRAMING_CSS = HERE.parents[1] / "ui" / "src" / "components" / "flows" / "framing" / "framing.css"
FLOW_FRAME_SHEET = (HERE.parents[1] / "ui" / "src" / "next" / "hubs" / "session" / "flows"
                    / "framing" / "FlowFrameSheet.tsx")
MARKER = "target-framing-sheet"
SECTIONS = ["WHERE", "GRID", "ANGLE", "PANELS", "RUN", "CENTRING"]


def _route_file() -> dict:
    return json.loads(ROUTES_PATH.read_text(encoding="utf-8"))


def _route(name: str) -> dict:
    return copy.deepcopy(next(r for r in _route_file()["routes"] if r["name"] == name))


# ------------------------------------------------------------------ fixture

FILLER = ("This is a synthetic page standing in for the AstroDeck flows screen. "
          "It carries enough text for the probe's vacuity guard, which refuses a "
          "page of fewer than two hundred characters as blank, and nothing more.")

# SkyCanvas's touch chip, SkyCanvas.tsx (the `touchDevice &&` block): an
# absolute, pointer-events-none layer at z-20 holding a sticky, 4 px padded
# wrapper at top-1, and a 44 px mono uppercase button. Inline styles for the
# Tailwind classes it carries.
CHIP = ('<div style="position:absolute;inset:0;z-index:20;pointer-events:none">'
        '<div style="position:sticky;top:4px;padding:4px">'
        '<button type="button" style="pointer-events:auto;min-height:44px;padding:0 8px;'
        'display:inline-flex;align-items:center;gap:6px;border-radius:10px;'
        'border:1px solid #888;background:#000c;color:#aaa;font:12px monospace;'
        'text-transform:uppercase;letter-spacing:.05em">'
        '<span>\u21d5</span><span>Swipe scrolls page</span></button></div></div>')

PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>*,::before,::after{box-sizing:border-box} body{margin:0;font:14px/1.5 sans-serif}
button{font:inherit;min-height:44px}
%(sky_css)s</style></head><body>
<header>ASTRODECK</header>
<main id="main-body"><p>%(filler)s</p>%(doors)s</main>
<div id="sheet" hidden>
 <div %(marker)s style="position:fixed;inset:0;display:flex;flex-direction:column;background:#fff">
  <header data-testid="framing-header" style="flex:none;height:48px;display:flex;align-items:center;justify-content:space-between">
   <button type="button">CANCEL</button><h2 style="margin:0;font-size:14px">FRAME M31</h2>
   <button type="button" id="done" %(done)s><span data-testid="framing-done">DONE</span></button>
  </header>
  <div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:%(dir)s">
   <div data-testid="framing-sky" class="fx-sky">
    <div class="fx-fit"><div class="sky-canvas">
     <div class="sky-canvas-square">
      <div role="application" class="fx-app">
       %(chip)s
       <span class="fx-n">N</span><span class="fx-w">W</span>
       <div style="position:absolute;%(move)s;top:8px;display:flex;flex-direction:column;gap:4px;z-index:5">
        <button type="button">MOVE SKY</button><button type="button">MOVE GRID</button>
       </div>
      </div>
     </div>
     %(lines)s
    </div></div>
   </div>
   <div data-testid="framing-scroller" style="flex:1 1 auto;min-height:0;min-width:0;overflow-y:auto;%(scroller)s">
    %(sections)s
   </div>
  </div>
 </div>
</div>
<script>
const V = %(variant)s;
const $ = (id) => document.getElementById(id);
function openSheet() {
  $('main-body').hidden = true;
  $('sheet').hidden = false;
  setTimeout(() => {
    // The night card's ask: same route, no anchor. DONE does not wait on it,
    // and the probe must let it through.
    fetch('/api/framing/mosaic', {method: 'POST', body: JSON.stringify({rows: 2, cols: 3})});
    fetch('/api/framing/mosaic', {method: 'POST', body: JSON.stringify({rows: 2, cols: 3, anchor: '{}'})})
      .then((r) => {
        if (r.ok && V !== 'done_stuck') {
          $('done').removeAttribute('aria-disabled');
          $('done').removeAttribute('title');
        }
      });
  }, 400);
}
function show(id) { $(id).hidden = false; }
</script>
</body></html>"""

# The phone's doors: the flow list's row, the stage list (whose DUSK row's
# wire line also says TARGET, as the real one does, and opens nothing), the
# stage editor's FRAME ON SKY.
PHONE_DOORS = """
<div><button type="button" data-testid="flow-open-probe-s4-m31-mosaic" onclick="show('stages')">M31 mosaic (probe copy)</button></div>
<div id="stages" data-testid="session-flow-stages" hidden>
 <div data-testid="flow-stage-row"><button type="button" class="nx-row"><span class="nx-row-title">DUSK WINDOW</span> <span>window opens -&gt; TARGET</span></button></div>
 <div data-testid="flow-stage-row"><button type="button" class="nx-row" onclick="%(target_press)s"><span class="nx-row-title">TARGET</span> <span>M31</span></button></div>
</div>
<div id="node" hidden>%(frame_door)s</div>"""

# The classic desktop's doors: the library card, the TARGET card (pressed on
# its title strip), the inspector's FRAME ON SKY. The library card also opens
# the inspector's FLOW panel, the 284 px column that scrolls (#469's, with
# its "While a mosaic waits" select; `scrollbar-width: thin` as index.css
# sets it), which the walk's mid-walk `scrollbar` check measures (#535).
DESKTOP_DOORS = """
<button type="button" data-flow-id="probe-s4-m31-mosaic" onclick="show('canvas')">M31 mosaic (probe copy)</button>
<div id="canvas" hidden><div data-node-id="n2" data-node-type="target" style="width:188px;height:96px;border:1px solid"
 onclick="show('inspector')">TARGET</div>
 <div data-flows-inspector="true" style="width:284px;height:300px;overflow-y:auto;border-left:1px solid #888;scrollbar-width:thin">
  <label style="display:block;height:1200px">While a mosaic waits <select style="font-size:10.5px">
   <option>Shoot later targets, then come back</option></select></label></div></div>
<div id="inspector" hidden><button type="button" data-flows-frame="n2" onclick="openSheet()">FRAME ON SKY</button></div>"""


def _sections(variant: str) -> str:
    bar_text = ("display:block;position:relative;top:6px;font-size:10px;line-height:14px"
                if variant == "text_clipped" else
                "display:block;font-size:10px;line-height:14px;padding-left:4px")
    alt = ("flex:0 0 30px;text-align:right" if variant == "text_wrapped" else
           "flex:0 0 80px;text-align:right;white-space:nowrap")
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;min-height:44px">'
        f'<span style="flex:1 1 auto"><span class="tfs-bar" style="display:block;height:16px;'
        f'border:1px solid #888;overflow:hidden;position:relative">'
        f'<span class="tfs-bar-text" style="{bar_text}">0/80</span></span></span>'
        f'<span data-testid="framing-alt-cell" style="{alt}">no peak</span></div>'
        for _ in range(2))
    out = []
    for name in SECTIONS:
        head = f'<h3 style="margin:0;min-height:44px">{name}</h3>'
        if name == "RUN" and variant == "label_clipped":
            # Present, 44 px tall, is_visible() true - and inside a box of no
            # height that clips it away, so no finger can reach it.
            head = f'<div style="height:0;overflow:hidden">{head}</div>'
        body = rows if name == "PANELS" else f'<p style="height:300px">{FILLER}</p>'
        testid = ' data-testid="framing-panels"' if name == "PANELS" else ""
        out.append(f"<section{testid}>{head}{body}</section>")
    return "".join(out)


# The step kinds that click nothing: a view that exists only once the hash
# says `#/frame` (reached by a `goto`), and a premise element (`wait_for`).
STEP_PAGE = """<!doctype html><html><body><header>ASTRODECK</header><p>%(filler)s</p>
%(premise)s
<div id="view" hidden><div data-testid="target-framing-sheet" style="width:200px;height:200px">view</div></div>
<script>
function route() { document.getElementById('view').hidden = location.hash !== '#/frame'; }
addEventListener('hashchange', route); route();
</script></body></html>"""


# The #/next flowFrame sheet's waiting card (FlowFrameSheet.tsx), in the phone
# sheet slot as SheetHost draws it: an absolute, `display: flex` slot over the
# screen, holding the sheet's own wrapper. `card_flush` is the wrapper as it
# stood on 2026-09-28, a bare flex item with no width and no padding, so the
# card's dashed border lay on the screen's top and left edges; `card_inset` is
# the wrapper with the sheet body's frame (next.css `.nx-sheet-body`: flex 1,
# 10 px 16 px padding). `card_flush_<edge>` is that frame with ONE edge's
# padding gone, so the card lies on that edge alone. The app's header is under
# the sheet layer, as it is on the real page, where the probe's vacuity guard
# looks for it.
CARD_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>*,::before,::after{box-sizing:border-box} body{margin:0;font:14px/1.5 sans-serif}
button{font:inherit;min-height:44px}</style></head><body>
<header>ASTRODECK</header>
<div style="position:fixed;inset:0;display:flex;background:#fff">
 <div data-testid="session-flow-frame" style="%(wrap)s">
  <div data-testid="flow-frame-other-flow" style="border:1px dashed #888;border-radius:12px;padding:16px 14px;display:flex;flex-direction:column;gap:8px;align-items:flex-start">
   <div>FRAME</div><div>This framing opens once the flow the link names has loaded.</div>
   <div><button type="button" data-testid="flow-frame-other-flow-back">BACK</button></div>
  </div>
  <p>%(filler)s</p>
 </div>
</div></body></html>"""


# The compass letters and the line's type, as SkyCanvas draws them (`top-1`,
# `right-1`, `text-[12px] mono`; the banner's `px-2 py-1` and border).
_SKY_FURNITURE = (
    ".fx-n{position:absolute;left:50%;transform:translateX(-50%);top:4px;font:12px/18px monospace}"
    ".fx-w{position:absolute;right:4px;top:50%;transform:translateY(-50%);font:12px/18px monospace}"
    ".fx-line{flex:none;font-size:12px;line-height:18px;padding:4px 8px;border:1px solid #888}")


def _sky_css(variant: str, phone: bool) -> str:
    """The modal's sky, as framing.css lays it out on a phone (#440, #465): a
    clipping box that is a size container, the fit box its largest square,
    SkyCanvas's column filling it, and the square's slot taking what the
    lines under it leave, read off the slot as a size container itself.
    `square_fills` drops that last `container-type`, which is the real page's
    "square fills the box" mutant (#495): the square is read off the sky, 390
    px in a slot the line has made 354 px, and centring pushes it 18 px past
    the sky's top and 18 px past the slot's bottom, 10 px over the line (the
    8 px gap takes the rest). `sky_collapsed` is a 2 px sky that
    does not clip, so the move pair still spills where a finger can reach it
    and the collapse is that page's one difference. The desktop is a plain
    720 px canvas beside the scroller."""
    if not phone:
        return (".fx-sky{flex:1 1 auto;min-width:0;position:relative;overflow:hidden;display:flex;"
                "align-items:center;justify-content:center}"
                ".sky-canvas{display:flex;flex-direction:column;gap:8px}"
                ".fx-app{position:relative;width:720px;height:720px}" + _SKY_FURNITURE)
    collapsed = variant == "sky_collapsed"
    slot = ("flex:1 1 0;min-height:0;display:flex;align-items:center;justify-content:center"
            + ("" if variant == "square_fills" else ";container-type:size"))
    return (f".fx-sky{{flex:none;height:{'2px' if collapsed else '390px'};min-width:0;"
            f"position:relative;overflow:{'visible' if collapsed else 'hidden'};"
            f"container-type:size;display:flex;align-items:center;justify-content:center}}"
            f".fx-fit{{width:min(100cqw,100cqh);height:min(100cqw,100cqh)}}"
            f".sky-canvas{{display:flex;flex-direction:column;gap:8px;height:100%}}"
            f".sky-canvas-square{{{slot}}}"
            f".fx-app{{position:relative;width:min(100cqw,100cqh);aspect-ratio:1/1}}"
            + _SKY_FURNITURE)


def page_html(variant: str, layout: str) -> str:
    if variant == "blank":
        return "<!doctype html><html><body></body></html>"
    if variant.startswith("card_"):
        # padding: top right bottom left, as CSS reads it.
        pad = {"card_inset": "10px 16px", "card_flush_left": "10px 16px 10px 0",
               "card_flush_top": "0 16px 10px", "card_flush_right": "10px 0 10px 16px"}.get(variant)
        wrap = (f"flex:1 1 auto;min-width:0;padding:{pad};display:flex;"
                "flex-direction:column;gap:10px" if pad else "")
        return CARD_PAGE % {"filler": FILLER, "wrap": wrap}
    if variant.startswith("steps"):
        premise = "" if variant == "steps_no_premise" else '<div id="premise">premise</div>'
        return STEP_PAGE % {"filler": FILLER, "premise": premise}
    phone = layout == "phone"
    if phone:
        doors = PHONE_DOORS % {
            "target_press": "openSheet()" if variant == "no_frame_door" else "show('node')",
            "frame_door": "" if variant == "no_frame_door" else
            '<button type="button" data-testid="flow-node-frame" onclick="openSheet()">FRAME ON SKY</button>',
        }
    else:
        doors = DESKTOP_DOORS
    # The degraded line under the canvas, in SkyCanvas's column, which a fresh
    # config's survey always shows (#440, #465). `line_over_canvas` draws it
    # inside the sky but out of the column, over the canvas's bottom edge.
    line = ('<div data-role="survey-degraded" class="fx-line"%s>Survey tiles not loading. '
            'Retrying automatically.</div>')
    lines = ("" if variant == "no_line" else
             line % (' style="position:absolute;left:0;right:0;bottom:0"'
                     if variant == "line_over_canvas" else ""))
    return PAGE % {
        "filler": FILLER, "doors": doors,
        "sky_css": _sky_css(variant, phone), "lines": lines,
        "marker": 'data-testid="target-framing-shell"' if variant == "no_marker" else f'data-testid="{MARKER}"',
        "done": "" if variant == "done_early" else
        'aria-disabled="true" title="waiting for the server\'s panel positions"',
        "dir": "column" if phone else "row",
        "chip": CHIP if phone else "",
        "move": "left:8px" if variant == "control_covered" else "right:8px",
        # A collapsed sky lets the move pair and the chip spill over the top
        # of the scroller; the headings start below them, so the collapse is
        # the page's ONE difference and nothing it spills covers a label.
        "scroller": ("padding-top:160px" if variant == "sky_collapsed" else "") if phone
        else "flex:none;width:360px",
        "sections": _sections(variant),
        "variant": json.dumps(variant),
    }


EXAMPLE = {
    "id": "example-m31-mosaic", "name": "M31 3x2, rotating", "folder": "Examples",
    "readonly": True, "graph": {"nodes": [
        {"id": "n1", "type": "dusk", "params": {}},
        {"id": "n2", "type": "target", "params": {"name": "M31", "rows": 2, "cols": 3}},
    ], "edges": [], "settings": {}},
}


class Handler(BaseHTTPRequestHandler):
    # Per-test state, set by the test before it walks.
    state: dict = {}

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/flows/example-m31-mosaic":
            ex = copy.deepcopy(EXAMPLE)
            ex["graph"]["nodes"][1]["params"].update(self.state.get("example_grid", {}))
            return self._json(200, ex)
        if path.startswith("/api/flows/"):
            fid = path.rsplit("/", 1)[-1]
            stored = self.state.setdefault("store", {}).get(fid)
            return self._json(200, stored) if stored else self._json(404, {"detail": "not found"})
        if path == "/":
            html = page_html(self.state.get("variant", "good"), self.state.get("layout", "phone"))
            return self._send(200, html.encode("utf-8"), "text/html")
        # Anything else a browser asks for on its own (a favicon) is answered,
        # so it is not a failed request the route would be charged for.
        return self._send(200, b"", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        path = self.path.split("?")[0]
        if path == "/api/framing/mosaic":
            self.state.setdefault("mosaic_bodies", []).append(raw.decode("utf-8", "replace"))
            return self._json(200, {"panels": []})
        if path == "/api/flows":
            flow = json.loads(raw.decode("utf-8"))["flow"]
            self.state.setdefault("posts", []).append(flow)
            # As the server's store does (FlowStore.save_and_report): an
            # Example's id is refused, 403 through _persist_flow.
            if str(flow.get("id", "")).startswith("example-"):
                return self._json(403, {"detail": {"code": "readonly"}})
            if self.state.get("rewrite_name"):
                # A store that keeps something other than what it was sent.
                for n in flow["graph"]["nodes"]:
                    if n["id"] == "n2":
                        n["params"]["name"] = "M31"
            self.state.setdefault("store", {})[flow["id"]] = flow
            return self._json(200, {"flow": flow})
        return self._json(404, {"detail": "not found"})


class _Browser(unittest.TestCase):
    """One fixture server and one browser per class."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()
        # The probe's own browsers: a desktop walk here draws its scrollbars,
        # as the real walks do (probe.py HIDE_SCROLLBARS, #535).
        cls.browsers = probe._Browsers(cls.pw)
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.browsers.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.tmp.cleanup()

    def context(self, layout: str):
        """The context the probe builds for the layout's width (390 x 844 with
        touch, or 1440 x 900), on the probe's browser for that width."""
        return self.browsers.context(390 if layout == "phone" else 1440)

    def walk(self, variant: str, layout: str = "phone", route: dict | None = None) -> dict:
        Handler.state = {"variant": variant, "layout": layout}
        route = route or _route("s4-frame-phone" if layout == "phone" else "s4-frame-classic-desktop")
        ctx = self.context(layout)
        try:
            return probe._run_isolated_route(ctx, self.base, route, Path(self.tmp.name),
                                             390 if layout == "phone" else 1440)
        finally:
            ctx.close()

    def assertFailsOnlyFor(self, result: dict, *needles: str) -> None:
        """The walk failed, and every reason it gives is the one this page
        was built to show - so the check under test, not some other, is what
        caught it."""
        self.assertFalse(result["passed"], "the probe passed a page it must fail")
        self.assertTrue(result["reasons"])
        for reason in result["reasons"]:
            self.assertTrue(any(n in reason for n in needles),
                            f"unexpected reason {reason!r}; all: {result['reasons']}")


# -------------------------------------------------------------------- walks

class PhoneWalkTest(_Browser):

    def test_the_good_page_passes_every_check(self):
        """CONTROL. The fixture satisfies every check the real route makes, so
        each negative page below can fail for its one difference and nothing
        else. Without this, a route no page could satisfy would make every
        negative case pass vacuously. It is also the gate's positive case.

        Mutation "gate does not hold" (probe.py `_Gate._on_route`: nothing
        held), observed red:
            AssertionError: False is not true : ['gate: no request to
            \\'**/api/framing/mosaic\\' containing \\'"anchor"\\' was made, so
            nothing shows what \\'button:has([data-testid="framing-done"])\\'
            waits on']"""
        r = self.walk("good")
        self.assertTrue(r["passed"], r["reasons"])
        self.assertEqual(r["testid_ok"], True)
        self.assertEqual([s["action"] for s in r["click_log"]], ["click"] * 3)
        self.assertEqual([l["text"] for l in r["labels"]], SECTIONS)
        g = r["gate"]
        self.assertEqual((g["held"], g["locked_while_held"], g["answers"], g["unlocked"]),
                         (1, True, [200], True))
        self.assertEqual(g["locked_reason"], "waiting for the server's panel positions")
        # The night card's request went straight through: two asks reached
        # the server, and only the anchored one was held.
        self.assertEqual(len(Handler.state["mosaic_bodies"]), 2)

    def test_a_page_without_the_view_marker_fails_on_the_marker_alone(self):
        """THE MARKER CANNOT BE SKIPPED. Every door opens, every section
        renders, DONE unlocks - and the sheet does not carry
        data-testid="target-framing-sheet". The walk must fail, and for that.

        The page still fails with the marker assertion gone, because the
        labels are looked for INSIDE the marker's element and so cannot be
        found either. That second net is why this case also pins that the
        marker assertion is what fired: without it, a probe that had lost its
        marker check would read the same as one that had not.

        Mutation "marker assertion removed" (probe.py: `if testid:` -> `if
        False:`), observed red:
            AssertionError: None != False
        Mutation "the route file's marker removed" (routes_s4_frame.json: the
        phone route's "testid" deleted), observed red:
            AssertionError: False is not true : unexpected reason "route defines
            neither 'testid' nor 'marker' -- nothing to assert, so it cannot
            pass"; all: ["route defines neither 'testid' nor 'marker' --
            nothing to assert, so it cannot pass"]"""
        r = self.walk("no_marker")
        self.assertFailsOnlyFor(r, MARKER)
        self.assertEqual(r["testid_ok"], False)
        self.assertEqual(len(r["reasons"]), 1)
        self.assertTrue(r["reasons"][0].startswith(f"testid '{MARKER}'"), r["reasons"])

    def test_a_blank_page_fails(self):
        """A blank page cannot pass: the vacuity guard stops it before any
        click, so no later check can be mistaken for the view.

        Mutation "vacuity guard returns []" (probe.py `_run_route`), observed
        red: the walk still failed, but on the first required door instead of
        the guard:
            AssertionError: False is not true : unexpected reason 'required step
            \\'[data-testid="flow-open-probe-s4-m31-mosaic"]\\' failed: no visible
            match within 8000 ms'; all: [...]"""
        r = self.walk("blank")
        self.assertFailsOnlyFor(r, "vacuity:")
        self.assertEqual(r["click_log"], [])

    def test_a_missing_door_fails_the_walk_even_when_the_view_opens_anyway(self):
        """REQUIRED STEPS. The stage editor has no FRAME ON SKY row (the TARGET
        row opens the sheet itself), so the marker, the labels and the gate
        all pass. The walk grades the doors a person uses, so it fails on the
        door that is not there.

        Mutation "required flag ignored" (probe.py `_run_clicks`: `required =
        False`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("no_frame_door")
        self.assertFailsOnlyFor(r, "required step", "flow-node-frame")
        self.assertEqual(r["click_log"][-1]["action"], "missing")

    def test_a_label_clipped_to_nothing_fails(self):
        """LABELS ANSWER A HIT TEST. RUN is 44 px tall and is_visible() says
        so, inside a box of no height with overflow hidden.

        Mutation "label hit test removed" (probe.py `_check_labels`: only the
        height floor), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("label_clipped")
        self.assertFailsOnlyFor(r, "label 'RUN' is covered or clipped")

    def test_a_collapsed_sky_fails(self):
        """THE SKY IS NOT A 2 PX LINE (probe-visible-is-not-sized). A 390 x 2
        sky clears MIN_VISIBLE_PX's width and is visible to Playwright.

        Mutation "box floor removed" (probe.py `_check_boxes`: ok whenever a
        box exists), observed red:
            AssertionError: True is not false : the probe passed a page it must fail

        Graded by the floor alone: a collapsed sky holds nothing whole, so
        `contained` and `apart` fail it too, and would pass this case under
        the mutant above for a reason that is not the floor (H4)."""
        route = _route("s4-frame-phone")
        for check in ("contained", "apart"):
            del route[check]
        r = self.walk("sky_collapsed", route=route)
        self.assertFailsOnlyFor(r, "box '[data-testid=\"framing-sky\"]", "is 390 x 2")

    def test_a_control_under_the_touch_chip_fails(self):
        """CONTROLS ANSWER A HIT TEST. MOVE SKY in the top-left corner, under
        SkyCanvas's touch chip: the defect the real-page probe found on
        2026-09-26.

        Mutation "reachable hit test removed" (probe.py `_check_reachable`:
        ok = True), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("control_covered")
        self.assertFailsOnlyFor(r, "MOVE SKY", "is covered")
        covered = [c for c in r["reachable"] if not c["ok"]]
        self.assertEqual(len(covered), 1)
        self.assertIn("Swipe scrolls page", covered[0]["hit"]["hit"])

    def test_a_done_that_never_locked_fails(self):
        """DONE IS LOCKED WHILE ITS ANSWER IS HELD. A DONE live from the start
        reads exactly like one that unlocked on the answer by the time a
        probe looks, which is why the probe holds the answer.

        Mutation "locked-while-held check removed" (probe.py `_check_gate`),
        observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("done_early")
        self.assertFailsOnlyFor(r, "was live while the answer it waits on was still held")
        self.assertEqual(r["gate"]["locked_while_held"], False)

    def test_a_done_that_never_unlocks_fails(self):
        """DONE UNLOCKS ONCE THE SERVER ANSWERS.

        Mutation "unlock check removed" (probe.py `_check_gate`: the
        stayed-locked reason deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        route = _route("s4-frame-phone")
        route["gate"]["unlock_timeout_ms"] = 1500  # the wait, not the check
        r = self.walk("done_stuck", route=route)
        self.assertFailsOnlyFor(r, "stayed locked after the server answered")
        self.assertEqual((r["gate"]["answers"], r["gate"]["unlocked"]), ([200], False))

    def test_text_cut_by_an_overflow_hidden_box_fails(self):
        """TEXT IS NOT CLIPPED, and the probe does not un-clip it. The bar's
        text sits 6 px down a 16 px overflow-hidden bar (the real one sat there
        from a baseline, and lost 4 px).

        Mutation "clip check removed" (probe.py TEXT_INTACT_JS: `if (over >
        0.5)` -> `if (false)`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail
        Mutation "Playwright's scroll" (probe.py `_bring_into_view`:
        el.scroll_into_view_if_needed), observed red - the scroll moves the
        overflow-hidden bar itself and the text then measures whole:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("text_clipped")
        self.assertFailsOnlyFor(r, "text '0/80'")
        self.assertIn("is clipped", r["reasons"][0])

    def test_text_that_wraps_where_it_must_not_fails(self):
        """ONE LINE. 'no peak' in a 30 px column wraps onto two.

        Mutation "one-line check removed" (probe.py `_check_text_intact`: the
        wraps-onto reason's `elif` -> `elif False:`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("text_wrapped")
        self.assertFailsOnlyFor(r, "'no peak'", "wraps onto 2 lines")

    def test_a_canvas_pushed_under_the_skys_clip_fails(self):
        """THE SKY HOLDS ITS CANVAS WHOLE (#495, #440). The square read off the
        sky and not its slot (`square_fills`, the real page's "square fills
        the box" build): 390 px in the 354 px the line leaves, so centring
        pushes it 18 px past the sky's top and 10 px over the line, and MOVE
        SKY 10 px and the N 14 px under the clip with it, the numbers #495
        measured on the real page. Nothing else the walk checks sees it, as
        both frame walks passed that build: MOVE SKY's centre still answers
        the hit test, the sky and the canvas clear their floors.

        Mutation "containment not graded" (probe.py `_contained_attempt`: `ok =
        worst <= LAYOUT_TOLERANCE_PX` -> `ok = True`), observed red:
            AssertionError: Items in the second set but not the first:
            'contained'
        Mutation "overlap not graded" (probe.py `_apart_attempt`: `hit = across
        > ... and down > ...` -> `hit = False`), observed red:
            AssertionError: Items in the second set but not the first:
            'apart'"""
        r = self.walk("square_fills")
        self.assertFailsOnlyFor(r, "contained:", "apart:")
        self.assertEqual({x.split(":")[0] for x in r["reasons"]}, {"contained", "apart"})
        over = {row["selector"].split('"] ', 1)[1]: row["over"]["top"]
                for row in r["contained"][0]["rows"] if not row["ok"]}
        self.assertEqual(over, {'[role="application"]': 18, 'button:text-is("MOVE SKY")': 10,
                                'span:text-is("N")': 14})
        self.assertEqual([o["overlap"] for o in r["apart"][0]["others"]], [[390, 10]])

    def test_a_line_over_the_canvas_inside_the_sky_fails(self):
        """THE LINES LIE UNDER THE CANVAS, NOT OVER IT. The degraded line drawn
        over the canvas's bottom 28 px, inside the sky: every element is whole
        inside the clip, so `contained` passes the page, and `apart` alone
        sees it (#495: "a status line painted over the canvas").

        Mutation "overlap not graded" (as above), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("line_over_canvas")
        self.assertFailsOnlyFor(r, "apart:")
        self.assertEqual([o["overlap"] for o in r["apart"][0]["others"]], [[390, 28]])
        # CONTROL within the case: containment held, so it is `apart` that
        # tells this page from the good one.
        self.assertTrue(r["contained"][0]["ok"], r["contained"])

    def test_a_sky_that_never_shows_its_line_is_not_graded_as_one(self):
        """THE STATE IS NAMED AND WAITED FOR. With no line under the canvas the
        square fills the sky exactly, so "square fills the box" passes such a
        sky: the defect shows only with a line drawn. The walk states the
        state it grades (`when`: the degraded line), and a sky that never
        shows it fails, naming it, rather than passing on a layout that could
        not show the defect.

        Mutation "premise ignored" (probe.py `_await_premise`: returns True at
        once), observed red, `contained` passing the lineless sky:
            AssertionError: False is not true : unexpected reason 'apart: 0
            visible \\'[data-testid="framing-sky"] .sky-canvas >
            :not(.sky-canvas-square)\\', need >= 1: nothing to hold apart from
            \\'[data-testid="framing-sky"] [role="application"]\\''; all:
            [...]"""
        route = _route("s4-frame-phone")
        for check in ("contained", "apart"):
            for spec in route[check]:
                spec["timeout_ms"] = 1500  # the wait, not the check
        r = self.walk("no_line", route=route)
        self.assertFailsOnlyFor(r, "the state it grades never arrived")
        self.assertEqual(sorted(x.split(":")[0] for x in r["reasons"]), ["apart", "contained"])


class DesktopWalkTest(_Browser):

    def test_the_good_desktop_page_passes(self):
        """CONTROL for the classic desktop walk: its doors, its offset press on
        the TARGET card and its larger floors can all be met, and in the
        probe's desktop browser both scrolling columns draw their bar: the
        FLOW panel's, measured mid-walk, and the modal's control column.

        Mutation "default launch restored" (probe.py `_launch_args`: returns
        {} for every width, #535), observed red:
            AssertionError: False is not true : ['mid-walk check at step 2:
            scrollbar: \\'[data-flows-inspector="true"]\\' scrolls (1200px of
            content in 300px) and its scrollbar takes 0px of its 284px, need
            >= 8px: this browser hides its scrollbars, so every fit measured
            in this column is a bar too generous (#535)', 'scrollbar:
            \\'[data-testid="framing-scroller"]\\' scrolls (1992px of content
            in 852px) and its scrollbar takes 0px of its 360px, need >= 8px:
            this browser hides its scrollbars, so every fit measured in this
            column is a bar too generous (#535)']"""
        r = self.walk("good", "desktop")
        self.assertTrue(r["passed"], r["reasons"])
        self.assertEqual(r["testid_ok"], True)
        self.assertGreaterEqual(r["scrollbar"][0]["measured"]["bar"], 8)

    def test_the_desktop_walk_needs_the_marker_too(self):
        """As the phone's case, and for the same reason it pins that the marker
        assertion fired: the first version of this case asserted only that
        the walk failed on a reason naming the marker, and under the mutation
        below it stayed green, because the labels' container is the marker.

        Mutation "marker assertion removed" (as above), observed red:
            AssertionError: None != False"""
        r = self.walk("no_marker", "desktop")
        self.assertFailsOnlyFor(r, MARKER)
        self.assertEqual(r["testid_ok"], False)
        self.assertEqual(len(r["reasons"]), 1)
        self.assertTrue(r["reasons"][0].startswith(f"testid '{MARKER}'"), r["reasons"])


# ------------------------------------------------------------ waiting card

class WaitingCardTest(_Browser):
    """The #/next sheet's waiting card, walked with the REAL fresh-missing-flow
    route: the card must be the width of the sheet less its gutter, and sit
    inside that gutter (`boxes` `min_inset`). The real page drew it against
    the screen's top and left edges on 2026-09-28, and every other check the
    route made passed: the card was visible, its marker was there and its
    BACK answered the hit test."""

    def test_a_waiting_card_inside_the_sheet_gutter_passes(self):
        """CONTROL. The card in the sheet body's 10 px 16 px frame passes, so
        the next case fails for its one difference: the frame."""
        r = self.walk("card_inset", route=_route("s4-frame-phone-fresh-missing-flow"))
        self.assertTrue(r["passed"], r["reasons"])
        card = next(b for b in r["boxes"] if "flow-frame-other-flow" in b["selector"])
        self.assertEqual(card["edges"], {"left": 16, "top": 10, "right": 16})
        self.assertEqual(card["box"]["width"], 358)

    def test_a_waiting_card_against_the_screen_edge_fails(self):
        """THE CARD KEEPS OFF THE SCREEN EDGE. The wrapper with no frame: the
        card is 390 px wide (the paragraph under it stretches the bare flex
        item), so the width floor alone passes it; only the inset catches it.

        Mutation "inset check removed" (probe.py `_check_boxes`: `min_inset`
        ignored), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk("card_flush", route=_route("s4-frame-phone-fresh-missing-flow"))
        self.assertFailsOnlyFor(r, "box '[data-testid=\"flow-frame-other-flow\"]'",
                                "from the viewport's left, top and right")
        card = next(b for b in r["boxes"] if "flow-frame-other-flow" in b["selector"])
        self.assertEqual((card["edges"]["left"], card["edges"]["top"]), (0, 0))

    def test_each_edge_is_graded_on_its_own(self):
        """`min_inset` holds the left, top AND right edges. The flush card lies
        on all three at once, so a probe that graded only one of them passed
        every case above: here the card lies on ONE edge and keeps the gutter
        on the other two, and must fail for that edge. The bottom is not
        graded (EDGES_JS), so it has no case.

        Mutation "inset graded on the left edge only" (probe.py
        `_check_boxes`: `min(edges.values()) >= inset` -> `edges["left"] >=
        inset`), observed red (S4-UPROBE verifier, scratchpad
        S4-UPROBE-r2-verify-mut, 2026-09-28), in the top and right subtests:
            AssertionError: True is not false : the probe passed a page it must fail
        Mutation "inset graded on the top edge only" (`edges["top"] >=
        inset`), observed red the same way in the left and right subtests,
        and "inset graded on the right edge only" (`edges["right"] >= inset`)
        in the left and top subtests. Before this case, all three survived
        every test in this file."""
        for edge in ("left", "top", "right"):
            with self.subTest(edge=edge):
                r = self.walk(f"card_flush_{edge}",
                              route=_route("s4-frame-phone-fresh-missing-flow"))
                self.assertFailsOnlyFor(r, "from the viewport's left, top and right")
                card = next(b for b in r["boxes"] if "flow-frame-other-flow" in b["selector"])
                # CONTROL within the case: the card is on this edge only, and
                # clear of the other two by the gutter's width.
                self.assertEqual(card["edges"][edge], 0)
                self.assertTrue(all(v >= 10 for k, v in card["edges"].items() if k != edge),
                                card["edges"])


# -------------------------------------------------------------------- steps

class StepTest(_Browser):
    """`goto` and `wait_for`, the two steps that click nothing (the other-flow
    walk ends on a `goto`, and states its premise with a `wait_for`)."""

    def test_a_goto_navigates_as_a_link_does(self):
        """The view exists only at `#/frame`, reached by the step.

        Mutation "goto step ignored" (probe.py `_run_clicks`: the goto branch
        logs without setting the hash), observed red:
            AssertionError: False is not true : ["testid 'target-framing-sheet'
            ([data-testid='target-framing-sheet']) not visible after clicks
            (click log: [{'text': '#/frame', 'action': 'goto'}])"]"""
        route = {"name": "goto", "url": "#/start", "click": [{"goto": "#/frame"}], "testid": MARKER}
        r = self.walk("steps", route=route)
        self.assertTrue(r["passed"], r["reasons"])
        self.assertEqual(r["click_log"], [{"text": "#/frame", "action": "goto"}])

    def test_a_premise_that_holds_is_seen(self):
        """CONTROL for the next case: a present premise is `seen`, not clicked."""
        route = {"name": "seen", "url": "#/start", "testid": MARKER,
                 "click": [{"goto": "#/frame"}, {"wait_for": "#premise"}]}
        r = self.walk("steps", route=route)
        self.assertTrue(r["passed"], r["reasons"])
        self.assertEqual([s["action"] for s in r["click_log"]], ["goto", "seen"])

    def test_a_premise_that_does_not_hold_fails_the_walk(self):
        """A `wait_for` is required: a walk whose premise never appeared is not
        grading the situation it claims to.

        Mutation "wait_for not required" (probe.py `_run_clicks`: the step is
        rewritten with `"required": False`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        route = {"name": "unseen", "url": "#/start", "testid": MARKER,
                 "click": [{"goto": "#/frame"}, {"wait_for": "#premise", "wait_ms": 1000}]}
        r = self.walk("steps_no_premise", route=route)
        self.assertFailsOnlyFor(r, "required step '#premise' failed")


# --------------------------------------------------------------------- seed

class SeedTest(_Browser):

    def _seed(self, **state):
        Handler.state = dict(state)
        ctx = self.context("desktop")
        try:
            return probe._seed(ctx.request, self.base, probe._load_seed(ROUTES_PATH))
        finally:
            ctx.close()

    def test_the_seed_saves_an_editable_copy_under_its_own_id(self):
        """The Example itself cannot be the flow walked: the store refuses an
        Example's id (403) and the sheet opens an Example with no DONE.

        Mutation "the copy keeps the Example's id" (probe.py `_seed`: id not
        replaced), observed red:
            probe.SeedError: POST /api/flows (copy of 'example-m31-mosaic' as
            'probe-s4-m31-mosaic') -> 403: {"detail": {"code": "readonly"}}"""
        log = self._seed()
        self.assertEqual(log, [
            {"copy_flow": "probe-s4-m31-mosaic", "from": "example-m31-mosaic", "readonly": False},
            {"copy_flow": "probe-s4-m33-namesake", "from": "example-m31-mosaic", "readonly": False}])
        posted = Handler.state["posts"][0]
        self.assertEqual((posted["id"], posted["readonly"], posted["folder"]),
                         ("probe-s4-m31-mosaic", False, "My flows"))
        self.assertEqual(posted["graph"], EXAMPLE["graph"])

    def test_the_namesake_copy_frames_another_object_in_the_same_node(self):
        """The other-flow walk needs two flows that share node n2 and differ in
        what it frames; `set_params` makes the second.

        Mutation "set_params ignored" (probe.py `_seed`: the loop removed),
        observed red:
            probe.SeedError: the copy 'probe-s4-m33-namesake''s node 'n2' stored
            name='M31', not 'M33'"""
        self._seed()
        namesake = Handler.state["posts"][1]
        n2 = next(n for n in namesake["graph"]["nodes"] if n["id"] == "n2")
        self.assertEqual((namesake["id"], n2["params"]["name"], n2["params"]["ra"]),
                         ("probe-s4-m33-namesake", "M33", "01h 33m 51s"))
        # CONTROL: the other nodes are the Example's.
        self.assertEqual([n for n in namesake["graph"]["nodes"] if n["id"] != "n2"],
                         [n for n in EXAMPLE["graph"]["nodes"] if n["id"] != "n2"])

    def test_a_copy_stored_other_than_it_was_sent_refuses(self):
        """expect_node's params are read off the copy AS STORED: two flows
        whose TARGETs the server stored alike would let the other-flow walk
        pass with nothing told apart.

        Mutation "expect params check removed" (probe.py `_seed`: the params
        loop removed), observed red:
            AssertionError: SeedError not raised"""
        with self.assertRaises(probe.SeedError) as caught:
            self._seed(rewrite_name=True)
        self.assertIn("stored name='M31', not 'M33'", str(caught.exception))

    def test_a_seed_whose_example_stopped_being_a_mosaic_refuses(self):
        """Were the eighth Example ever one panel, the walk would grade a sheet
        with no grid and still pass.

        Mutation "expect_node check removed" (probe.py `_seed`), observed red:
            AssertionError: SeedError not raised"""
        with self.assertRaises(probe.SeedError) as caught:
            self._seed(example_grid={"rows": 1, "cols": 1})
        self.assertIn("1 panel(s)", str(caught.exception))


# --------------------------------------------------------------- route file

class RouteFileTest(unittest.TestCase):

    def test_each_width_walks_its_own_door(self):
        """`widths` keeps the phone walk off the desktop and the other way
        round: at 820 the phone's hash is the canvas, not the list.

        Mutation "width filter removed" (probe.py `_routes_for_width` returns
        every route), observed red (re-run by the verifier once the
        fresh-missing-flow walk was added):
            AssertionError: Lists differ: ['s4-[19 chars]rame-classic-desktop',
            's4-frame-phone-other-f[68 chars]low'] != ['s4-[19 chars]rame-phone-other-flow',
            's4-frame-phone-missin[40 chars]low']"""
        routes = _route_file()["routes"]
        names = lambda w: [r["name"] for r in probe._routes_for_width(routes, w)]
        self.assertEqual(names(390), ["s4-frame-phone", "s4-frame-phone-other-flow",
                                      "s4-frame-phone-missing-flow",
                                      "s4-frame-phone-fresh-missing-flow"])
        self.assertEqual(names(1440), ["s4-frame-classic-desktop"])
        self.assertEqual(names(820), [])
        # CONTROL: a route with no `widths` still runs everywhere.
        self.assertEqual(len(probe._routes_for_width([{"name": "x"}], 820)), 1)

    def test_both_walks_assert_the_one_view_marker_through_required_doors(self):
        """What the acceptance asks of the route file, held so an edit that
        drops it is red here rather than a quieter probe.

        Mutation "the route file's marker removed" (as above), observed red:
            AssertionError: None != 'target-framing-sheet'"""
        data = _route_file()
        seeded = data["seed"][0]["copy_flow"]
        self.assertEqual(seeded["from"], "example-m31-mosaic")
        self.assertNotEqual(seeded["id"], seeded["from"])
        for route in (_route("s4-frame-phone"), _route("s4-frame-classic-desktop")):
            self.assertEqual(route.get("testid"), MARKER)
            # Every door is required; a mid-walk check opens none (H4's
            # scrollbar check on the classic walk).
            doors = [s for s in route["click"] if "check" not in s]
            self.assertTrue(doors and all(s.get("required") for s in doors))
            self.assertEqual(route["labels"]["texts"], SECTIONS)
            self.assertIn(seeded["id"], route["url"] + json.dumps(route["click"]))
            self.assertEqual(route["gate"]["control"], 'button:has([data-testid="framing-done"])')
        phone = _route("s4-frame-phone")
        self.assertEqual(phone["widths"], [390])
        self.assertIn(390, probe.WIDTH_PROFILES)
        self.assertTrue(probe.WIDTH_PROFILES[390]["is_mobile"])

    def test_the_phone_walk_holds_the_sky_whole_with_its_line_showing(self):
        """#495's acceptance, held in the route file: s4-frame-phone grades
        the canvas, MOVE SKY, MOVE GRID and the compass letters whole inside
        the sky, and the canvas clear of the lines under it, once the
        degraded line is drawn.

        Mutation "the phone walk's containment dropped" (routes_s4_frame.json:
        s4-frame-phone's `contained` deleted), observed red:
            KeyError: 'contained'
        and its real-page red is in the route's `_mutations`."""
        route = _route("s4-frame-phone")
        degraded = '[data-testid="framing-sky"] [data-role="survey-degraded"]'
        (held,) = route["contained"]
        self.assertEqual((held["within"], held["when"]), ('[data-testid="framing-sky"]', degraded))
        self.assertEqual(held["selectors"], SKY_PARTS)
        (apart,) = route["apart"]
        self.assertEqual((apart["a"], apart["b"], apart["when"]), (SKY_PARTS[0], SKY_LINES, degraded))
        self.assertGreaterEqual(apart["min_b"], 1)

    def test_the_desktop_walk_says_its_columns_draw_their_bar(self):
        """#535, held in the route file: the classic walk measures the FLOW
        panel's column (#469's) mid-walk, before a node press replaces the
        panel, and the modal's control column at the end, each needing a bar
        of at least 8 px, under the 10 px thin bar, and more than any hidden
        one's 0.

        Mutation "the desktop walk's scrollbar checks dropped"
        (routes_s4_frame.json: the mid-walk check step and `scrollbar`
        deleted), observed red:
            AssertionError: Lists differ: [] != [('[data-flows-inspector="true"]',
            8)]"""
        route = _route("s4-frame-classic-desktop")
        mid = [(c["selector"], c["min_px"]) for s in route["click"]
               for c in s.get("check", {}).get("scrollbar", [])]
        self.assertEqual(mid, [('[data-flows-inspector="true"]', 8)])
        steps = [s.get("selector") or "check" for s in route["click"]]
        self.assertLess(steps.index("check"), steps.index('[data-node-id="n2"]'))
        self.assertEqual([(c["selector"], c["min_px"]) for c in route["scrollbar"]],
                         [('[data-testid="framing-scroller"]', 8)])
        # A phone's bar takes no width, so no phone walk asks for one.
        for phone in (r for r in _route_file()["routes"] if r.get("widths") == [390]):
            self.assertNotIn("scrollbar", json.dumps(phone), phone["name"])

    def test_the_other_flow_walk_ends_on_a_link_to_the_first_flow(self):
        """The other-flow walk's shape: it opens the namesake's framing (and
        says it saw M33 there), then follows a link to the M31 copy's framing
        and asserts that flow's: the marker and FRAME M31.

        Mutation "the other-flow walk's link removed" (routes_s4_frame.json:
        the goto step deleted), observed red:
            AssertionError: '#/session/flows/flowStages/flowFrame?open=probe-s4-m31-mosaic&node=n2'
            != None"""
        data = _route_file()
        a, b = (op["copy_flow"] for op in data["seed"])
        route = _route("s4-frame-phone-other-flow")
        self.assertEqual((route.get("testid"), route.get("marker")), (MARKER, "FRAME M31"))
        self.assertEqual(a["expect_node"]["params"], {"name": "M31"})
        self.assertEqual(b["expect_node"]["params"], {"name": "M33"})
        self.assertIn(b["id"], route["url"])
        self.assertIn("FRAME M33", route["click"][3].get("wait_for", ""))
        self.assertEqual(f"#/session/flows/flowStages/flowFrame?open={a['id']}&node=n2",
                         route["click"][-1].get("goto"))

    def test_the_missing_flow_walk_links_to_a_flow_nobody_seeded(self):
        """The missing-flow walk asserts the sheet's WAITING card and its BACK,
        which is only the right answer when the flow the link names never
        loads: a link to a seeded flow would load it, the modal would replace
        the card, and the walk would fail for the wrong reason or, pointed at
        the open flow, grade nothing.

        Mutation "the missing-flow link names a seeded flow"
        (routes_s4_frame.json: probe-s4-no-such-flow -> probe-s4-m31-mosaic in
        the goto), observed red:
            AssertionError: False is not true : ('probe-s4-m31-mosaic',
            ['probe-s4-m31-mosaic', 'probe-s4-m33-namesake'])"""
        seeded = {op["copy_flow"]["id"] for op in _route_file()["seed"]}
        route = _route("s4-frame-phone-missing-flow")
        link = route["click"][-1].get("goto", "")
        named = link.split("open=")[-1].split("&")[0]
        self.assertTrue(named and named not in seeded, (named, sorted(seeded)))
        self.assertIn(route["url"].split("open=")[-1], seeded)
        self.assertEqual(route["testid"], "flow-frame-other-flow")
        self.assertEqual(route["reachable"], ['[data-testid="flow-frame-other-flow-back"]'])

    def test_the_waiting_walks_hold_the_card_inside_the_sheet_gutter(self):
        """Both walks that end on the waiting card measure it: a floor on its
        size and an inset from the screen's edges, so the 2026-09-28 defect
        (the card against the top and left edges) is red on the real page.

        Mutation "the waiting walks' inset removed" (routes_s4_frame.json:
        `min_inset` deleted from both), observed red:
            AssertionError: [] is not true : ('s4-frame-phone-missing-flow',
            [{'selector': '[data-testid="flow-frame-other-flow"]', 'min_width':
            320, 'min_height': 110}])"""
        for name in ("s4-frame-phone-missing-flow", "s4-frame-phone-fresh-missing-flow"):
            route = _route(name)
            card = [b for b in route.get("boxes", [])
                    if b["selector"] == '[data-testid="flow-frame-other-flow"]'
                    and b.get("min_inset", 0) >= 8 and b.get("min_width", 0) >= 320]
            self.assertTrue(card, (name, route.get("boxes")))

    def test_the_two_waiting_walks_each_pin_their_own_card_wording(self):
        """The waiting card has two wordings (FlowFrameSheet.tsx): another flow
        is open (FRAME_OTHER_FLOW), or none is (FRAME_FLOW_LOADING, a fresh
        link). The missing-flow walk and the fresh-missing-flow walk each hold
        one by a text marker, and a marker is a case-insensitive SUBSTRING
        (probe `_visible_matches`), so each must sit in its own wording and not
        in the other's, read here off the source: a marker both wordings carry
        would pass on the wrong card. The fresh walk also clicks nothing, which
        is what makes it fresh, and names a flow nobody seeded.

        Mutation "fresh marker in both wordings" (routes_s4_frame.json: the
        fresh walk's marker -> "opens once"), observed red:
            AssertionError: 'opens once' unexpectedly found in 'this framing
            belongs to the flow the link names, and another flow is open. it
            opens once that flow has loaded.'
        Mutation "the other-flow wording also says the loading one"
        (FlowFrameSheet.tsx: FRAME_OTHER_FLOW's last sentence -> "It opens once
        the flow the link names has loaded."), observed red:
            AssertionError: 'opens once the flow the link names has loaded'
            unexpectedly found in 'this framing belongs to the flow the link
            names, and another flow is open. it opens once the flow the link
            names has loaded.'
        Mutation "the fresh walk opens a flow first" (routes_s4_frame.json: the
        fresh walk's `click` -> the namesake's list row, required), observed
        red:
            AssertionError: Lists differ: [{'selector': '[data-testid="flow-open-pro[36
            chars]rue}] != []
        (All three run in the verifier's private copy, scratchpad
        S4-UPROBE-verify-mut, 2026-09-26. The walk itself was red on the real
        page under G1 and G2, recorded in the route file's `_mutations`.)"""
        src = FLOW_FRAME_SHEET.read_text(encoding="utf-8")
        wording = {name: re.search(name + r' =\s*"([^"]+)"', src).group(1).lower()
                   for name in ("FRAME_OTHER_FLOW", "FRAME_FLOW_LOADING")}
        seeded = {op["copy_flow"]["id"] for op in _route_file()["seed"]}
        fresh = _route("s4-frame-phone-fresh-missing-flow")
        missing = _route("s4-frame-phone-missing-flow")
        self.assertEqual(fresh["click"], [])
        self.assertNotIn(fresh["url"].split("open=")[-1].split("&")[0], seeded)
        self.assertEqual((fresh["testid"], fresh["reachable"]),
                         (missing["testid"], missing["reachable"]))
        self.assertIn(fresh["marker"].lower(), wording["FRAME_FLOW_LOADING"])
        self.assertNotIn(fresh["marker"].lower(), wording["FRAME_OTHER_FLOW"])
        self.assertIn(missing["marker"].lower(), wording["FRAME_OTHER_FLOW"])
        self.assertNotIn(missing["marker"].lower(), wording["FRAME_FLOW_LOADING"])


# ------------------------------------------------------------- framing.css

CSS_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>*,::before,::after{box-sizing:border-box} :root{--font-mono:monospace}
body{margin:0;font:14px/1.5 sans-serif} button{font:inherit}</style>
<style>%(css)s</style></head><body>
<div class="tfs" style="width:390px;height:844px"><div class="tfs-body">
 <div class="tfs-sky"><div class="tfs-sky-fit">
  <div role="application" style="position:relative;width:100%%;aspect-ratio:1/1">
   <div class="tfs-move" role="group"><button type="button" class="tfs-btn">MOVE SKY</button><button type="button" class="tfs-btn">MOVE GRID</button></div>
   %(chip)s
  </div>
 </div></div>
 <div class="tfs-controls"><div class="tfs-scroller"><section data-testid="framing-panels">
  <div class="tfs-row tfs-panel">
   <span class="tfs-c-order tfs-mono">1</span><span class="tfs-c-label tfs-mono">1-1</span>
   <span class="tfs-c-on"><button type="button" class="tfs-btn">ON</button></span>
   <span class="tfs-c-bar"><span class="tfs-bar"><span class="tfs-bar-fill" style="width:0%%"></span><span class="tfs-bar-text tfs-mono">0/80</span></span></span>
   <span class="tfs-c-alt tfs-mono" data-testid="framing-alt-cell">no peak</span>
  </div>
 </section></div></div>
</div></div></body></html>"""


class FramingCssTest(unittest.TestCase):
    """framing.css, the REAL file, in a browser at the phone profile, around
    the markup its rules select: FramingSky's move pair inside SkyCanvas's
    box with the touch chip SkyCanvas draws there, and a PANELS row as
    PanelsSection writes it (class names only; the fonts are the app's
    measured 14 px / 21 px row). The real-page probe found all three broken on
    2026-09-26; this holds them without a server."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browsers = probe._Browsers(cls.pw)
        ctx = cls.browsers.context(390)
        cls.ctx = ctx
        cls.page = ctx.new_page()
        cls.page.set_content(CSS_PAGE % {"css": FRAMING_CSS.read_text(encoding="utf-8"),
                                         "chip": CHIP})
        cls.page.wait_for_timeout(200)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close()
        cls.browsers.close()
        cls.pw.stop()

    def test_the_move_pair_is_not_under_the_touch_chip(self):
        """Mutation "move pair back in the top-left" (framing.css `.tfs-move`:
        `right: 8px` -> `left: 8px`), observed red:
            AssertionError: Lists differ: ['control \\'button:text-is("MOVE SKY")\\'
            i[159 chars]'})'] != []
        whose element 0 is: control 'button:text-is("MOVE SKY")' is covered:
        the point at its centre is not the control ({'x': 48, 'y': 30,
        'in_view': True, 'inside': False, 'hit': '<span>Swipe scrolls
        page</span>'})"""
        _, reasons = probe._check_reachable(
            self.page, ['button:text-is("MOVE SKY")', 'button:text-is("MOVE GRID")'])
        self.assertEqual(reasons, [])

    def test_the_bar_text_is_whole(self):
        """Mutation "bar text inline again" (framing.css `.tfs-bar-text`:
        `display: block` removed), observed red:
            AssertionError: Lists differ: ['text \\'0/80\\' ([data-testid="framing-pan[66
            chars]xt)'] != []
        whose element 0 is: text '0/80' ([data-testid="framing-panels"]
        .tfs-bar-text) is clipped 2px by 'tfs-bar' (1 of 1 with text). 2 px
        with this fixture's fonts; 4 px with the app's, on the real page."""
        _, reasons = probe._check_text_intact(
            self.page, [{"selector": '[data-testid="framing-panels"] .tfs-bar-text'}])
        self.assertEqual(reasons, [])

    def test_no_peak_sits_on_one_line(self):
        """Mutation "PEAK column back to 44 px" (framing.css `.tfs-c-alt`:
        `flex: 0 0 44px`, no nowrap), observed red:
            AssertionError: Lists differ: ['text \\'no peak\\' ([data-testid="framing-[67
            chars]xt)'] != []
        whose element 0 is: text 'no peak' ([data-testid="framing-alt-cell"])
        wraps onto 2 lines, and must sit on one (1 of 1 with text)"""
        _, reasons = probe._check_text_intact(
            self.page, [{"selector": '[data-testid="framing-alt-cell"]', "one_line": True}])
        self.assertEqual(reasons, [])

    def test_the_fixture_has_a_chip_to_be_under(self):
        """CONTROL: the chip is drawn where SkyCanvas draws it, over the top-left
        corner, so the first case grades a real collision and not an empty
        corner."""
        hit = self.page.evaluate(
            "() => { const e = document.elementFromPoint(40, 30); return e ? e.textContent : null; }")
        self.assertIn("Swipe scrolls page", hit or "")


# The modal's sky as SkyCanvas builds it (the square's slot, the canvas, the
# move pair and the compass letters in it, and the lines under it: the
# degraded line and the verdict), under the REAL framing.css. The utility
# classes SkyCanvas carries are stood in for inside `@layer utilities`, where
# the app's Tailwind puts them, so framing.css's unlayered rules outrank them
# as they do in the app (an inline style would outrank framing.css instead).
SKY_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>*,::before,::after{box-sizing:border-box} :root{--font-mono:monospace}
body{margin:0;font:14px/1.5 sans-serif} button{font:inherit}
@layer utilities {
 .sky-canvas{display:flex;flex-direction:column;gap:8px}
 .fx-app{position:relative;width:100%%;aspect-ratio:1/1;min-width:min(320px,calc(100vw - 2rem));margin:0 auto}
 .fx-letter{position:absolute;font:12px/18px monospace}
 .fx-banner{font-size:12px;padding:4px 8px;border:1px solid #888}
 .fx-verdict{font-size:12px}
}</style>
<style>%(css)s</style></head><body>
<div class="tfs" style="width:390px;height:844px"><div class="tfs-body">
 <div class="tfs-sky" data-testid="framing-sky"><div class="tfs-sky-fit"><div class="sky-canvas">
  <div class="sky-canvas-square"><div role="application" class="fx-app">
   <div class="tfs-move" role="group"><button type="button" class="tfs-btn">MOVE SKY</button><button type="button" class="tfs-btn">MOVE GRID</button></div>
   <span class="fx-letter" style="left:50%%;transform:translateX(-50%%);top:4px">N</span>
   <span class="fx-letter" style="right:4px;top:50%%;transform:translateY(-50%%)">W</span>
  </div></div>
  <div data-role="survey-degraded" class="fx-banner">Survey tiles not loading. Retrying automatically.</div>
  <div class="fx-verdict">Object is 6.4x your frame - needs a mosaic</div>
 </div></div></div>
</div></div></body></html>"""

SKY = '[data-testid="framing-sky"]'
SKY_PARTS = [f'{SKY} [role="application"]', f'{SKY} button:text-is("MOVE SKY")',
             f'{SKY} button:text-is("MOVE GRID")', f'{SKY} span:text-is("N")',
             f'{SKY} span:text-is("W")']
SKY_LINES = f"{SKY} .sky-canvas > :not(.sky-canvas-square)"


class FramingSkyCssTest(unittest.TestCase):
    """framing.css, the REAL file, holding the Target modal's sky whole with
    BOTH lines under the canvas, the state #440 names (the survey degraded
    and a catalogue verdict showing), graded by the probe's own `contained`
    and `apart`. The real-page walks hold it on the app; this holds the CSS
    contract without a server, where jsdom (framingSkyDegraded.test.tsx) has
    no layout to hold it with."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browsers = probe._Browsers(cls.pw)
        cls.ctx = cls.browsers.context(390)
        cls.page = cls.ctx.new_page()
        cls.page.set_content(SKY_PAGE % {"css": FRAMING_CSS.read_text(encoding="utf-8")})
        cls.page.wait_for_timeout(200)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close()
        cls.browsers.close()
        cls.pw.stop()

    def test_the_canvas_and_its_furniture_lie_whole_inside_the_sky(self):
        """Mutation "square fills the box" (framing.css: `container-type: size`
        dropped from `.tfs-sky-fit .sky-canvas-square`, the real page's #495
        mutant), observed red:
            AssertionError: Lists differ: ['contained: \\'[data-testid="framing-sky"][765
            chars]90}'] != []
        whose three elements, read whole in the same mirror, are the canvas
        "(top 31px): its box {'left': 0, 'top': -31, 'right': 390, 'bottom':
        359}", MOVE SKY "(top 23px)" and the N "(top 27px)": with two lines
        under the canvas, the numbers the real page's catalogue walk measured
        on that build."""
        _, reasons = probe._check_contained(
            self.page, [{"within": SKY, "selectors": SKY_PARTS, "timeout_ms": 1000}])
        self.assertEqual(reasons, [])

    def test_no_line_lies_over_the_canvas(self):
        """Mutation "square fills the box" (as above), observed red:
            AssertionError: Lists differ: ['apart: \\'Survey tiles not loading.
            Retry[324 chars]59}'] != []
        read whole: the banner "lies over '[data-testid="framing-sky"]
        [role="application"]' by 23px down and 390px across"."""
        _, reasons = probe._check_apart(
            self.page, [{"a": SKY_PARTS[0], "b": SKY_LINES, "min_b": 2, "timeout_ms": 1000}])
        self.assertEqual(reasons, [])

    def test_the_fixture_draws_both_lines_under_a_canvas_that_fills_the_rest(self):
        """CONTROL: both lines are there and the square is the height they
        leave (390 less two lines and two gaps), so the two cases above grade
        the crowded sky and not an empty one. Under "square fills the box" it
        is red too, as it should be, the square being the sky's height:
            AssertionError: 390 not less than 338"""
        got = self.page.evaluate("""() => {
          const sky = document.querySelector('[data-testid="framing-sky"]').getBoundingClientRect();
          const app = document.querySelector('[role="application"]').getBoundingClientRect();
          return {sky: sky.height, app: app.height,
                  lines: document.querySelectorAll('.sky-canvas > :not(.sky-canvas-square)').length}; }""")
        self.assertEqual(got["lines"], 2)
        self.assertEqual(got["sky"], 390)
        self.assertLess(got["app"], 390 - 2 * 8 - 2 * 18)


# A clipping box with two children matched by one selector: the first always
# inside, the second placed by the case. `#box` has a 3 px border and 5 px of
# padding, so its padding box, where `overflow: hidden` clips, starts 3 px
# inside its border box; an absolute child's `left: 0` sits on that edge.
EDGE_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width"></head>
<body style="margin:0"><div style="padding:40px"><div id="box" style="position:relative;width:200px;
height:200px;overflow:hidden;border:3px solid #888;padding:5px">
 <div class="kid" style="position:absolute;left:80px;top:80px;width:40px;height:40px"></div>
 <div class="kid" style="position:absolute;%s;width:40px;height:40px"></div>
</div></div><div id="loose">no box around me</div>
<div id="canvas" style="position:absolute;left:300px;top:40px;width:100px;height:100px"></div>
<div class="line" style="position:absolute;left:300px;top:%spx;width:100px;height:20px"></div>
</body></html>"""


class ContainedCheckTest(unittest.TestCase):
    """`contained` and `apart` on their own, in the probe's desktop browser."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browsers = probe._Browsers(cls.pw)
        cls.ctx = cls.browsers.context(1440)
        cls.page = cls.ctx.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close()
        cls.browsers.close()
        cls.pw.stop()

    def contained(self, place: str, selectors=(".kid",), line_top: int = 140) -> list[str]:
        self.page.set_content(EDGE_PAGE % (place, line_top))
        _, reasons = probe._check_contained(
            self.page, [{"within": "#box", "selectors": list(selectors), "settle_ms": 200}])
        return reasons

    def test_a_child_inside_the_padding_box_passes(self):
        """CONTROL: both children inside, one flush on the padding box's left
        edge (`left: 0`), where the clip is."""
        self.assertEqual(self.contained("left:0;top:20px"), [])

    def test_every_edge_of_the_clip_is_graded(self):
        """A child 6 px past one edge, and inside the other three. The second
        match, so a check that graded the first match alone would pass all
        four. (Named apart from WaitingCardTest's edge case, which
        test_mosaic_spec_claims.py finds by its name.)

        Mutation "only the top edge graded" (probe.py `_contained_attempt`:
        `worst = max(over.values())` -> `worst = over["top"]`), observed red
        in the left, right and bottom subtests:
            AssertionError: [] is not true
        Mutation "only the first match graded" (`for i, el in
        enumerate(found)` -> `enumerate(found[:1])`), observed red in all
        four:
            AssertionError: [] is not true"""
        for edge, place in (("left", "left:-6px;top:20px"), ("top", "left:20px;top:-6px"),
                            ("right", "right:-6px;top:20px"), ("bottom", "left:20px;bottom:-6px")):
            with self.subTest(edge=edge):
                reasons = self.contained(place)
                self.assertTrue(reasons)
                self.assertEqual(len(reasons), 1, reasons)
                self.assertIn(f"({edge} 6px)", reasons[0])
                self.assertIn(".kid #2", reasons[0])

    def test_the_clip_is_the_padding_box_not_the_border_box(self):
        """A child 2 px past the padding box's left edge is cut by the clip,
        though it is 1 px inside the border box.

        Mutation "the border box graded" (probe.py CONTAINED_JS: `L = b.left +
        a.clientLeft` -> `L = b.left`, the same for T, R and B from the border
        box), observed red:
            AssertionError: [] is not true"""
        reasons = self.contained("left:-2px;top:20px")
        self.assertTrue(reasons)
        self.assertIn("(left 2px)", reasons[0])

    def test_a_selector_that_matches_nothing_fails(self):
        """A control that went missing is never contained by default.

        Mutation "nothing to measure passes" (probe.py `_contained_attempt`:
        the not-visible branch appends no problem), observed red:
            AssertionError: Lists differ: [] != ["contained: '#nothing' is not
            visible, so nothing says it sits inside '#box'"]"""
        self.assertEqual(self.contained("left:20px;top:20px", ("#nothing",)),
                         ["contained: '#nothing' is not visible, so nothing says it sits "
                          "inside '#box'"])

    def test_an_element_outside_any_such_container_fails(self):
        """`within` names the element's own container, an ancestor: an element
        with none is not inside one."""
        self.assertEqual(self.contained("left:20px;top:20px", ("#loose",)),
                         ["contained: '#loose' has no ancestor '#box'"])

    def test_boxes_that_meet_at_an_edge_are_apart_and_overlapping_ones_are_not(self):
        """`apart` on its own: a line whose top is the canvas's bottom touches it
        and does not overlap (CONTROL); one 5 px higher lies over it.

        Mutation "overlap not graded" (probe.py `_apart_attempt`: `hit =
        False`), observed red:
            AssertionError: [] is not true"""
        spec = [{"a": "#canvas", "b": ".line", "settle_ms": 200}]
        self.page.set_content(EDGE_PAGE % ("left:20px;top:20px", 140))
        self.assertEqual(probe._check_apart(self.page, spec)[1], [])
        self.page.set_content(EDGE_PAGE % ("left:20px;top:20px", 135))
        reasons = probe._check_apart(self.page, spec)[1]
        self.assertTrue(reasons)
        self.assertIn("by 5px down and 100px across", reasons[0])

    def test_apart_needs_as_many_lines_as_the_walk_names(self):
        """`min_b`: the one-tap walk grades the canvas with BOTH lines under it
        (min_b 2), so a sky showing one of them is not the state it grades and
        fails, naming the count; at min_b 1 the same page passes (CONTROL). No
        walk case reaches this: each walk's premise is one of its lines, so a
        premise that arrived brought a line with it (found by the verifier,
        2026-09-29, whose mutant ran in scratchpad H4-PROBE-LAYOUT-verify-mut).

        Mutation "min_b not enforced" (probe.py `_apart_attempt`: `if
        len(b_found) < need:` -> `if False:`), observed red (it survived
        every other case in the probe's suites):
            AssertionError: Lists differ: [] != ["apart: 1 visible '.line',
            need >= 2: nothing to hold apart from '#canvas'"]"""
        self.page.set_content(EDGE_PAGE % ("left:20px;top:20px", 140))
        one = [{"a": "#canvas", "b": ".line", "min_b": 1, "timeout_ms": 300, "settle_ms": 200}]
        self.assertEqual(probe._check_apart(self.page, one)[1], [])
        two = [{**one[0], "min_b": 2}]
        self.assertEqual(probe._check_apart(self.page, two)[1],
                         ["apart: 1 visible '.line', need >= 2: nothing to hold apart "
                          "from '#canvas'"])


if __name__ == "__main__":
    unittest.main()
