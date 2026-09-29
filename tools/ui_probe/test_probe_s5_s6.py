"""Self-tests for the S5 and S6 probe (#189 S5, S6, routes_s5_s6.json). Run
with the probe's Playwright Python, like test_probe_s4.py:

    cd tools/ui_probe && python -m unittest test_probe_s5_s6

No AstroDeck server, no UI build and no rig: every page is a loopback fixture,
and every API answer the checks read is the fixture's. Each check the route
file leans on has a CONTROL (a page it must pass, so the negative cases cannot
pass vacuously on a check no page could satisfy) and a case for the one thing
it exists to catch, and each such case names the mutation of probe.py that
turns it red, with the failure observed when that mutation was run in a
private copy (scratchpad S56-PROBE-mut, 2026-09-28; the runner and every
verbatim output are kept there).

The real page was walked with these checks too: routes_s5_s6.json's
`_mutations` entries record what they said about the UI as it stood before
each fix, and about each fix undone in a private build.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import copy
import io
import json
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
        f"`cd tools/ui_probe && python -m unittest test_probe_s5_s6`.")

import probe  # noqa: E402

ROUTES_PATH = HERE / "routes_s5_s6.json"


def _route_file() -> dict:
    return json.loads(ROUTES_PATH.read_text(encoding="utf-8"))


def _route(name: str) -> dict:
    return copy.deepcopy(next(r for r in _route_file()["routes"] if r["name"] == name))


# ------------------------------------------------------------------ fixture

FILLER = ("This is a synthetic page standing in for an AstroDeck screen. It "
          "carries enough text for the probe's vacuity guard, which refuses a "
          "page of fewer than two hundred characters as blank, and nothing more.")

PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>*,::before,::after{box-sizing:border-box} body{margin:0;font:14px/1.5 sans-serif}
button{font:inherit;min-height:44px}</style></head><body>
<header>ASTRODECK</header><p>%(filler)s</p>
<div data-testid="view" style="padding:16px">%(body)s</div>
<script>%(script)s</script>
</body></html>"""


def page(body: str, script: str = "") -> str:
    return PAGE % {"filler": FILLER, "body": body, "script": script}


class Handler(BaseHTTPRequestHandler):
    """The page is `state["page"]`; a GET of an API path answers
    `state["api"][path]`, and a LIST there is a sequence of answers, one per
    GET, the last repeating (a library before and after a walk, a counter
    that moves). POSTs are recorded in `state["posts"]` and answered from
    `state["post_api"]`, 200 {} by default."""
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
        if path == "/":
            return self._send(200, self.state.get("page", page("")).encode("utf-8"),
                              "text/html; charset=utf-8")
        api = self.state.get("api", {})
        if path in api:
            answer = api[path]
            if isinstance(answer, list) and answer and isinstance(answer[0], tuple):
                # [(status, body), ...]: consumed in order, the last repeating.
                status, body = answer[0] if len(answer) == 1 else answer.pop(0)
                return self._json(status, body)
            return self._json(200, answer)
        return self._send(200, b"", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        path = self.path.split("?")[0]
        self.state.setdefault("posts", []).append((path, raw.decode("utf-8", "replace")))
        status, body = self.state.get("post_api", {}).get(path, (200, {}))
        return self._json(status, body)


def seq(*answers):
    """A sequence of 200 answers for `Handler.state["api"]`."""
    return [(200, a) for a in answers]


class _Browser(unittest.TestCase):
    """One fixture server and one browser per class."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.tmp.cleanup()

    def walk(self, html: str, route: dict, api: dict | None = None, phone: bool = True,
             post_api: dict | None = None) -> dict:
        Handler.state = {"page": html, "api": api or {}, "post_api": post_api or {}}
        route = {"name": "fixture", "url": "#/", "testid": "view", **route}
        if phone:
            ctx = self.browser.new_context(viewport={"width": 390, "height": 844},
                                           is_mobile=True, has_touch=True)
        else:
            ctx = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            return probe._run_isolated_route(ctx, self.base, route, Path(self.tmp.name),
                                             390 if phone else 1440)
        finally:
            ctx.close()

    def assertFailsOnlyFor(self, result: dict, *needles: str) -> None:
        """The walk failed, and every reason it gives is the one this page was
        built to show - so the check under test, not some other, caught it."""
        self.assertFalse(result["passed"], "the probe passed a page it must fail")
        self.assertTrue(result["reasons"])
        for reason in result["reasons"]:
            self.assertTrue(any(n in reason for n in needles),
                            f"unexpected reason {reason!r}; all: {result['reasons']}")

    def assertPasses(self, result: dict) -> None:
        self.assertTrue(result["passed"], result["reasons"])


# ------------------------------------------------------------- text_expect

RUN_BUTTON = '<button type="button" data-testid="run"><span>%s</span></button>'
ROW = '<div data-testid="row"><b>%s</b> <span>%s</span></div>'


class TextExpectTest(_Browser):

    def test_the_words_the_button_says_pass(self):
        """CONTROL: STOP equals STOP, and three rows none of which says IDLE."""
        html = page(RUN_BUTTON % "STOP" + "".join(ROW % (t, "RUNNING") for t in ("A", "B", "C")))
        r = self.walk(html, {"text_expect": [
            {"selector": '[data-testid="run"]', "equals": "STOP"},
            {"selector": '[data-testid="run"]', "matches": "STOP$"},
            {"selector": '[data-testid="row"]', "none_contain": "IDLE", "min_count": 3}]})
        self.assertPasses(r)

    def test_other_words_fail(self):
        """A RUN button that still says RUN while the run is live.

        Mutation "equals ignored" (probe.py `_check_text_expect`: the
        `equals` test deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(RUN_BUTTON % "RUN"), {"text_expect": [
            {"selector": '[data-testid="run"]', "equals": "STOP"}]})
        self.assertFailsOnlyFor(r, "reads 'RUN', not 'STOP'")

    def test_a_row_that_says_idle_fails(self):
        """The phone stage list claiming IDLE through a live run (#189 S5).

        Mutation "none_contain never fails" (probe.py `_check_text_expect`:
        `if bad:` -> `if False:`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        html = page("".join(ROW % (t, w) for t, w in (("A", "IDLE"), ("B", "x"), ("C", "y"))))
        r = self.walk(html, {"text_expect": [
            {"selector": '[data-testid="row"]', "none_contain": "IDLE", "min_count": 3}]})
        self.assertFailsOnlyFor(r, "1 of 3 contain 'IDLE'")

    def test_no_rows_is_not_none_saying_idle(self):
        """`min_count`: a list with no rows says nothing, and must not pass as
        a list none of whose rows says IDLE.

        Mutation "min_count ignored" (probe.py `_check_text_expect`: the
        `len(texts) < need` test deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page("<p>no rows here</p>"), {"text_expect": [
            {"selector": '[data-testid="row"]', "none_contain": "IDLE", "min_count": 3}]})
        self.assertFailsOnlyFor(r, "0 visible, need >= 3")

    def test_text_is_the_dom_text_not_the_painted_case(self):
        """`_text_of` reads textContent: the classic buttons upper-case their
        words with CSS, and "(night 2, ...)" must read as the copy wrote it.

        Mutation "innerText" (probe.py `_text_of`: `e.innerText`), observed red:
            AssertionError: False is not true : ['text \'[data-testid="run"]\' reads
            \'CONTINUE (NIGHT 2, 1/80 SUBS)\', not \'CONTINUE (night 2, 1/80 subs)\'']"""
        html = page('<button type="button" data-testid="run" style="text-transform:uppercase">'
                    'CONTINUE (night 2, 1/80 subs)</button>')
        r = self.walk(html, {"text_expect": [
            {"selector": '[data-testid="run"]', "equals": "CONTINUE (night 2, 1/80 subs)"}]})
        self.assertPasses(r)


# ----------------------------------------------------------------- api_text

STAGE = '<div data-testid="stage">%s</div>'
STATE = "/api/sequence/state"


class ApiTextTest(_Browser):
    ROUTE = {"api_text": [{"selector": '[data-testid="stage"]', "get": STATE,
                           "template": "{group.name} {group.panel} \u00b7 pass {group.pass}",
                           "timeout_ms": 1500}]}

    def test_the_engines_panel_passes(self):
        """CONTROL: the page names the panel and pass the engine does."""
        api = {STATE: {"group": {"name": "M31", "panel": "1-2", "pass": 3}}}
        r = self.walk(page(STAGE % "M31 1-2 \u00b7 pass 3"), self.ROUTE, api)
        self.assertPasses(r)
        self.assertEqual(r["api_text"][0]["want"], "M31 1-2 \u00b7 pass 3")

    def test_a_well_formed_wrong_panel_fails(self):
        """The readout names a panel in the right shape, and not the engine's.

        Mutation "api_text compares nothing" (probe.py `_check_api_text`: the
        attempt answers `True`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        api = {STATE: {"group": {"name": "M31", "panel": "1-2", "pass": 3}}}
        r = self.walk(page(STAGE % "M31 2-1 \u00b7 pass 3"), self.ROUTE, api)
        self.assertFailsOnlyFor(r, "and the server says 'M31 1-2")

    def test_a_number_that_only_ends_like_the_engines_fails(self):
        """The page counts 10 / 80 and the engine 0 / 80, and "0 / 80" is
        inside "FRAMES10 / 80" as text; likewise "0 / 800" at the other end.
        A number at either end of the expected text is held whole.

        Mutation "a number matches inside a longer one" (probe.py
        `_check_api_text`: `_holds_text(got, want)` -> `want in got`, the
        code as S56-PROBE left it), observed red (scratchpad
        S56-PROBE-verify-mut, 2026-09-28):
            AssertionError: True is not false : the probe passed a page it must fail"""
        route = {"api_text": [{"selector": '[data-testid="stage"]', "get": STATE,
                               "template": "{progress.frames_done} / {progress.frames_total}",
                               "timeout_ms": 800}]}
        api = {STATE: {"progress": {"frames_done": 0, "frames_total": 80}}}
        for shown in ("FRAMES10 / 80", "FRAMES0 / 800"):
            r = self.walk(page(STAGE % shown), route, api)
            self.assertFailsOnlyFor(r, "and the server says '0 / 80'")
        # CONTROL: the label running into the engine's own count, as the real
        # page draws it.
        self.assertPasses(self.walk(page(STAGE % "FRAMES0 / 80"), route, api))

    def test_the_real_pages_readouts_still_hold(self):
        """CONTROL for the fence: the three texts the real page showed on
        2026-09-28 (S56-PROBE's final report.jsonl), each holding the engine's
        answer; words run into numbers there, and only digits are fenced.

        Mutation "the fence takes words too" (probe.py `_holds_text`: letters
        fenced as well as digits, at both ends always), observed red:
            AssertionError: False is not true : ('STAGEM31 1-1 \\xb7 pass 1',
            'M31 1-1 \\xb7 pass 1')"""
        seen = [("STAGEM31 1-1 \u00b7 pass 1", "M31 1-1 \u00b7 pass 1"),
                ("FRAMES0 / 80", "0 / 80"),
                ("#PANELSHOOTBANKEDPEAK11-1ON0/20no peak1-1: shooting now21-2ON0/20no peak",
                 "1-1: shooting now")]
        for got, want in seen:
            self.assertTrue(probe._holds_text(got, want), (got, want))
        self.assertFalse(probe._holds_text("STAGEM31 1-1 \u00b7 pass 12", "M31 1-1 \u00b7 pass 1"))
        self.assertFalse(probe._holds_text("peak11-1: shooting now", "1-1: shooting now"))

    def test_a_hole_the_server_left_empty_never_passes(self):
        """The engine names no panel (before a group's first visit), and a
        page that prints "None" there must not pass as matching it.

        Mutation "holes filled with str(None)" (probe.py `_render`: the
        missing test deleted, so a None renders as "None"), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        api = {STATE: {"group": {"name": "M31", "panel": None, "pass": 1}}}
        r = self.walk(page(STAGE % "M31 None \u00b7 pass 1"), self.ROUTE, api)
        self.assertFailsOnlyFor(r, "answered no group.panel")

    def test_the_page_and_the_engine_are_read_again_until_they_agree(self):
        """The engine moves on between reads: here it answers 1-1 twice, then
        1-2, the panel the page shows. The check must poll both, not judge
        the first read.

        Mutation "one read only" (probe.py `_poll`: returns after the first
        attempt), observed red:
            AssertionError: False is not true : ['api_text: text
            \'[data-testid="stage"]\' reads \'M31 1-2 \\xb7 pass 1\', and the server
            says \'M31 1-1 \\xb7 pass 1\'']"""
        one = {"group": {"name": "M31", "panel": "1-1", "pass": 1}}
        two = {"group": {"name": "M31", "panel": "1-2", "pass": 1}}
        r = self.walk(page(STAGE % "M31 1-2 \u00b7 pass 1"), self.ROUTE, {STATE: seq(one, one, two)})
        self.assertPasses(r)


# ----------------------------------------------------------------- run_copy

PROG = "/api/flows/f1/progress"
FLOW = "/api/flows/f1"


def progress(status="dormant", nights=1, blocks=((13, 80), (2, 10))):
    return {"flow_id": "f1", "session": {"id": "s1", "status": status, "nights": nights},
            "blocks": [{"banked": b, "total": t} for b, t in blocks]}


class RunCopyTest(_Browser):
    ROUTE = {"run_copy": {"selector": '[data-testid="run"]', "flow": "f1", "night": 2,
                          "min_banked": 1, "timeout_ms": 1500}}

    def walk_copy(self, text: str, prog: dict, route: dict | None = None) -> dict:
        return self.walk(page(RUN_BUTTON % text), route or self.ROUTE,
                         {PROG: prog, FLOW: {"id": "f1", "name": "M31 2x2"}})

    def test_the_routes_numbers_pass(self):
        """CONTROL: night = nights + 1; the counts are EVERY block's banked
        over its total, summed (13 + 2 of 80 + 10); the name in capitals."""
        r = self.walk_copy("CONTINUE M31 2X2 (night 2, 15/90 subs)", progress())
        self.assertPasses(r)
        self.assertEqual(r["run_copy"]["want"]["text"], "CONTINUE M31 2X2 (night 2, 15/90 subs)")

    def test_counts_of_one_block_fail(self):
        """A button that counted the first block only.

        Mutation "blocks not summed" (probe.py `_run_copy_want`: `blocks[:1]`),
        observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk_copy("CONTINUE M31 2X2 (night 2, 13/80 subs)", progress())
        self.assertFailsOnlyFor(r, "and the progress route says 'CONTINUE M31 2X2 (night 2, 15/90 subs)'")

    def test_continue_over_a_session_that_is_not_dormant_fails(self):
        """CONTINUE is only what RUN does over a DORMANT session: over an
        active one the button must not read it, however right its numbers.

        Mutation "status not read" (probe.py `_run_copy_want`: the dormant
        test deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk_copy("CONTINUE M31 2X2 (night 2, 15/90 subs)", progress(status="active"))
        self.assertFailsOnlyFor(r, "names no dormant session")

    def test_the_night_the_walk_expects_is_held(self):
        """A first run's abort continues night 2; a button and a route that
        agree on night 1 are both wrong for this walk.

        Mutation "night not held" (probe.py `_check_run_copy`: the `night`
        test deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk_copy("CONTINUE M31 2X2 (night 1, 15/90 subs)", progress(nights=0))
        self.assertFailsOnlyFor(r, "continues night 1, and the walk expects night 2")

    def test_counts_of_nothing_banked_do_not_show_counts_carry(self):
        """`min_banked`: a button printing 0 for every count must not pass a
        walk that meant to show the counts carrying.

        Mutation "min_banked ignored" (probe.py `_check_run_copy`: the
        `min_banked` test deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk_copy("CONTINUE M31 2X2 (night 2, 0/90 subs)", progress(blocks=((0, 80), (0, 10))))
        self.assertFailsOnlyFor(r, "banked 0 subs")


# -------------------------------------------------------------------- count

class CountTest(_Browser):

    def test_counts(self):
        """CONTROL, then each way a count fails. A view-only sheet has no
        DONE; a disabled fieldset is there; a review of one TARGET has one.

        Mutation "count always ok" (probe.py `_check_count`: `ok = True`),
        observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        html = page('<fieldset disabled><button>ON</button></fieldset>'
                    '<div data-testid="block">M31</div>')
        spec = [{"selector": '[data-testid="done"]', "equals": 0},
                {"selector": "fieldset:disabled", "min": 1},
                {"selector": '[data-testid="block"]', "equals": 1}]
        self.assertPasses(self.walk(html, {"count": spec}))
        two = page('<fieldset><button>ON</button></fieldset><div data-testid="block">A</div>'
                   '<div data-testid="block">B</div><button data-testid="done">DONE</button>')
        r = self.walk(two, {"count": spec})
        self.assertFailsOnlyFor(r, "count '[data-testid=\"done\"]' is 1, need == 0",
                                "count 'fieldset:disabled' is 0, need >= 1",
                                "count '[data-testid=\"block\"]' is 2, need == 1")
        self.assertEqual(len(r["reasons"]), 3)


# ---------------------------------------------------------- forbid_requests

CONFIRM = """<button type="button" data-testid="start-over" onclick="document.getElementById('c').hidden=false">START OVER</button>
<div id="c" hidden><button type="button" data-testid="cancel" onclick="%s">CANCEL</button></div>"""


class ForbiddenTest(_Browser):
    ROUTE = {"click": [{"selector": '[data-testid="start-over"]', "required": True},
                       {"selector": '[data-testid="cancel"]', "required": True}],
             "forbid_requests": [{"method": "POST", "url": "**/api/flows/*/run"}]}

    def test_a_cancel_that_posts_nothing_passes(self):
        """CONTROL: CANCEL closes the confirm and sends nothing."""
        html = page(CONFIRM % "document.getElementById('c').hidden=true")
        self.assertPasses(self.walk(html, self.ROUTE))

    def test_a_cancel_that_starts_the_run_anyway_fails(self):
        """START OVER behind a confirm whose CANCEL still posts the fresh run:
        the page reads the same once it settles.

        Mutation "listener not installed" (probe.py `_Forbidden.__init__`: the
        `page.on("request", ...)` line deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        html = page(CONFIRM % ("fetch('/api/flows/f1/run', {method: 'POST', "
                               "body: JSON.stringify({fresh: true})})"))
        r = self.walk(html, self.ROUTE)
        self.assertFailsOnlyFor(r, "forbidden request made: POST")

    def test_a_forbidden_request_is_graded_even_off_the_view(self):
        """Graded whatever the marker says: here the view marker is missing
        AND the post was made, and both must be said.

        Mutation "forbidden graded with the view checks" (probe.py
        `_run_route`: `if forbidden is not None:` -> `if forbidden is not None
        and marker_ok and testid_ok is not False:`), observed red:
            AssertionError: False is not true : ['testid \'view\' ([data-testid=\'view\'])
            not visible after clicks (click log: [{\'text\': \'[data-testid="start-over"]\',
            \'action\': \'click\', \'matched\': 1}, {\'text\': \'[data-testid="cancel"]\',
            \'action\': \'click\', \'matched\': 1}])']"""
        html = page(CONFIRM % "fetch('/api/flows/f1/run', {method: 'POST', body: '{}'})")
        html = html.replace('data-testid="view"', 'data-testid="not-the-view"')
        r = self.walk(html, self.ROUTE)
        self.assertTrue(any("forbidden request made" in x for x in r["reasons"]), r["reasons"])
        self.assertTrue(any("testid 'view'" in x for x in r["reasons"]), r["reasons"])

    def test_the_glob_is_playwrights(self):
        """`**` any run, `*` one path segment. Mutation "star crosses slashes"
        (probe.py `_glob_re`: `*` -> ".*"), observed red:
            AssertionError: <re.Match object; span=(0, 26), match='http://h/api/flows/a/b/run'> is not None"""
        rx = probe._glob_re("**/api/flows/*/run")
        self.assertIsNotNone(rx.match("http://h/api/flows/abc/run"))
        self.assertIsNotNone(rx.match("http://h/api/flows/abc/run?x=1"))
        self.assertIsNone(rx.match("http://h/api/flows/a/b/run"))
        self.assertIsNone(rx.match("http://h/api/flows/abc/runs"))


# ----------------------------------------------------------------- new_flow

def graph(*types):
    return {"graph": {"nodes": [{"id": f"n{i}", "type": t, "params": {"name": f"T{i}"}}
                                for i, t in enumerate(types)]}}


class NewFlowTest(_Browser):
    ROUTE = {"new_flow": {"target_nodes": 1}}

    def walk_library(self, after_ids, flows) -> dict:
        api = {"/api/flows": seq([{"id": "old"}], [{"id": i} for i in after_ids])}
        api.update({f"/api/flows/{k}": v for k, v in flows.items()})
        return self.walk(page("<p>review</p>"), self.ROUTE, api)

    def test_one_flow_of_one_target_passes(self):
        """CONTROL: the walk saved one flow, and it holds one TARGET."""
        r = self.walk_library(["old", "w1"], {"w1": graph("dusk", "target", "cycle")})
        self.assertPasses(r)
        self.assertEqual((r["new_flow"]["id"], r["new_flow"]["targets"]), ("w1", ["T1"]))

    def test_a_target_per_panel_fails(self):
        """The door that made one TARGET per panel, as the old Plan side
        channel made one Plan target per panel.

        Mutation "targets not counted" (probe.py `_check_new_flow`: the
        `len(targets) != want` test deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk_library(["old", "w1"], {"w1": graph("target", "target", "target", "target")})
        self.assertFailsOnlyFor(r, "has 4 TARGET node(s)")

    def test_two_flows_saved_fail_and_none_saved_fails(self):
        """GENERATE saves ONE flow; two (a double press) or none is wrong.

        Mutation "count of new flows not read" (probe.py `_check_new_flow`: the
        `len(new) != 1` test deleted, the first new id graded), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk_library(["old", "w1", "w2"], {"w1": graph("target"), "w2": graph("target")})
        self.assertFailsOnlyFor(r, "saved 2 flow(s)")
        r = self.walk_library(["old"], {})
        self.assertFailsOnlyFor(r, "saved 0 flow(s)")


# ---------------------------------------------------------- no_plan_targets

class NoPlanTargetsTest(_Browser):

    def test_no_plan_or_an_empty_plan_passes(self):
        """CONTROL: nothing stored, and a stored Plan with no targets."""
        self.assertPasses(self.walk(page("<p>x</p>"), {"no_plan_targets": True}))
        empty = page("<p>x</p>", "localStorage.setItem('astrodeck-plan', JSON.stringify({targets: []}));")
        self.assertPasses(self.walk(empty, {"no_plan_targets": True}))

    def test_a_door_that_still_feeds_the_plan_fails(self):
        """The side channel S6 deleted wrote one Plan target per panel.

        Mutation "plan not read" (probe.py `_check_no_plan_targets`: returns
        ok at once), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        fed = page("<p>x</p>", "localStorage.setItem('astrodeck-plan', JSON.stringify({targets: "
                               "[{name: 'M31 1-1'}, {name: 'M31 1-2'}]}));")
        r = self.walk(fed, {"no_plan_targets": True})
        self.assertFailsOnlyFor(r, "the Plan holds 2 target(s)")


# ------------------------------------------------------------ reachable_all

SHEET = """<div data-testid="sheet" style="position:relative">
<div><button type="button">SAVE</button> <button type="button" style="%(squash)s">ADD STAGE</button></div>
<div style="height:60px;display:flex;align-items:center"><button type="button" id="under">REMOVE</button></div>
%(cover)s</div>"""


class ReachableAllTest(_Browser):
    ROUTE = {"reachable_all": [{"within": '[data-testid="sheet"]', "min_height": 44}]}

    def test_every_button_answering_passes(self):
        """CONTROL: three 44 px buttons, none covered."""
        r = self.walk(page(SHEET % {"squash": "", "cover": ""}), self.ROUTE)
        self.assertPasses(r)
        self.assertEqual(r["reachable_all"][0]["seen"], 3)

    def test_a_covered_button_nobody_named_fails(self):
        """REMOVE under a card nobody thought to name in `reachable`.

        Mutation "hit test ignored" (probe.py `_check_reachable_all`: the
        covered branch deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        cover = ('<div style="position:absolute;left:0;right:0;bottom:0;height:60px;'
                 'background:#fff;z-index:5">a card</div>')
        r = self.walk(page(SHEET % {"squash": "", "cover": cover}), self.ROUTE)
        self.assertFailsOnlyFor(r, "'REMOVE' is covered: the point at its centre is not the control")
        # CONTROL within the case: only REMOVE is under the card.
        self.assertEqual(len(r["reachable_all"][0]["failed"]), 1)

    def test_a_button_squashed_to_a_line_fails(self):
        """+ ADD STAGE at 20 px, which answers the hit test at its centre.

        Mutation "min_height ignored" (probe.py `_check_reachable_all`: the
        height branch deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        squash = "min-height:0;height:20px;overflow:hidden"
        r = self.walk(page(SHEET % {"squash": squash, "cover": ""}), self.ROUTE)
        self.assertFailsOnlyFor(r, "'ADD STAGE' is 20")

    def test_a_container_with_no_buttons_fails(self):
        """Vacuity: nothing inside to grade is not every control reachable."""
        r = self.walk(page('<div data-testid="sheet"><p>no controls</p></div>'), self.ROUTE)
        self.assertFailsOnlyFor(r, "no visible 'button'")


# ------------------------------------------------------ text_intact, own box

NAME = ('<div style="width:300px"><span data-testid="name" style="display:block;width:%s;'
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">M31 2X2 (PROBE PHONE)</span></div>')


class OwnBoxClipTest(_Browser):
    ROUTE = {"text_intact": [{"selector": '[data-testid="name"]'}]}

    def test_a_name_that_fits_passes(self):
        """CONTROL: the same span, wide enough for its words."""
        self.assertPasses(self.walk(page(NAME % "280px"), self.ROUTE))

    def test_a_name_cut_by_its_own_ellipsis_fails(self):
        """The element clips its own text: the ellipsis is a cut too.

        Mutation "walk from the parent" (probe.py TEXT_INTACT_JS: `for (let a =
        el; ...` -> `for (let a = el.parentElement; ...`, the code before
        #189 S5), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(NAME % "60px"), self.ROUTE)
        self.assertFailsOnlyFor(r, "is clipped")


# ---------------------------------------------------- boxes: fills, squashed

FOOT = """<div class="foot" style="display:flex;padding:10px 16px;gap:8px">
 <div class="col" style="display:flex;flex-direction:column;gap:8px;%(col)s">
  <button type="button" data-testid="run" style="width:100%%">RUN</button>
 </div></div>"""
LOG = """<div data-testid="log" style="display:flex;flex-direction:column;max-height:170px;overflow-y:auto;%(h)s">
 <div style="min-height:15px">line one</div><div style="min-height:15px">line two</div>
 %(more)s</div>"""


class BoxesFillAndDepthTest(_Browser):
    FILLS = {"boxes": [{"selector": '[data-testid="run"]', "fills": ".foot"}]}
    DEPTH = {"boxes": [{"selector": '[data-testid="log"]', "unsquashed": True}]}

    def test_a_button_filling_its_footer_passes(self):
        """CONTROL: the column flexes, so the full button spans the footer."""
        r = self.walk(page(FOOT % {"col": "flex:1 1 auto;min-width:0"}), self.FILLS)
        self.assertPasses(r)
        self.assertEqual(r["boxes"][0]["fills"]["width"], r["boxes"][0]["fills"]["content"])

    def test_a_full_button_as_wide_as_its_word_fails(self):
        """The phone stage sheet's footer column with no `flex` (#189 S5): RUN
        92 px in a 358 px footer on the real page.

        Mutation "fills ignored" (probe.py `_check_boxes`: the `fills` block
        deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(FOOT % {"col": ""}), self.FILLS)
        self.assertFailsOnlyFor(r, "does not fill '.foot'")

    def test_a_scroller_showing_its_content_or_at_its_max_passes(self):
        """CONTROL: two lines in a box tall enough, and forty lines in a box
        at its own 170 px max-height (a scroller is allowed to scroll)."""
        self.assertPasses(self.walk(page(LOG % {"h": "", "more": ""}), self.DEPTH))
        many = "".join(f"<div style='min-height:15px'>line {i}</div>" for i in range(40))
        self.assertPasses(self.walk(page(LOG % {"h": "", "more": many}), self.DEPTH))

    def test_a_scroller_squashed_below_its_content_fails(self):
        """The LOG at 17 px holding 32 px of lines, far under its max-height.

        Mutation "unsquashed ignored" (probe.py `_check_boxes`: the
        `unsquashed` block deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(LOG % {"h": "height:17px", "more": ""}), self.DEPTH)
        self.assertFailsOnlyFor(r, "is squashed")


# ------------------------------------------------------------------ aligned

RAIL = """<ol style="display:flex;flex-wrap:wrap;gap:4px 12px;margin:0;padding:8px;list-style:none;
 font:10px sans-serif;width:%(w)s;%(align)s">%(items)s</ol>"""


def rail_html(align: str, width: str = "360px", n_buttons: int = 2) -> str:
    """The wizard's step rail as SendToWizardSheet draws it: a wrapping flex
    row whose steps behind the current one are 32 px buttons."""
    items = "".join(
        f'<li data-testid="rail-{i}">'
        + (f'<button type="button" style="min-height:32px;padding:0;border:0;background:none;'
           f'font:inherit">{i} STEP</button>' if i <= n_buttons else f"<span>{i} STEP</span>")
        + "</li>" for i in range(1, 6))
    return RAIL % {"w": width, "align": align, "items": items}


def rail(align: str, width: str = "360px", n_buttons: int = 2) -> str:
    return page(rail_html(align, width, n_buttons))


class AlignedTest(_Browser):
    ROUTE = {"aligned": [{"selector": '[data-testid^="rail-"] > *', "min_count": 5}]}

    def test_a_rail_on_one_line_passes(self):
        """CONTROL: the items centred on their line."""
        self.assertPasses(self.walk(rail("align-items:center"), self.ROUTE))

    def test_words_below_and_above_the_line_fail(self):
        """The wizard's rail before the fix: two 32 px buttons among plain
        words at the top of a stretched line.

        Mutation "spread not graded" (probe.py `_check_aligned`: `worst =
        max(spreads, default=0.0)` -> `worst = 0.0`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(rail(""), self.ROUTE)
        self.assertFailsOnlyFor(r, "px out of line")

    def test_a_rail_that_wraps_is_graded_line_by_line(self):
        """CONTROL: a rail too narrow for one line wraps, and each line is
        judged alone; two lines are not one line out of line.

        Mutation "one line only" (probe.py `_check_aligned`: every text put in
        a single line), observed red:
            AssertionError: False is not true : ['aligned \'[data-testid^="rail-"] > *\':
            text on one line is 25.5px out of line, need <= 2px ([(\'1 STEP\', 173),
            (\'2 STEP\', 173), (\'3 STEP\', 173), (\'4 STEP\', 198.5), (\'5 STEP\', 198.5)])']"""
        self.assertPasses(self.walk(rail("align-items:center", width="160px"), self.ROUTE))


# --------------------------------------------------------------- the steps

STEPS = """<div id="wiz">%(rail)s</div>
<input data-testid="search" aria-label="Search">
<div id="results"></div>
<button type="button" data-testid="generate" onclick="generate()">GENERATE</button>"""

# GENERATE turns the rail's buttons into words, as the wizard's does once the
# flow is saved; the search shows a result once M31 is typed.
STEPS_SCRIPT = """
const AFTER = %(after)s;
function generate() { document.getElementById('wiz').innerHTML = AFTER; }
document.querySelector('[data-testid=search]').addEventListener('input', (e) => {
  document.getElementById('results').innerHTML = e.target.value === 'M31'
    ? '<button type="button" data-testid="hit">M31</button>' : '';
});"""


class StepsTest(_Browser):

    def steps_page(self) -> str:
        after = json.dumps(rail_html("align-items:center", n_buttons=0))
        return page(STEPS % {"rail": rail_html("")}, STEPS_SCRIPT % {"after": after})

    def test_a_mid_walk_check_grades_the_state_the_walk_leaves(self):
        """The rail is out of line only before GENERATE, which turns its
        buttons into words; checked at the end, it passes.

        Mutation "check step skipped" (probe.py `_run_clicks`: the check branch
        logs and runs nothing), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        route = {"click": [{"check": {"aligned": [{"selector": '[data-testid^="rail-"] > *',
                                                   "min_count": 5}]}},
                           {"selector": '[data-testid="generate"]', "required": True}],
                 "aligned": [{"selector": '[data-testid^="rail-"] > *', "min_count": 5}]}
        r = self.walk(self.steps_page(), route)
        self.assertFailsOnlyFor(r, "mid-walk check at step 1: aligned")
        # CONTROL within the case: the route's own end-of-walk check passed.
        self.assertTrue(r["aligned"][0]["ok"], r["aligned"])

    def test_an_unknown_mid_walk_check_is_a_reason(self):
        """A misspelt check name must not read as a check that passed."""
        r = self.walk(page("<p>x</p>"), {"click": [{"check": {"alinged": []}}]})
        self.assertFailsOnlyFor(r, "unknown mid-walk check 'alinged'")

    def test_fill_types_as_a_person_does(self):
        """The result exists only once M31 is typed.

        Mutation "fill types nothing" (probe.py `_run_clicks`: `matches[0].fill`
        call deleted), observed red:
            AssertionError: False is not true : ['required step \'[data-testid="hit"]\'
            failed: no visible match within 1500 ms']"""
        route = {"click": [{"fill": '[data-testid="search"]', "value": "M31"},
                           {"selector": '[data-testid="hit"]', "required": True, "wait_ms": 1500}]}
        r = self.walk(self.steps_page(), route)
        self.assertPasses(r)
        self.assertEqual([s["action"] for s in r["click_log"]], ["fill", "click"])
        route["click"][0]["value"] = "M3"
        r = self.walk(self.steps_page(), route)
        self.assertFailsOnlyFor(r, "required step '[data-testid=\"hit\"]' failed")

    def test_a_shot_step_saves_its_evidence(self):
        """Mutation "shot step ignored" (probe.py `_run_clicks`: the shot branch
        skips the screenshot), observed red:
            AssertionError: False is not true :
            C:\\Users\\bear\\AppData\\Local\\Temp\\tmpwmny23xw\\390\\fixture-confirm.png"""
        r = self.walk(page("<p>x</p>"), {"click": [{"shot": "confirm"}]})
        self.assertPasses(r)
        path = Path(self.tmp.name) / "390" / "fixture-confirm.png"
        self.assertTrue(path.is_file(), str(path))

    def test_wait_api_waits_for_the_server(self):
        """The first banked sub arrives on the third read.

        Mutation "wait_api reads once" (probe.py `_wait_api`: `_poll(page, 0,
        attempt)`), observed red:
            AssertionError: False is not true : ["required step 'wait_api
            /api/flows/f1/progress blocks.0.banked' failed: the server still
            says 0 after 3000 ms"]"""
        api = {PROG: seq({"blocks": [{"banked": 0}]}, {"blocks": [{"banked": 0}]},
                         {"blocks": [{"banked": 1}]})}
        step = {"wait_api": {"get": PROG, "field": "blocks.0.banked", "min": 1}, "wait_ms": 3000}
        r = self.walk(page("<p>x</p>"), {"click": [step]}, api)
        self.assertPasses(r)
        self.assertEqual(r["click_log"][0]["value"], 1)

    def test_wait_api_that_never_sees_it_fails_the_walk(self):
        """Mutation "wait_api not required" (probe.py `_run_clicks`: a timed-out
        wait_api logged as `seen`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        api = {PROG: {"blocks": [{"banked": 0}]}}
        step = {"wait_api": {"get": PROG, "field": "blocks.0.banked", "min": 1}, "wait_ms": 800}
        r = self.walk(page("<p>x</p>"), {"click": [step]}, api)
        self.assertFailsOnlyFor(r, "the server still says 0")


# --------------------------------------------------------------------- seed

GRAPH = {"nodes": [{"id": "n2", "type": "target", "params": {"name": "M31", "rows": 2, "cols": 2}},
                   {"id": "n7", "type": "cycle", "params": {"plan": "L 10"}}],
         "edges": [], "settings": {}}


class SeedSaveFlowTest(_Browser):

    def seed(self, progress_session=None, engine="idle", unmapped=(), **spec):
        stored = {"id": "p1", "name": "M31 2x2", "readonly": False, "graph": GRAPH}
        Handler.state = {"api": {"/api/flows/p1": stored,
                                 "/api/flows/p1/progress": {"session": progress_session},
                                 "/api/sequence/state": {"state": engine}},
                         "post_api": {"/api/flows/p1/compile": (200, {"unmapped": list(unmapped)})}}
        op = {"save_flow": {"id": "p1", "name": "M31 2x2", "graph": GRAPH,
                            "expect_node": {"id": "n2", "type": "target", "min_panels": 4},
                            **spec}}
        ctx = self.browser.new_context()
        try:
            return probe._seed(ctx.request, self.base, [op])
        finally:
            ctx.close()

    def test_a_drawn_flow_is_saved_and_read_back(self):
        """CONTROL: POST /api/flows with the route's graph, then read back."""
        log = self.seed(fresh=True, runnable=True, unmapped=[{"level": "note", "detail": "cool"}])
        self.assertEqual(log, [{"save_flow": "p1", "readonly": False}])
        path, body = Handler.state["posts"][0]
        self.assertEqual((path, json.loads(body)["flow"]["graph"]), ("/api/flows", GRAPH))

    def test_a_server_that_already_holds_a_session_refuses(self):
        """RUN would read CONTINUE before the walk pressed anything.

        Mutation "fresh ignores the session" (probe.py `_seed_save_flow`: the
        session test deleted), observed red:
            AssertionError: SeedError not raised"""
        with self.assertRaises(probe.SeedError) as caught:
            self.seed(progress_session={"id": "s1", "status": "dormant"}, fresh=True)
        self.assertIn("already holds a session", str(caught.exception))

    def test_an_engine_that_is_running_refuses(self):
        """RUN would be refused "already running".

        Mutation "fresh ignores the engine" (probe.py `_seed_save_flow`: the
        engine test deleted), observed red:
            AssertionError: SeedError not raised"""
        with self.assertRaises(probe.SeedError) as caught:
            self.seed(engine="running", fresh=True)
        self.assertIn("the engine is 'running'", str(caught.exception))

    def test_a_compile_that_lists_a_loss_refuses_and_a_note_does_not(self):
        """RUN would stop on a question the walk does not answer (a TARGET
        framed for another camera's field is M5's loss); a note is not asked.

        Mutation "runnable ignored" (probe.py `_seed_save_flow`: the runnable
        block deleted), observed red:
            AssertionError: SeedError not raised
        Mutation "a note refuses too" (the `level != "note"` filter deleted),
        observed red in the control, test_a_drawn_flow_is_saved_and_read_back:
            probe.SeedError: 'p1' would not run without a question on this server: 'cool'"""
        loss = {"level": "warn", "detail": "framed for 1.35 x 0.90 deg; this camera now images 0.49 x 0.37 deg"}
        with self.assertRaises(probe.SeedError) as caught:
            self.seed(runnable=True, unmapped=[loss])
        self.assertIn("would not run without a question", str(caught.exception))

    def test_a_seed_op_runs_only_at_its_widths(self):
        """The phone's flow is seeded before the phone walks only: seeded again
        before the desktop's, `fresh` would find the phone's own session.

        Mutation "widths ignored" (probe.py `_seed_for_width` returns every
        op), observed red:
            AssertionError: Lists differ: ['a', 'b', 'c'] != ['a', 'c']"""
        ops = [{"widths": [390], "save_flow": {"id": "a"}},
               {"widths": [1440], "save_flow": {"id": "b"}},
               {"save_flow": {"id": "c"}}]
        ids = lambda w: [op["save_flow"]["id"] for op in probe._seed_for_width(ops, w)]
        self.assertEqual(ids(390), ["a", "c"])
        self.assertEqual(ids(1440), ["b", "c"])


class RequireSimTest(_Browser):
    """`require_sim`: these walks press RUN, and must never press it on a
    server that drives a real rig."""

    def seed_status(self, answer):
        Handler.state = {"api": {"/api/status": answer} if answer is not None else {}}
        ctx = self.browser.new_context()
        try:
            return probe._seed(ctx.request, self.base, [{"require_sim": True}])
        finally:
            ctx.close()

    def test_the_simulator_passes(self):
        """CONTROL: the rig `server_ctl.py start` connects."""
        self.assertEqual(self.seed_status({"mode": "sim", "connected": {}}),
                         [{"require_sim": True, "mode": "sim"}])

    def test_a_real_rig_refuses_and_quotes_only_its_mode(self):
        """A server on real hardware (a native profile reads "alpaca"). The
        refusal names the mode and nothing else of the status: the body can
        carry the site, and a refusal is printed.

        Mutation "sim not required" (probe.py `_seed_require_sim`: the
        `mode != "sim"` test deleted), observed red (scratchpad
        S56-PROBE-verify-mut, 2026-09-28):
            AssertionError: SeedError not raised
        Mutation "the refusal quotes the status" (the body appended to the
        refusal), observed red:
            AssertionError: '12.3456' unexpectedly found in "these walks press RUN,
            and this server's rig is mode='alpaca', not the simulator: ... ({'mode':
            'alpaca', 'site': {'lat': 12.3456, 'lon': -65.4321}, 'connected': {}})"
        """
        made_up = {"lat": 12.3456, "lon": -65.4321}   # not a site: an invented value
        with self.assertRaises(probe.SeedError) as caught:
            self.seed_status({"mode": "alpaca", "site": made_up, "connected": {}})
        self.assertIn("mode='alpaca'", str(caught.exception))
        self.assertNotIn("12.3456", str(caught.exception))

    def test_a_status_that_says_nothing_refuses(self):
        """A 500, and a path that answers no JSON: nothing saying the rig is
        the simulator is not the simulator.

        Mutation "a silent status passes" (probe.py `_seed_require_sim`: a
        body that is not a dict returns as passed), observed red:
            AssertionError: SeedError not raised"""
        with self.assertRaises(probe.SeedError) as caught:
            self.seed_status([(500, {"detail": "boom"})])
        self.assertIn("-> 500", str(caught.exception))
        with self.assertRaises(probe.SeedError) as caught:
            self.seed_status(None)
        self.assertIn("could not be read", str(caught.exception))


# ------------------------------------------------------------------ console

class ConsoleSafeTest(unittest.TestCase):

    def test_a_reason_the_console_cannot_encode_is_printed(self):
        """The first walk of routes_s5_s6.json died printing a reason that
        quoted the page's warning sign to a cp1252 console.

        Mutation "streams left strict" (probe.py `_console_safe`: body is
        `pass`), observed red:
            UnicodeEncodeError: 'charmap' codec can't encode character '\\u26a0' in
            position 0: character maps to <undefined>"""
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        probe._console_safe(stream)
        print("\u26a0 Survey unreachable", file=stream)
        stream.flush()
        self.assertEqual(raw.getvalue().decode("cp1252").strip(), "\\u26a0 Survey unreachable")


# --------------------------------------------------------------- route file

class RouteFileTest(unittest.TestCase):
    """What the acceptance asks of routes_s5_s6.json, held so an edit that
    drops it is red here rather than a quieter probe."""

    def test_every_walk_asserts_a_view_marker_through_doors(self):
        """Every route names a data-testid; every click step but the atlas
        toggle (skipped when the atlas is already off, its premise stated by
        the next step) is required or clicks nothing.

        Mutation "a walk without its marker" (routes_s5_s6.json:
        s5-continue-phone's "testid" deleted), observed red:
            AssertionError: unexpectedly None : s5-continue-phone"""
        for r in _route_file()["routes"]:
            self.assertIsNotNone(r.get("testid"), r["name"])
            self.assertIn(r["widths"], ([390], [1440]), r["name"])
            for s in r["click"]:
                optional = s.get("selector", "").startswith('[data-testid="sky-atlas-mode"]')
                acts = {"goto", "wait_for", "fill", "shot", "check", "wait_api"} & set(s)
                self.assertTrue(optional or acts or s.get("required"), (r["name"], s))

    def test_the_run_walks_hold_the_page_to_the_rig(self):
        """(a): RUN by its arm and CONFIRM, the readouts against the engine,
        RUN reading STOP, no stage claiming IDLE; the modal view-only with
        the engine's panel shooting, on both widths.

        Mutation "the readouts not held to the engine" (routes_s5_s6.json:
        s5-run-phone's api_text deleted), observed red:
            AssertionError: None is not true : s5-run-phone"""
        run = _route("s5-run-phone")
        self.assertTrue(run.get("api_text"), "s5-run-phone")
        self.assertIn("{group.panel}", run["api_text"][0]["template"])
        self.assertIn("pass {group.pass}", run["api_text"][0]["template"])
        expects = {json.dumps(t, sort_keys=True) for t in run["text_expect"]}
        self.assertIn(json.dumps({"selector": '[data-testid="flow-stages-run"]', "equals": "STOP"},
                                 sort_keys=True), expects)
        for name in ("s5-frame-running-phone", "s5-frame-running-desktop"):
            r = _route(name)
            self.assertEqual(r["testid"], "target-framing-sheet", name)
            self.assertEqual(r["api_text"][0]["template"], "{group.panel}: shooting now", name)
            self.assertIn({"selector": '[data-testid="framing-done"]', "equals": 0}, r["count"])

    def test_the_continue_walks_hold_continue_and_the_confirm(self):
        """(b): CONTINUE with the route's numbers on night 2 with a sub banked,
        START OVER opened then CANCELled, and no run request sent.

        Mutation "the confirm not guarded" (routes_s5_s6.json:
        s5-continue-desktop's forbid_requests deleted), observed red:
            AssertionError: None != [{'method': 'POST', 'url': '**/api/flows/*/run'}]"""
        for name, flow in (("s5-continue-phone", "probe-s5-phone"),
                           ("s5-continue-desktop", "probe-s5-desk")):
            r = _route(name)
            self.assertEqual(r.get("forbid_requests"),
                             [{"method": "POST", "url": "**/api/flows/*/run"}])
            self.assertEqual((r["run_copy"]["flow"], r["run_copy"]["night"],
                              r["run_copy"]["min_banked"]), (flow, 2, 1), name)
            steps = json.dumps(r["click"])
            self.assertLess(steps.index("start-over"), steps.index("Start this flow over?"), name)
            self.assertIn("CANCEL" if "desktop" in name else "confirm-keep", r["click"][-1]["selector"])

    def test_the_wizard_walks_hold_one_target_and_no_plan_targets(self):
        """(c): Sky FRAME and the classic Atlas, at both widths, each ending on
        a review of one block, one saved flow of one TARGET, no Plan targets.

        Mutation "the Plan not graded" (routes_s5_s6.json:
        s6-atlas-wizard-desktop's no_plan_targets deleted), observed red:
            AssertionError: None is not True : s6-atlas-wizard-desktop"""
        names = ["s6-sky-wizard-phone", "s6-atlas-wizard-phone",
                 "s6-atlas-wizard-desktop", "s6-sky-wizard-desktop"]
        for name in names:
            r = _route(name)
            self.assertEqual(r["testid"], "send-to-wizard-sheet", name)
            self.assertEqual(r.get("new_flow"), {"target_nodes": 1}, name)
            self.assertIs(r.get("no_plan_targets"), True, name)
            self.assertIn({"selector": '[data-testid="wizard-review-block"]', "equals": 1}, r["count"])
        doors = {name: json.dumps(_route(name)["click"]) for name in names}
        self.assertTrue(all("sky-send-to-wizard" in doors[n] for n in names if "sky" in n))
        self.assertTrue(all("atlas-send-to-wizard" in doors[n] for n in names if "atlas" in n))

    def test_the_seeds_are_fresh_runnable_and_one_per_width(self):
        """Each flow is saved at its own width only, refuses a server that
        would make the walk other than its first run, and each walk names its
        own width's flow.

        Mutation "the phone seed at every width" (routes_s5_s6.json: the first
        seed's "widths" deleted), observed red:
            AssertionError: Lists differ: [None, [1440]] != [[390], [1440]]"""
        seeds = [op for op in _route_file()["seed"] if "save_flow" in op]
        self.assertEqual([op.get("widths") for op in seeds], [[390], [1440]])
        for op in seeds:
            self.assertIs(op["save_flow"]["fresh"], True)
            self.assertIs(op["save_flow"]["runnable"], True)
        for r in _route_file()["routes"]:
            if r["name"].startswith("s5-"):
                flow = "probe-s5-phone" if r["widths"] == [390] else "probe-s5-desk"
                self.assertIn(flow, json.dumps(r["click"]) + json.dumps(r.get("run_copy", {})),
                              r["name"])

    def test_the_walks_refuse_a_rig_that_is_not_the_simulator(self):
        """These walks press RUN, so the first seed op is require_sim, and it
        runs at every width, before either flow is saved.

        Mutation "the route file trusts its caller" (routes_s5_s6.json: the
        require_sim op deleted), observed red:
            AssertionError: {'widths': [390], 'save_flow': {'id': 'prob[895 chars]rue}}
            != {'require_sim': True}"""
        seeds = _route_file()["seed"]
        self.assertEqual(seeds[0], {"require_sim": True})
        for width in (390, 1440):
            self.assertEqual(probe._seed_for_width(seeds, width)[0], {"require_sim": True})

    def test_the_route_file_is_ascii(self):
        """The house rule for source files, and the file quotes no site value:
        the TARGET's coordinates are M31's catalogue position."""
        raw = ROUTES_PATH.read_bytes()
        self.assertTrue(all(b < 128 for b in raw))


if __name__ == "__main__":
    unittest.main()
