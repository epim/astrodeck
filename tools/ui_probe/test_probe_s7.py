# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Self-tests for the S7 probe (#189 S7 item 1b, routes_s7.json). Run with the
probe's Playwright Python, like test_probe_s5_s6.py:

    cd tools/ui_probe && python -m unittest test_probe_s7

No AstroDeck server, no UI build and no rig: every page is a loopback fixture,
and every API answer a check reads is the fixture's. (One case, which runs
seed_session.py itself, needs the server's venv and skips, saying so, without
it: that script imports the server's own store.) Each check has a CONTROL, a
page it must pass, so the negative cases cannot pass on a check no page could
satisfy, and a case for the thing it exists to catch, naming the mutation of
probe.py (or server_ctl.py, or seed_session.py) that turns it red and the
failure observed when that mutation was run in a private copy of this folder
(scratchpad S7-PROBE-mut, 2026-09-28; the runner, mutate.py, and every
verbatim output are kept there).

The acceptance's three named mutants are "readouts check always passes"
(ReadoutsTest), "phone walk without touch" (TouchTest) and "port 8800
accepted" (RigPortTest, once in probe.py and once in server_ctl.py).

Mosaic slice H4 takes every browser here from `probe._Browsers` (#535), and
re-pinned the seed_session cases to the probe marker H4-PROBE-GUARD made the
script require (#539): each marks the temporary directories it passes.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import calendar
import contextlib
import copy
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

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
        f"`cd tools/ui_probe && python -m unittest test_probe_s7`.")

import probe  # noqa: E402
import seed_session  # noqa: E402
import server_ctl  # noqa: E402

ROUTES_PATH = HERE / "routes_s7.json"
REPO_ROOT = HERE.parents[1]
#: The environment of the venv-python children below: this checkout's server/
#: first on PYTHONPATH, because the venv's editable install names the main
#: checkout and a child run from a worktree would otherwise import that one
#: (#915). seed_session.py pins its own tree; the -c snippets do not.
CHILD_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(
    [str(REPO_ROOT / "server")]
    + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])}


def _resolve_venv_python() -> Path:
    """The interpreter for SeedSessionScriptTest's two end-to-end cases below.

    A wave worktree gets a `ui/node_modules` junction but no `server/.venv` of
    its own, so the repo-relative default always pointed at a venv that was
    not there, and both cases silently SKIPPED in every wave run and the
    integration worktree (#653) -- the only cases that run seed_session.py,
    the script that arms a dormant session for unattended auto-resume, end to
    end against a real store. ASTRODECK_PROBE_VENV_PYTHON lets the wave
    tooling (and a post-merge check in the main tree) point this file at a
    venv that exists, while `cd tools/ui_probe && python -m unittest
    test_probe_s7` in the main tree keeps resolving the same path as before.
    """
    override = os.environ.get("ASTRODECK_PROBE_VENV_PYTHON")
    if override:
        return Path(override)
    return REPO_ROOT / "server" / ".venv" / "Scripts" / "python.exe"


VENV_PYTHON = _resolve_venv_python()
SCENARIOS = {
    "1": ["s7-rot-run-phone", "s7-rot-frame-phone", "s7-rot-classic-phone",
          "s7-rot-classic-desktop"],
    "2": ["s7-fault-phone", "s7-fault-desktop"],
    "3": ["s7-cont-phone", "s7-cont-desktop"],
    "4": ["s7-merid-phone", "s7-merid-desktop"],
}


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
    """The page is `state["page"]`. A GET of an API path answers
    `state["api"][path]`: a plain body (200), a list of (status, body)
    consumed in order with the last repeating, or a callable given the number
    of GETs of that path so far (a run that moves between two reads). PUTs
    and POSTs are recorded in `state["writes"]` and answered from
    `state["write_api"]` ((status, body) or a callable given the body), 200 {}
    by default."""
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
            counts = self.state.setdefault("gets", {})
            n = counts[path] = counts.get(path, 0) + 1
            answer = api[path]
            if callable(answer):
                return self._json(*answer(n))
            if isinstance(answer, list) and answer and isinstance(answer[0], tuple):
                status, body = answer[0] if len(answer) == 1 else answer.pop(0)
                return self._json(status, body)
            return self._json(200, answer)
        return self._send(200, b"", "text/plain")

    def _write(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        path = self.path.split("?")[0]
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            body = raw.decode("utf-8", "replace")
        self.state.setdefault("writes", []).append((self.command, path, body))
        answer = self.state.get("write_api", {}).get((self.command, path), (200, {}))
        return self._json(*(answer(body) if callable(answer) else answer))

    do_POST = _write
    do_PUT = _write


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

    def setUp(self):
        probe._MEMO.clear()

    def walk(self, html: str, route: dict, api: dict | None = None, width: int = 390,
             write_api: dict | None = None) -> dict:
        """One route on the fixture, in the context the probe itself builds
        for `width` (`probe._new_context`), on the browser it launches for
        that width (`probe._Browsers`), so a profile that lost its touch, or a
        desktop browser that hid its scrollbars, is what these cases walk in."""
        Handler.state = {"page": html, "api": api or {}, "write_api": write_api or {}}
        route = {"name": "fixture", "url": "#/", "testid": "view", **route}
        ctx = self.browsers.context(width)
        try:
            return probe._run_isolated_route(ctx, self.base, route, Path(self.tmp.name), width)
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


# ---------------------------------------------------------------- readouts

READOUT = ('<div data-testid="stage" class="tile"><span class="lab">STAGE</span>'
           '<span class="val">%s</span></div>')


def state_at(panel: str, pass_: int = 2, session: str = "s1", frames: int = 7) -> dict:
    return {"state": "running", "session": {"id": session},
            "group": {"name": "M31", "panel": panel, "pass": pass_, "meridian_wait": False,
                      "set_aside": []},
            "progress": {"frames_done": frames, "frames_total": 80}}


def progress_of(session: str = "s1") -> dict:
    return {"flow_id": "f", "session": {"id": session, "status": "active", "nights": 1},
            "blocks": [{"banked": 7, "total": 80}]}


STAGE_FIELD = {"selector": '[data-testid="stage"] .val',
               "template": "{group.name} {group.panel} \u00b7 pass {group.pass}"}


def readouts(**kw) -> dict:
    spec = {"flow": "f", "same_session": True, "timeout_ms": 1500, "fields": [STAGE_FIELD]}
    spec.update(kw)
    return {"readouts": spec}


class ReadoutsTest(_Browser):

    def api(self, state, progress=None) -> dict:
        return {"/api/sequence/state": state, "/api/flows/f/progress": progress or progress_of()}

    def test_a_page_equal_to_both_route_reads_passes(self):
        """CONTROL: STAGE reads exactly what the state says, the state does not
        move, and the rig's session is the flow's."""
        r = self.walk(page(READOUT % "M31 1-2 \u00b7 pass 2"), readouts(),
                      self.api(state_at("1-2")))
        self.assertPasses(r)
        row = r["readouts"]["rows"][0]
        self.assertEqual((row["first"], row["second"], row["dom"]),
                         ("M31 1-2 \u00b7 pass 2",) * 3)

    def test_a_well_formed_readout_the_routes_do_not_say_fails(self):
        """The page names 2-1 while the engine is on 1-2: shaped right, false.

        Mutation "readouts check always passes" (probe.py `_readouts_attempt`:
        its last line returns `True, info`), observed red (scratchpad
        S7-PROBE-mut, 2026-09-28):
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(READOUT % "M31 2-1 \u00b7 pass 2"), readouts(),
                      self.api(state_at("1-2")))
        self.assertFailsOnlyFor(r, "readouts:")
        self.assertIn("reads 'M31 2-1 \u00b7 pass 2', and state says 'M31 1-2 \u00b7 pass 2'",
                      r["reasons"][0])

    def test_the_dom_must_equal_the_routes_not_merely_hold_them(self):
        """"pass 2" is inside "pass 21", and a label run into the value holds
        the value too: equality is the rule unless a field asks for `holds`,
        and `holds` still fences digits (`_holds_text`).

        Mutation "equal read as contains" (`text == want_1` made
        `want_1 in text`), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(READOUT % "M31 1-2 \u00b7 pass 21"), readouts(),
                      self.api(state_at("1-2")))
        self.assertFailsOnlyFor(r, "readouts:")
        held = readouts(fields=[{"selector": '[data-testid="stage"]', "match": "holds",
                                 "template": STAGE_FIELD["template"]}])
        self.assertPasses(self.walk(page(READOUT % "M31 1-2 \u00b7 pass 2"), held,
                                    self.api(state_at("1-2"))))
        self.assertFailsOnlyFor(self.walk(page(READOUT % "M31 1-2 \u00b7 pass 21"), held,
                                          self.api(state_at("1-2"))), "readouts:")

    def test_two_route_reads_that_disagree_are_never_one_moment(self):
        """The state moves on every GET (1-2, 2-2, 1-2, ...), so each reading's
        first and second route read disagree, and the page, which says 1-2,
        agrees with only one of them each time. The check must not pass on
        the first read alone, however often it is asked.

        Mutation "routes read once" (`_readouts_attempt` renders the second
        read from `before` too), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        moving = lambda n: (200, state_at("1-2" if n % 2 else "2-2"))  # noqa: E731
        r = self.walk(page(READOUT % "M31 1-2 \u00b7 pass 2"), readouts(), self.api(moving))
        self.assertFailsOnlyFor(r, "readouts:")
        self.assertIn("disagree across the DOM read", r["reasons"][0])

    def test_the_page_is_read_between_the_two_route_reads(self):
        """ONE evaluation, in order: the state answers 1-2 to the first GET of
        each reading and 2-2 to the second, and the page swaps its text from
        1-2 to 2-2 on the first fetch it sees. Read between the two GETs, the
        page says 2-2; a probe that read the DOM before the first GET would
        see 1-2 and report that. Either way the reads disagree and it fails;
        what this holds is WHICH text it read, the one that was on screen
        between them.

        Mutation "DOM read before the routes" (READOUTS_JS: `dom` computed
        before `before`), observed red:
            AssertionError: 'M31 1-2 \u00b7 pass 2' != 'M31 2-2 \u00b7 pass 2'"""
        moving = lambda n: (200, state_at("1-2" if n % 2 else "2-2"))  # noqa: E731
        swap = ("const f0 = window.fetch; window.fetch = function (u, o) {"
                " if (String(u).includes('sequence/state')) {"
                " document.querySelector('[data-testid=stage] .val').textContent ="
                " 'M31 2-2 \u00b7 pass 2'; }"
                " return f0.call(this, u, o); };")
        r = self.walk(page(READOUT % "M31 1-2 \u00b7 pass 2", swap),
                      readouts(timeout_ms=0), self.api(moving))
        self.assertFalse(r["passed"])
        self.assertEqual(r["readouts"]["rows"][0]["dom"], "M31 2-2 \u00b7 pass 2")

    def test_a_readout_of_another_session_fails(self):
        """The rig runs another session than the one the flow's progress route
        counts: a readout equal to that run describes someone else's ledger.

        Mutation "session not compared" (the `same_session` block deleted),
        observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(READOUT % "M31 1-2 \u00b7 pass 2"), readouts(),
                      self.api(state_at("1-2", session="other")))
        self.assertFailsOnlyFor(r, "not one session")

    def test_a_hole_the_route_left_empty_never_passes(self):
        """No panel yet (null): "M31  \u00b7 pass 2" must not be rendered and
        compared, and a page that prints the word None must not pass."""
        st = state_at("1-2")
        st["group"]["panel"] = None
        r = self.walk(page(READOUT % "M31 None \u00b7 pass 2"), readouts(), self.api(st))
        self.assertFailsOnlyFor(r, "answered no group.panel")

    def test_what_the_walk_requires_of_the_routes_is_held_in_both_reads(self):
        """`require`: the straddle's wait readout is graded only while the state
        says the group waits on the meridian, in both reads.

        Mutation "require ignored" (the `require` loop deleted), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        wait = {"selector": '[data-testid="stage"] .val',
                "template": "{group.name} \u00b7 waiting for the meridian"}
        spec = readouts(fields=[wait], require={"state.group.meridian_wait": True})
        html = page(READOUT % "M31 \u00b7 waiting for the meridian")
        self.assertFailsOnlyFor(self.walk(html, spec, self.api(state_at("1-2"))),
                                "state.group.meridian_wait")
        st = state_at("1-2")
        st["group"]["meridian_wait"] = True
        self.assertPasses(self.walk(html, spec, self.api(st)))

    def test_containing_picks_the_row_that_says_it(self):
        """Two PANELS rows, a set-aside one first: `containing` grades the row
        that says "shooting now", as `:has-text` did for a locator, and the
        progress route can feed a field (the replay line's date filter)."""
        rows = ('<div data-testid="row">set aside tonight: centring failed on 2-2</div>'
                '<div data-testid="row">1-2: shooting now</div>'
                '<div data-testid="line">saved 2026-09-27</div>')
        prog = progress_of()
        prog["session"]["plan_saved_ts"] = time.mktime((2026, 9, 27, 21, 0, 0, 0, 0, -1))
        spec = readouts(fields=[
            {"selector": '[data-testid="row"]', "containing": "shooting now",
             "template": "{group.panel}: shooting now"},
            {"selector": '[data-testid="line"]', "from": "progress",
             "template": "saved {session.plan_saved_ts|localdate}"}])
        self.assertPasses(self.walk(page(rows), spec, self.api(state_at("1-2"), prog)))

    def test_a_route_that_answers_an_error_fails_in_its_words(self):
        """A 503 from the state route is a reading that says nothing: it fails,
        naming the route and the status, and never compares the page with an
        error body. (The browser's own console line for the 503 is expected
        here, so the reason left is the check's.)"""
        route = {**readouts(), "expected_errors": ["status of 503", "/api/sequence/state"]}
        r = self.walk(page(READOUT % "M31 1-2 \u00b7 pass 2"), route,
                      {"/api/sequence/state": [(503, {"detail": "down"})],
                       "/api/flows/f/progress": progress_of()})
        self.assertFailsOnlyFor(r, "GET /api/sequence/state -> 503")

    def test_no_fields_is_no_claim(self):
        self.assertFailsOnlyFor(self.walk(page(READOUT % "x"), readouts(fields=[]),
                                          self.api(state_at("1-2"))), "no fields")

    def test_the_real_pages_readouts_hold(self):
        """The phone stage sheet's STAGE tile as ReadoutTile draws it (label
        and value spans), read through the value span the route file names."""
        tile = ('<div class="nx-readout" data-testid="flow-stages-stage">'
                '<span class="nx-readout-label">STAGE</span>'
                '<span class="nx-readout-value">M31 1-2 \u00b7 pass 2</span></div>')
        field = _route("s7-rot-run-phone")["readouts"]["fields"][0]
        spec = readouts(fields=[field])
        self.assertPasses(self.walk(page(tile), spec, self.api(state_at("1-2"))))


# -------------------------------------------------------------------- touch

TAP_PAGE = ('<button type="button" data-testid="go" '
            'onclick="document.getElementById(\'how\').textContent = event.pointerType">GO</button>'
            '<span id="how" data-testid="how">none</span>')


class TouchTest(_Browser):

    def test_the_phone_walk_is_a_touch_screen_and_taps(self):
        """CONTROL and the case: at 390 the probe's own context reports a touch
        screen, and a step on a `touch` route is a tap, which the page sees as
        a touch pointer.

        Mutation "phone walk without touch" (probe.py WIDTH_PROFILES[390]
        `has_touch` False), observed red (scratchpad S7-PROBE-mut,
        2026-09-29):
            AssertionError: False is not true : ["touch: this is a phone walk and
            the page reports no touch screen ({'max_touch_points': 0,
            'touch_events': False, 'coarse': False}): its taps would be a
            desktop's clicks", 'required step \\'[data-testid="go"]\\' failed:
            Locator.tap: The page does not support tap. Use hasTouch context
            option to enable touch support.', 'text \\'[data-testid="how"]\\'
            reads \\'none\\', not \\'touch\\'']"""
        r = self.walk(page(TAP_PAGE), {"touch": True,
                                       "click": [{"selector": '[data-testid="go"]', "required": True}],
                                       "text_expect": [{"selector": '[data-testid="how"]',
                                                        "equals": "touch"}]})
        self.assertPasses(r)
        self.assertEqual(r["click_log"][0]["action"], "tap")
        self.assertTrue(r["touch"]["ok"])

    def test_a_touch_route_is_tapped_not_clicked(self):
        """In a touch context, a click is a mouse's: the page sees "mouse".

        Mutation "clicks on a touch route" (`_run_route` passes `tap=False`),
        observed red:
            AssertionError: False is not true : ['text \\'[data-testid="how"]\\'
            reads \\'mouse\\', not \\'touch\\'']"""
        r = self.walk(page(TAP_PAGE), {"touch": True,
                                       "click": [{"selector": '[data-testid="go"]', "required": True}],
                                       "text_expect": [{"selector": '[data-testid="how"]',
                                                        "equals": "touch"}]})
        self.assertPasses(r)

    def test_a_desktop_walk_is_not_a_touch_screen(self):
        """CONTROL at 1440: `touch: false` passes, and the click is a mouse's."""
        r = self.walk(page(TAP_PAGE), {"touch": False,
                                       "click": [{"selector": '[data-testid="go"]', "required": True}],
                                       "text_expect": [{"selector": '[data-testid="how"]',
                                                        "equals": "mouse"}]}, width=1440)
        self.assertPasses(r)

    def test_a_phone_walk_in_a_desktop_context_fails_by_name(self):
        """A route that says it is on a touch screen, walked at 1440, fails on
        `touch` before any tap, naming it."""
        r = self.walk(page(TAP_PAGE), {"touch": True}, width=1440)
        self.assertFailsOnlyFor(r, "touch: this is a phone walk")

    def test_every_phone_walk_in_the_route_file_is_a_touch_walk(self):
        """Every 390 route says `touch: true` and every 1440 route `touch:
        false`, and the phone profile the probe builds has touch.

        Mutation "a phone walk without touch in the file" (routes_s7.json:
        s7-fault-phone's "touch" deleted), observed red:
            AssertionError: None is not True : s7-fault-phone"""
        self.assertTrue(probe.WIDTH_PROFILES[390]["has_touch"])
        self.assertTrue(probe.WIDTH_PROFILES[390]["is_mobile"])
        self.assertEqual(probe.WIDTH_PROFILES[390]["height"], 844)
        self.assertEqual(probe.WIDTH_PROFILES[1440]["height"], 900)
        for r in _route_file()["routes"]:
            if r["widths"] == [390]:
                self.assertIs(r.get("touch"), True, r["name"])
            else:
                self.assertIs(r.get("touch"), False, r["name"])


# -------------------------------------------------------------- walk steps

class StepsTest(_Browser):

    def test_wait_change_follows_a_readout_that_moves(self):
        """CONTROL: the readout moves on its own a moment after the page opens."""
        html = page(READOUT % "M31 1-1 \u00b7 pass 1",
                    "setTimeout(() => { document.querySelector('.val').textContent ="
                    " 'M31 1-2 \u00b7 pass 1'; }, 1200);")
        r = self.walk(html, {"click": [{"wait_change": '[data-testid="stage"] .val',
                                        "wait_ms": 5000}]})
        self.assertPasses(r)
        self.assertEqual((r["click_log"][0]["from"], r["click_log"][0]["to"]),
                         ("M31 1-1 \u00b7 pass 1", "M31 1-2 \u00b7 pass 1"))

    def test_a_readout_frozen_on_its_first_publish_fails(self):
        """The case `wait_change` exists for: a page that never moves.

        Mutation "wait_change passes at once" (`_wait_change` returns ok when
        the text is the same), observed red:
            AssertionError: True is not false : the probe passed a page it must fail"""
        r = self.walk(page(READOUT % "M31 1-1 \u00b7 pass 1"),
                      {"click": [{"wait_change": '[data-testid="stage"] .val', "wait_ms": 1500}]})
        self.assertFailsOnlyFor(r, "still read 'M31 1-1")

    def test_a_readout_that_is_not_there_cannot_change(self):
        r = self.walk(page("<p>nothing</p>"),
                      {"click": [{"wait_change": '[data-testid="stage"] .val', "wait_ms": 500}]})
        self.assertFailsOnlyFor(r, "not visible, or said nothing")

    def test_remember_then_wait_for_it_to_differ(self):
        """The pier side is remembered ("east") and the walk later waits for it
        to be something else, set and not "unknown". The fixture answers east,
        then unknown (never a flip), then west.

        Mutation "differs_from ignores the memory" (`value != _MEMO[name]`
        deleted), observed red at once: the wait took the remembered side
        itself as the change,
            AssertionError: 'east' != 'west'"""
        sides = iter(["east", "east", "unknown", "west"])
        last = {"v": "east"}

        def status(n):
            last["v"] = next(sides, last["v"])
            return 200, {"meridian": {"pier_side": last["v"]}}

        step_r = {"remember_api": {"get": "/api/status", "field": "meridian.pier_side",
                                   "as": "side", "not_in": ["unknown"]}}
        step_w = {"wait_api": {"get": "/api/status", "field": "meridian.pier_side",
                               "differs_from": "side", "not_in": ["unknown"]}, "wait_ms": 4000}
        r = self.walk(page("<p>x</p>"), {"click": [step_r, step_w]}, {"/api/status": status})
        self.assertPasses(r)
        self.assertEqual(probe._MEMO["side"], "east")
        self.assertEqual(r["click_log"][1]["value"], "west")
        # The same side throughout never differs.
        r = self.walk(page("<p>x</p>"), {"click": [step_r, {**step_w, "wait_ms": 1200}]},
                      {"/api/status": {"meridian": {"pier_side": "east"}}})
        self.assertFailsOnlyFor(r, "the server still says 'east'")

    def test_nothing_remembered_differs_from_nothing(self):
        """A `differs_from` with no remembered value (its phone walk never ran)
        never passes: "it changed" is no claim about a side never read."""
        step_w = {"wait_api": {"get": "/api/status", "field": "meridian.pier_side",
                               "differs_from": "never"}, "wait_ms": 800}
        r = self.walk(page("<p>x</p>"), {"click": [step_w]},
                      {"/api/status": {"meridian": {"pier_side": "west"}}})
        self.assertFailsOnlyFor(r, "the server still says 'west'")

    def test_an_unread_side_is_not_remembered(self):
        r = self.walk(page("<p>x</p>"), {"click": [
            {"remember_api": {"get": "/api/status", "field": "meridian.pier_side", "as": "s",
                              "not_in": ["unknown"]}, "wait_ms": 800}]},
            {"/api/status": {"meridian": {"pier_side": "unknown"}}})
        self.assertFailsOnlyFor(r, "the server said 'unknown' throughout")
        self.assertNotIn("s", probe._MEMO)

    def test_run_copy_runs_mid_walk(self):
        """The CONTINUE walks grade the copy before their edit as well as
        after it, so `run_copy` is a mid-walk check."""
        self.assertIn("run_copy", probe.MID_WALK_CHECKS)
        self.assertIn("readouts", probe.MID_WALK_CHECKS)

    def test_localdate_is_this_machines_calendar_day(self):
        ts = time.mktime((2026, 9, 27, 23, 30, 0, 0, 0, -1))
        self.assertEqual(probe._render("from {t|localdate}.", {"t": ts}), ("from 2026-09-27.", []))
        self.assertEqual(probe._render("{t|localdate}", {"t": "soon"}), (None, ["t|localdate"]))
        self.assertEqual(probe._render("{t|nope}", {"t": ts}), (None, ["t|nope"]))


# ---------------------------------------------------------------- rig port

class RigPortTest(unittest.TestCase):

    def test_the_probe_refuses_the_rigs_port_before_it_launches_anything(self):
        """`--port 8800` (and a `--base` on it) exits 2, in words, before a
        browser starts or a seed is sent. A private port is not refused.

        Mutation "port 8800 accepted" (probe.py `main`: the `_refuse_rig_port`
        block's test made `if False:`), observed red (scratchpad S7-PROBE-mut,
        2026-09-29):
            AssertionError: 0 != 2"""
        self.assertIsNone(probe._refuse_rig_port("http://127.0.0.1:8871"))
        self.assertIsNone(probe._refuse_rig_port("http://127.0.0.1"))
        self.assertIn("8800", probe._refuse_rig_port("http://127.0.0.1:8800"))
        with tempfile.TemporaryDirectory() as out:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = probe.main(["--port", "8800", "--routes", str(ROUTES_PATH),
                                   "--only", "no-such-route", "--widths", "390", "--out", out])
            self.assertEqual(code, 2)
            self.assertIn("rig's server port", err.getvalue())
            self.assertFalse((Path(out) / "report.jsonl").exists())
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(["--base", "http://localhost:8800/", "--routes",
                                             str(ROUTES_PATH), "--only", "x", "--out", out]), 2)

    def test_server_ctl_refuses_to_start_a_server_on_the_rigs_port(self):
        """`server_ctl.py start --port 8800` exits 2 naming the rig's port,
        before it looks for the venv, wipes a directory or spawns anything.
        The venv path given is one that does not exist, so a start that got
        past the refusal stops at the next check and spawns nothing either.

        Mutation "port 8800 accepted" (server_ctl.py `cmd_start`: the
        `refuse_rig_port` block's test made `if False:`), observed red:
            AssertionError: "rig's server port" not found in '[server_ctl] ERROR:
            venv python not found at <the test's temp dir>\\\\no-python.exe\\n'"""
        self.assertIsNone(server_ctl.refuse_rig_port(8871))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg"
            cfg.mkdir()
            (cfg / "keep.txt").write_text("x", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = server_ctl.main(["start", "--fresh", "--port", "8800",
                                        "--config-dir", str(cfg), "--capture-dir",
                                        str(Path(tmp) / "cap"), "--venv-python",
                                        str(Path(tmp) / "no-python.exe")])
            self.assertEqual(code, 2)
            self.assertIn("rig's server port", out.getvalue())
            self.assertTrue((cfg / "keep.txt").exists())

    def test_server_ctl_checks_the_staged_solve_failure(self):
        """`--sim-solve-fault` is three numbers the SimSolver will read, or the
        start refuses (a typo would otherwise stage no failure at all, which
        the SimSolver reads as none). What a start hands the server is the
        next case's."""
        self.assertEqual(server_ctl.parse_solve_fault(" 0.731777, 41.34074 ,0.12"),
                         "0.731777,41.34074,0.12")
        for bad in ("0.73,41", "a,b,c", "0.73,41,0", "25,41,0.1", "0.7,95,0.1", "nan,1,1"):
            with self.assertRaises(ValueError, msg=bad):
                server_ctl.parse_solve_fault(bad)
        route = _route_file()
        self.assertIn("--sim-solve-fault 0.731777,41.34074,0.12", route["_run"])
        self.assertIn("0.731777 h +41.34074", route["_fault"])

    def test_server_ctl_hands_the_server_the_fault_it_was_given_and_no_other(self):
        """The environment `cmd_start` builds for the server, as `_spawn_server`
        receives it (stopped there, so nothing is launched): with
        `--sim-solve-fault` it carries the normalised value, and without the
        flag it carries none, even when the shell running server_ctl has one
        set (a value left in scenario 2's shell would otherwise stage a solve
        failure on every later scenario's server). And the variable is the
        one the SimSolver reads, or the flag would stage nothing at all.

        Mutation "the shell's fault inherited" (server_ctl.py `cmd_start`: the
        `env.pop(SOLVE_FAULT_ENV, None)` line deleted), observed red
        (verifier's scratchpad S7-PROBE-verify-mut, 2026-09-29; before this
        case the mutant survived the whole file):
            AssertionError: '1.0,2.0,3.0' is not None : a start without the flag
            passed the shell's value on"""
        name = server_ctl.SOLVE_FAULT_ENV
        seen: list[dict] = []

        class _Spawned(Exception):
            pass

        def spawn(venv_python, port, env, log_path):
            seen.append(dict(env))
            raise _Spawned

        saved_spawn, saved_value = server_ctl._spawn_server, os.environ.get(name)
        server_ctl._spawn_server = spawn
        os.environ[name] = "1.0,2.0,3.0"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                py = Path(tmp) / "python.exe"
                py.write_bytes(b"")
                start = ["start", "--port", "8871", "--no-sim",
                         "--config-dir", str(Path(tmp) / "cfg"),
                         "--capture-dir", str(Path(tmp) / "cap"),
                         "--venv-python", str(py), "--ui-dir", tmp]
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(_Spawned):
                        server_ctl.main(start)
                    with self.assertRaises(_Spawned):
                        server_ctl.main(start + ["--sim-solve-fault", "0.731777,41.34074,0.12"])
        finally:
            server_ctl._spawn_server = saved_spawn
            if saved_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved_value
        # Only the one value is ever compared or printed: the environment
        # itself is the shell's, and may hold tokens a failure must not show.
        self.assertEqual(len(seen), 2)
        self.assertIsNone(seen[0].get(name), "a start without the flag passed the shell's value on")
        self.assertEqual(seen[1].get(name), "0.731777,41.34074,0.12")
        simsolver = (REPO_ROOT / "server" / "astrodeck" / "solve" / "simsolver.py")
        self.assertIn(f'SOLVE_FAULT_ENV = "{name}"', simsolver.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- seeds

class SeedScopeTest(unittest.TestCase):

    def test_a_seed_op_runs_only_for_its_scenarios_routes(self):
        """`--only` one scenario seeds that scenario alone: the straddle's
        fixture site must not be saved on scenario 3's server (it opens the
        window auto-resume would start the armed session in), and scenario
        3's first nights must not run on scenario 1's.

        Mutation "seed ops ignore routes" (`_seed_for_width` drops the
        `routes` filter), observed red:
            AssertionError: Lists differ: [['re[21 chars]low'], ['save_flow'],
            ['save_flow'], ['save_fl[94 chars]ow']] != [['re[21 chars]low']]"""
        seeds = _route_file()["seed"]

        def kinds(width, names):
            return [sorted(k for k in op if k not in ("widths", "routes"))
                    for op in probe._seed_for_width(seeds, width, set(names))]

        self.assertEqual(kinds(390, SCENARIOS["1"][:3]), [["require_sim"], ["save_flow"]])
        self.assertEqual(kinds(1440, SCENARIOS["1"][3:]), [["require_sim"]])
        self.assertEqual(kinds(390, SCENARIOS["3"][:1]),
                         [["require_sim"], ["save_flow"], ["save_flow"], ["run_flow"],
                          ["run_flow"], ["daylight_site"], ["seed_session"]])
        self.assertEqual(kinds(1440, SCENARIOS["3"][1:]), [["require_sim"], ["seed_session"]])
        self.assertEqual(kinds(390, SCENARIOS["4"][:1]),
                         [["require_sim"], ["set_site"], ["save_flow"]])
        # No names: every op at the width, as before S7.
        self.assertEqual(len(probe._seed_for_width(seeds, 390)),
                         len([op for op in seeds if 390 in op.get("widths", [390])]))


class SkyTest(unittest.TestCase):

    def test_the_sidereal_time_is_the_servers(self):
        """The probe's LST is server/astrodeck/catalog/coords.py's formula:
        two fixed instants against the values `lst_hours` gives for them
        (computed with the server venv, 2026-09-28).

        Mutation "the sidereal rate of a solar day" (`24.06570982441908` made
        `24.0`), observed red:
            AssertionError: 20.919596785592148 != 14.25356735882815 within 7
            places (6.666029426763998 difference)"""
        self.assertAlmostEqual(probe._lst_hours(0.0, 1790000000.0), 14.25356735882815, places=7)
        self.assertAlmostEqual(probe._lst_hours(-74.0, 1790662433.0), 1.8332011014436524,
                               places=7)

    def test_the_daylight_site_is_under_the_sun_all_year(self):
        """At the subsolar longitude, 40 N, the Sun stands at 90 - |40 - its
        declination|, never below 26 deg; at the opposite longitude it is as
        far below. Sampled every 5 days for a year, at hours that vary.

        Mutation "the antisolar longitude" (`_subsolar_lon` adds 180), observed
        red:
            AssertionError: -49.44800333505124 not greater than 26.0 : (0, 145.0)"""
        t0 = 1790000000.0
        for k in range(73):
            t = t0 + k * 5 * 86400 + k * 3917
            lon = float(round(probe._subsolar_lon(t)))
            self.assertGreater(probe._sun_altitude(40.0, lon, t), 26.0, (k, lon))
            anti = lon - 180.0 if lon > 0 else lon + 180.0
            self.assertLess(probe._sun_altitude(40.0, anti, t), -20.0, (k, anti))

    def test_the_fixture_site_is_dark_at_night_and_light_at_noon(self):
        """`set_site` `night` asks the same altitude: 74 W at 06:00 UTC on
        2026-09-29 (01:00 there) is dark, and at 17:00 UTC it is day."""
        # timegm reads the struct as UTC; mktime minus time.timezone was an
        # hour off it under daylight saving (05:00 UTC on a PDT machine).
        night = calendar.timegm(time.strptime("2026-09-29 06:00", "%Y-%m-%d %H:%M"))
        self.assertLess(probe._sun_altitude(40.0, -74.0, night), -12.0)
        self.assertGreater(probe._sun_altitude(40.0, -74.0, night + 11 * 3600), 20.0)

    def test_ra_text_is_typed_as_a_target_is(self):
        self.assertEqual(probe._ra_text(0.7122222), "00h 42m 44s")
        self.assertEqual(probe._ra_text(23.9999999), "00h 00m 00s")
        self.assertEqual(probe._ra_text(-0.5), "23h 30m 00s")


class _Seeding(_Browser):

    def seed(self, ops, api=None, write_api=None, ctx=None):
        Handler.state = {"page": page(""), "api": api or {}, "write_api": write_api or {}}
        c = self.browsers.for_width(1440).new_context()
        try:
            return probe._seed(c.request, self.base, ops, ctx)
        finally:
            c.close()


class SiteSeedTest(_Seeding):

    def site_api(self, saved: dict):
        """GET /api/site answers whatever the last PUT saved."""
        def put(body):
            saved.update(body["site"], is_default=False)
            return 200, {"ok": True}

        def get(n):
            return 200, {"site": dict(saved), "version": n}
        return {"/api/site": get}, {("PUT", "/api/site"): put}

    def test_a_fixture_site_is_put_and_read_back(self):
        """CONTROL: the PUT carries the fixture, and the server keeps it."""
        saved = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
        api, write = self.site_api(saved)
        out = self.seed([{"set_site": {"latitude": 40.0, "longitude": -74.0,
                                       "name": "probe fixture 40N 74W"}}], api, write)
        self.assertEqual(out, [{"set_site": "probe fixture 40N 74W"}])
        self.assertEqual(Handler.state["writes"][0][2]["site"]["longitude"], -74.0)

    def test_a_site_the_server_did_not_keep_refuses(self):
        """A server that answers the PUT and still holds its default."""
        api = {"/api/site": {"site": {"latitude": 0.0, "longitude": 0.0, "is_default": True}}}
        with self.assertRaises(probe.SeedError) as caught:
            self.seed([{"set_site": {"latitude": 40.0, "longitude": -74.0}}], api)
        self.assertIn("did not keep the fixture site", str(caught.exception))

    def test_a_daylight_site_puts_the_sun_overhead_and_prints_no_coordinate(self):
        """The saved longitude is the subsolar one now, and the seed's log line
        carries no coordinate (a report is scanned against the observing
        site's values, and needs none)."""
        saved = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
        api, write = self.site_api(saved)
        out = self.seed([{"daylight_site": {"latitude": 40.0, "name": "day"}}], api, write)
        self.assertEqual(out, [{"daylight_site": "day", "sun_up": True}])
        self.assertGreater(probe._sun_altitude(40.0, saved["longitude"], time.time()), 26.0)
        self.assertEqual(saved["longitude"], float(int(saved["longitude"])))

    def test_the_meridian_ra_is_computed_at_the_servers_site_now(self):
        """The TARGET's RA is the server's saved site's sidereal time now plus
        `minutes_east`: its hour angle at the seed is -5 min, to the second.

        Mutation "meridian at longitude 0" (`_graph_with_meridian_ra` reads
        `lon = 0.0`), observed red:
            AssertionError: -5.016714005684351 != -0.08333333333333333 within
            0.0006 delta (4.933380672351018 difference)"""
        saved = {"latitude": 40.0, "longitude": -74.0, "is_default": False}
        api, write = self.site_api(saved)
        graph = {"nodes": [{"id": "n2", "type": "target", "params": {"ra": "00h 00m 00s"}}],
                 "edges": []}
        t = time.time()
        g = self._meridian(graph, api, write)
        ra = g["nodes"][0]["params"]["ra"]
        h, m, s = (int(x) for x in re.findall(r"\d+", ra))
        ha = ((probe._lst_hours(-74.0, t) - (h + m / 60 + s / 3600) + 12) % 24) - 12
        self.assertAlmostEqual(ha, -5.0 / 60.0, delta=0.0006)
        self.assertEqual(graph["nodes"][0]["params"]["ra"], "00h 00m 00s")  # not mutated

    def _meridian(self, graph, api, write):
        Handler.state = {"page": page(""), "api": api, "write_api": write}
        c = self.browsers.for_width(1440).new_context()
        try:
            return probe._graph_with_meridian_ra(c.request, self.base, graph,
                                                 {"node": "n2", "minutes_east": 5.0})
        finally:
            c.close()

    def test_no_saved_site_no_meridian(self):
        api = {"/api/site": {"site": {"latitude": 0.0, "longitude": 0.0, "is_default": True}}}
        with self.assertRaises(probe.SeedError) as caught:
            self._meridian({"nodes": [{"id": "n2", "type": "target", "params": {}}]}, api, {})
        self.assertIn("put a set_site op first", str(caught.exception))

    def test_a_night_walk_refuses_a_fixture_site_in_daylight(self):
        """`night`: the straddle's seed refuses unless the Sun is more than 12
        deg down at the fixture site now, naming why (in daylight the start's
        own Sun gate would stop the walk three doors later, for a reason it
        does not name). A 40 N site under a noon Sun now refuses; the site
        opposite it, where the Sun is at least 26 deg down, passes (CONTROL).

        Mutation "a straddle seeded in daylight" (probe.py `_seed_set_site`:
        the `alt > -12.0` test made `if False:`), observed red (verifier's
        scratchpad S7-PROBE-verify-mut, 2026-09-29; before this case the
        mutant survived the whole file):
            AssertionError: SeedError not raised"""
        noon = float(round(probe._subsolar_lon(time.time())))
        midnight = noon - 180.0 if noon > 0 else noon + 180.0
        saved = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
        api, write = self.site_api(saved)
        with self.assertRaises(probe.SeedError) as caught:
            self.seed([{"set_site": {"latitude": 40.0, "longitude": noon, "name": "noon",
                                     "night": True}}], api, write)
        self.assertIn("the Sun is up at the fixture site 'noon'", str(caught.exception))
        out = self.seed([{"set_site": {"latitude": 40.0, "longitude": midnight,
                                       "name": "midnight", "night": True}}], api, write)
        self.assertEqual(out, [{"set_site": "midnight", "night": True}])


class RunFlowSeedTest(_Seeding):

    def test_a_flow_is_run_until_it_banks_then_aborted_to_dormant(self):
        """CONTROL: the engine is idle, the run starts, the progress route
        counts 0, then 1, then 2 banked, the abort is sent after that, and the
        session reads dormant."""
        banks = iter([0, 1, 2, 2])
        live = {"on": False}

        def progress(n):
            b = next(banks, 2)
            status = "dormant" if not live["on"] and b >= 2 else "active"
            return 200, {"session": {"id": "s9", "status": status}, "blocks": [{"banked": b}]}

        def run(body):
            live["on"] = True
            return 200, {"started": True}

        def abort(body):
            live["on"] = False
            return 200, {"aborted": True}

        api = {"/api/sequence/state": lambda n: (200, {"state": "running" if live["on"] else "aborted"}),
               "/api/flows/f/progress": progress}
        write = {("POST", "/api/flows/f/run"): run, ("POST", "/api/sequence/abort"): abort}
        out = self.seed([{"run_flow": {"flow": "f", "min_banked": 2, "timeout_ms": 20000}}],
                        api, write)
        self.assertEqual(out, [{"run_flow": "f", "banked": 2, "session": "s9"}])
        self.assertEqual([w[1] for w in Handler.state["writes"]],
                         ["/api/flows/f/run", "/api/sequence/abort"])

    def test_a_first_night_that_banks_nothing_is_aborted_and_refused(self):
        """The progress route never counts a banked sub: the run is still
        aborted when its time is up (a seed never leaves a run going on the
        server it walks), and the seed refuses, naming the count, rather than
        seeding a CONTINUE whose counts no ledger holds.

        Mutation "a first night of no frames" (probe.py `_seed_run_flow`: the
        `banked < need` test made `if False:`), observed red (verifier's
        scratchpad S7-PROBE-verify-mut, 2026-09-29; before this case the
        mutant survived the whole file):
            AssertionError: SeedError not raised"""
        live = {"on": False}

        def run(body):
            live["on"] = True
            return 200, {"started": True}

        def abort(body):
            live["on"] = False
            return 200, {"aborted": True}

        def progress(n):
            return 200, {"session": {"id": "s9", "status": "active" if live["on"] else "dormant"},
                         "blocks": [{"banked": 0}]}

        api = {"/api/sequence/state": lambda n: (200, {"state": "running" if live["on"] else "aborted"}),
               "/api/flows/f/progress": progress}
        write = {("POST", "/api/flows/f/run"): run, ("POST", "/api/sequence/abort"): abort}
        with self.assertRaises(probe.SeedError) as caught:
            self.seed([{"run_flow": {"flow": "f", "min_banked": 2, "timeout_ms": 1500}}],
                      api, write)
        self.assertIn("'f' banked 0 subs in 1500 ms", str(caught.exception))
        self.assertEqual([w[1] for w in Handler.state["writes"]],
                         ["/api/flows/f/run", "/api/sequence/abort"])

    def test_a_busy_engine_is_refused_before_anything_is_pressed(self):
        with self.assertRaises(probe.SeedError) as caught:
            self.seed([{"run_flow": {"flow": "f"}}], {"/api/sequence/state": {"state": "running"}})
        self.assertIn("engine is busy", str(caught.exception))
        self.assertNotIn("writes", Handler.state)

    def test_seed_session_needs_the_private_directories(self):
        """seed_session writes a store; it never guesses whose."""
        with self.assertRaises(probe.SeedError) as caught:
            self.seed([{"seed_session": {"flow": "f", "arm": True}}], ctx={})
        self.assertIn("--config-dir, --capture-dir", str(caught.exception))

    def test_seed_session_is_read_back_through_the_progress_route(self):
        """After seed_session.py, the progress route must name the session the
        script moved, dormant, armed as asked, and no run may be left on
        tonight's observing night; each way the server can say otherwise
        refuses, naming it (CONTROL first). The script's run is stood in for
        here, printing what it prints: the script itself is graded end to end
        by SeedSessionScriptTest, and this is the probe's reading of it.

        Mutation "the move taken on the script's word" (probe.py
        `_seed_session`: the `if problems:` test made `if False:`), observed
        red (verifier's scratchpad S7-PROBE-verify-mut, 2026-09-29; before
        this case the mutant survived the whole file):
            AssertionError: SeedError not raised : names session 's8'"""
        printed = {"session": "s9", "tonight": "2026-09-29",
                   "observing_nights": ["2026-09-28"], "runs_after": ["f-20260928-230000"],
                   "frames": 2, "armed": True}
        calls: list[list[str]] = []

        def script(cmd, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(printed) + "\n",
                                               stderr="")

        def api(**session):
            return {"/api/flows/f/progress": {"session": {"id": "s9", "status": "dormant",
                                                          "armed": True, **session}}}

        ctx = {"config_dir": "cfg", "capture_dir": "cap", "venv_python": "py"}
        op = [{"seed_session": {"flow": "f", "nights_ago": 1, "arm": True}}]
        with mock.patch.object(probe.subprocess, "run", script):
            out = self.seed(op, api(), ctx=ctx)
            self.assertEqual((out[0]["seed_session"], out[0]["session"]), ("f", "s9"))
            self.assertEqual(calls[0][0], "py")
            self.assertTrue(calls[0][1].endswith("seed_session.py"), calls[0][1])
            self.assertEqual(calls[0][2:], ["--config-dir", "cfg", "--capture-dir", "cap",
                                            "--flow", "f", "--nights-ago", "1", "--arm"])
            for session, why in (({"id": "s8"}, "names session 's8'"),
                                 ({"status": "active"}, "is 'active', not dormant"),
                                 ({"armed": False}, "armed=False")):
                with self.assertRaises(probe.SeedError, msg=why) as caught:
                    self.seed(op, api(**session), ctx=ctx)
                self.assertIn(why, str(caught.exception))
            printed["observing_nights"] = ["2026-09-28", "2026-09-29"]
            with self.assertRaises(probe.SeedError) as caught:
                self.seed(op, api(), ctx=ctx)
            self.assertIn("still on tonight's observing night", str(caught.exception))


# ------------------------------------------------------------ seed_session

class VenvPythonResolutionTest(unittest.TestCase):
    """#653: wave worktrees have no server/.venv, so VENV_PYTHON's old
    repo-relative-only resolution always missed and the two end-to-end cases
    below always SKIPPED there. These hold the resolution function itself,
    not the module-level VENV_PYTHON (computed once at import time, so an
    env var set after import would not move it)."""

    def test_the_environment_variable_overrides_the_repo_relative_venv(self):
        """The wave tooling's and the post-merge check's way in.

        Mutation "the override ignored" (`_resolve_venv_python` returns the
        repo-relative path unconditionally, never reading the environment
        variable), observed red (2026-10-01, this worktree):
            AssertionError: WindowsPath('<repo-relative server/.venv path>')
            != WindowsPath('C:/elsewhere/python.exe')"""
        with mock.patch.dict(os.environ,
                             {"ASTRODECK_PROBE_VENV_PYTHON": "C:/elsewhere/python.exe"}):
            self.assertEqual(_resolve_venv_python(), Path("C:/elsewhere/python.exe"))

    def test_with_no_override_it_falls_back_to_the_repo_relative_venv(self):
        """The main tree's unchanged path: no env var, same default VENV_PYTHON
        above was always computed from."""
        env = dict(os.environ)
        env.pop("ASTRODECK_PROBE_VENV_PYTHON", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(_resolve_venv_python(),
                             REPO_ROOT / "server" / ".venv" / "Scripts" / "python.exe")


class SeedSessionScriptTest(unittest.TestCase):

    def test_a_run_moves_back_whole_calendar_days_at_the_same_wall_clock(self):
        """The stamp in the id moves; the slug and a second start's suffix do
        not, and the seconds moved are the wall clock's (a day across a clock
        change is not 86400 s)."""
        rx = re.compile(r"(\d{8}-\d{6})(?:-\d+)?$")
        moved, delta = seed_session._moved_id("s7-cont-20260928-231806-2", 1, rx)
        self.assertEqual(moved, "s7-cont-20260927-231806-2")
        was = time.mktime(time.strptime("20260928-231806", "%Y%m%d-%H%M%S"))
        now = time.mktime(time.strptime("20260927-231806", "%Y%m%d-%H%M%S"))
        self.assertEqual(delta, was - now)
        with self.assertRaises(seed_session.SeedRefused):
            seed_session._moved_id("legacy-run", 1, rx)

    def test_it_refuses_the_developers_own_directories(self):
        """The server's own defaults for a checkout, and anything under them:
        server/config (config.py) and the repository's captures/ (hub.py's
        CAPTURE_DIR is the repository root's captures/, not server/'s), each
        refused for THAT reason, so a path that merely does not exist cannot
        pass this for it. Since H4-PROBE-GUARD (#539) the refusal is not a
        list of the developer's directories but the absence of the marker
        server_ctl.py writes into the directories it creates, and its message
        still says the directory may be the developer's own
        (server/tests/test_h4_seed_session_allow_list.py holds that rule); a
        directory is accepted only once a probe start has marked it.

        Mutation "the accepted directory not marked" (this case's
        `write_probe_marker` line deleted, the case as it stood before
        H4-PROBE-LAYOUT re-pinned it), observed red (the probe suite's
        baseline run, and again in the private mirror, 2026-09-29):
            seed_session.SeedRefused: --config-dir <tmp> carries no
            .astrodeck-probe marker, so no probe server's server_ctl.py start
            created it, and it may be the developer's own or a real
            observatory's config or captures: pass the directory
            server_ctl.py start made for the private server (one made before
            the marker existed needs server_ctl.py start --fresh)

        The mutation first recorded here, gone with the list it mutated:
        Mutation "the developer's captures accepted" (seed_session.py
        `_DEVELOPERS_OWN` without its captures/ entry, which is how the script
        first shipped: it refused server/ alone while its docstring said it
        protected the developer's captures), observed red (verifier's
        scratchpad S7-PROBE-verify-mut, 2026-09-29):
            AssertionError: SeedRefused not raised : <the copy's root>\\captures

        Issue #624: server/config and captures/ (and captures/sessions) are
        .gitignore'd runtime output, written only once a server has actually
        run against this checkout. A fresh clone, CI checkout or wave worktree
        does not have them, so `seed_session._private`'s own `is_dir()` check
        refused first, with "... is not a directory" -- never reaching the
        marker-absence refusal this case exists to grade, and the assertIn
        below failed on the wrong message. Each missing one is made here, as
        an empty stand-in for the span of this case, and removed after:
        creating a throwaway directory under these exact names is safe (a
        real server run would make the same ones, and `_private` never writes
        into them -- it only reads `is_dir()` and the marker file).
        `REPO_ROOT / "server"` is tracked and always present, so it is never
        among `created`.

        Named mutant: delete the `if not marker.is_file(): raise ...` ownership
        check in seed_session.py's `_marker` (the one piece of this script that
        decides whether a directory is proven to be a probe server's). Observed
        red with every one of these four directories present and empty (2026-10
        -01, this worktree): the later `open(marker, "rb")` still raises on the
        now-unchecked missing file, but as a plain OSError its own `except`
        catches and rewords, so this reaches the assertIn instead of crashing,
        and fails there --
            AssertionError: "developer's own" not found in '--capture-dir
            ...\\server\\config: its .astrodeck-probe marker cannot be read
            (FileNotFoundError), so it is not one server_ctl.py start wrote'
        (a SeedRefused is still raised, so assertRaises is satisfied -- the
        ownership wording the removed check alone was responsible for is
        what is gone)."""
        checked = (REPO_ROOT / "server" / "config", REPO_ROOT / "server",
                   REPO_ROOT / "captures", REPO_ROOT / "captures" / "sessions")
        created = [own for own in checked if not own.is_dir()]
        for own in created:
            own.mkdir(parents=True, exist_ok=True)
        try:
            for own in checked:
                with self.assertRaises(seed_session.SeedRefused, msg=str(own)) as caught:
                    seed_session._private(str(own), "--capture-dir")
                self.assertIn("developer's own", str(caught.exception))
        finally:
            # Deepest first: captures/sessions before captures, so the parent
            # is empty by the time its own rmdir runs. suppress rather than
            # assert -- a cleanup slip must never stand in for this case's
            # own result.
            for own in sorted(created, key=lambda p: len(p.parts), reverse=True):
                with contextlib.suppress(OSError):
                    own.rmdir()
        with self.assertRaises(seed_session.SeedRefused):
            seed_session._private(None, "--capture-dir")
        with tempfile.TemporaryDirectory() as tmp:
            server_ctl.write_probe_marker(Path(tmp).resolve(), 8871, 1)
            self.assertEqual(seed_session._private(tmp, "--config-dir"), Path(tmp).resolve())

    @unittest.skipUnless(VENV_PYTHON.is_file(), f"needs the server venv at {VENV_PYTHON}")
    def test_the_script_moves_a_stored_session_and_arms_it(self):
        """End to end, under the server's venv, against a store in a temporary
        folder: a dormant session of one run tonight with two frames, a
        report, a set-aside record and a second, armed session. After the
        script, the run and its frames are a night earlier, the report file is
        under the new id, the set-aside keeps to the moved night, the session
        is the armed one (the other disarmed), and plan_saved_ts is untouched.

        Mutation "frames left on the night they were shot" (seed_session.py:
        the frame loop's test made `if False:`), observed red:
            AssertionError: Lists differ: ['s7-20260929-000730',
            's7-20260929-000730'] != ['s7-20260928-000730', 's7-20260928-000730']

        The store's directories carry the marker a probe start writes (#539,
        H4-PROBE-GUARD). Mutation "the store not marked" (the two
        `write_probe_marker` lines deleted, the case as it stood before
        H4-PROBE-LAYOUT re-pinned it), observed red (the probe suite's
        baseline run, 2026-09-29):
            AssertionError: 2 != 0 : seed_session: REFUSED: --config-dir
            <tmp>/cfg carries no .astrodeck-probe marker, so no probe server's
            server_ctl.py start created it, ..."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cap = Path(tmp) / "cfg", Path(tmp) / "cap"
            cfg.mkdir()
            cap.mkdir()
            server_ctl.write_probe_marker(cfg, 8871, 1)
            server_ctl.write_probe_marker(cap, 8871, 1)
            env = {**CHILD_ENV, "ASTRODECK_CONFIG_DIR": str(cfg),
                   "ASTRODECK_CAPTURE_DIR": str(cap)}
            made = subprocess.run([str(VENV_PYTHON), "-c", MAKE_SESSION], env=env,
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(REPO_ROOT))
            self.assertEqual(made.returncode, 0, made.stderr[-800:])
            before = json.loads(made.stdout.strip().splitlines()[-1])
            done = subprocess.run([str(VENV_PYTHON), str(HERE / "seed_session.py"),
                                   "--config-dir", str(cfg), "--capture-dir", str(cap),
                                   "--flow", "f7", "--nights-ago", "1", "--arm"],
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(REPO_ROOT))
            self.assertEqual(done.returncode, 0, done.stderr[-800:])
            out = json.loads(done.stdout.strip().splitlines()[-1])
            back = subprocess.run([str(VENV_PYTHON), "-c", READ_SESSION, before["id"],
                                   before["other"]], env=env, capture_output=True, text=True,
                                  timeout=120, cwd=str(REPO_ROOT))
            self.assertEqual(back.returncode, 0, back.stderr[-800:])
            got = json.loads(back.stdout.strip().splitlines()[-1])
        new_id = before["run"].replace(before["stamp"], before["stamp_before"])
        self.assertEqual(out["runs_after"], [new_id])
        self.assertEqual(got["nights"], [new_id])
        self.assertEqual(got["frame_nights"], [new_id, new_id])
        self.assertEqual([round(a - b) for a, b in zip(before["frame_ts"], got["frame_ts"])],
                         [round(before["delta"])] * 2)
        self.assertEqual(got["set_aside_nights"], [got["observing_nights"][0]])
        self.assertNotIn(got["tonight"], got["observing_nights"])
        self.assertTrue(got["report_moved"])
        self.assertFalse(got["report_old_left"])
        self.assertEqual((got["armed"], got["other_armed"]), (True, False))
        self.assertEqual(got["plan_saved_ts"], before["plan_saved_ts"])

    @unittest.skipUnless(VENV_PYTHON.is_file(), f"needs the server venv at {VENV_PYTHON}")
    def test_a_session_a_run_owns_is_refused(self):
        """A session a run owns is never moved or armed. Its directories carry
        the probe marker (#539), so the refusal seen is the one this case is
        for.

        Mutation "the store not marked" (the two `write_probe_marker` lines
        deleted, the case as it stood before H4-PROBE-LAYOUT re-pinned it),
        observed red (the probe suite's baseline run, 2026-09-29):
            AssertionError: "'active', not dormant" not found in
            "seed_session: REFUSED: --config-dir <tmp>/cfg carries no
            .astrodeck-probe marker, ..." """
        with tempfile.TemporaryDirectory() as tmp:
            cfg, cap = Path(tmp) / "cfg", Path(tmp) / "cap"
            cfg.mkdir()
            cap.mkdir()
            server_ctl.write_probe_marker(cfg, 8871, 1)
            server_ctl.write_probe_marker(cap, 8871, 1)
            env = {**CHILD_ENV, "ASTRODECK_CONFIG_DIR": str(cfg),
                   "ASTRODECK_CAPTURE_DIR": str(cap)}
            made = subprocess.run([str(VENV_PYTHON), "-c", MAKE_SESSION, "active"], env=env,
                                  capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
            self.assertEqual(made.returncode, 0, made.stderr[-800:])
            done = subprocess.run([str(VENV_PYTHON), str(HERE / "seed_session.py"),
                                   "--config-dir", str(cfg), "--capture-dir", str(cap),
                                   "--flow", "f7", "--arm"],
                                  capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
            self.assertEqual(done.returncode, 2)
            self.assertIn("'active', not dormant", done.stderr)


#: Run by the server venv: a dormant flow session of one run started tonight
#: (a report id stamped now), two frames, a report on disk, a set-aside record
#: for tonight's night key, and an older armed session of another flow.
MAKE_SESSION = r'''
import json, sys, time
from datetime import datetime, timedelta
from astrodeck.events import night_key
from astrodeck.persist import write_json_atomic
from astrodeck.sequence import report as R
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import Session, SessionFrame, session_store
now = time.time()
stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
# One CALENDAR day earlier at the same wall clock, as seed_session.py moves a
# run: `now - 86400` is an hour off it on the day the clocks change.
before = (datetime.strptime(stamp, "%Y%m%d-%H%M%S") - timedelta(days=1)).strftime("%Y%m%d-%H%M%S")
run = "s7-" + stamp
other = Session(name="other", status="dormant", auto_resume=True, created_ts=now - 9e5,
                plan=SequencePlan(name="other"), origin="flow", origin_id="f8")
session_store.save(other)
s = Session(name="f7", status=(sys.argv[1] if len(sys.argv) > 1 else "dormant"),
            created_ts=now - 60, plan=SequencePlan(name="f7"), nights=[run],
            origin="flow", origin_id="f7", plan_saved_ts=now - 3600,
            frames=[SessionFrame(ts=now - 30, night=run, step_id="a"),
                    SessionFrame(ts=now - 10, night=run, step_id="a")])
s.note_set_aside("t1", "centring failed", night=night_key(now))
session_store.save(s)
write_json_atomic(R._reports_dir() / (run + ".json"),
                  R.SessionReport(id=run, plan_name="f7", started_at=now - 60,
                                  frames=[R.FrameRecord(ts=now - 30, target="t1")]).model_dump())
delta = time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S")) - time.mktime(time.strptime(before, "%Y%m%d-%H%M%S"))
print(json.dumps({"id": s.id, "other": other.id, "run": run, "stamp": stamp,
                  "stamp_before": before, "delta": delta,
                  "frame_ts": [f.ts for f in s.frames], "plan_saved_ts": s.plan_saved_ts}))
'''

#: Run by the server venv: what the store now holds for the two sessions.
READ_SESSION = r'''
import json, sys, time
from astrodeck.events import night_key
from astrodeck.sequence import report as R
from astrodeck.sequence.session import session_store
s = session_store.load(sys.argv[1])
o = session_store.load(sys.argv[2])
old = [p.stem for p in R._reports_dir().glob("*.json")]
print(json.dumps({"nights": s.nights, "frame_nights": [f.night for f in s.frames],
                  "frame_ts": [f.ts for f in s.frames],
                  "set_aside_nights": [r["night"] for r in s.set_aside],
                  "observing_nights": s.observing_nights(), "tonight": night_key(time.time()),
                  "report_moved": s.nights[0] in old,
                  "report_old_left": any(n != s.nights[0] for n in old),
                  "armed": s.is_armed(), "other_armed": o.auto_resume,
                  "plan_saved_ts": s.plan_saved_ts}))
'''


# --------------------------------------------------------------- route file

class RouteFileTest(unittest.TestCase):
    """What the acceptance asks of routes_s7.json, held so an edit that drops
    it is red here rather than a quieter probe."""

    def test_four_scenarios_at_both_widths_each_with_a_view_marker(self):
        """Every scenario has a 390 walk and a 1440 walk; every walk names a
        data-testid; every click step is required or acts without clicking.

        Mutation "a walk without its marker" (routes_s7.json: s7-cont-desktop's
        "testid" deleted), observed red:
            AssertionError: unexpectedly None : s7-cont-desktop"""
        data = _route_file()
        self.assertEqual(data["_scenarios"], SCENARIOS)
        names = [r["name"] for r in data["routes"]]
        self.assertEqual(sorted(names), sorted(sum(SCENARIOS.values(), [])))
        for walks in SCENARIOS.values():
            widths = {tuple(_route(n)["widths"]) for n in walks}
            self.assertEqual(widths, {(390,), (1440,)}, walks)
        acts = {"goto", "wait_for", "fill", "shot", "check", "wait_api", "wait_change",
                "remember_api"}
        for r in data["routes"]:
            self.assertIsNotNone(r.get("testid"), r["name"])
            for s in r["click"]:
                self.assertTrue(acts & set(s) or s.get("required"), (r["name"], s))

    def test_every_live_run_walk_holds_the_page_to_the_routes_at_one_moment(self):
        """Scenarios 1, 2 and 4 grade the page with `readouts`, tied to their
        flow and (for a live run) to the rig's session, and scenario 1 follows
        each surface to the next panel before its final reading.

        Mutation "the follow dropped" (routes_s7.json: s7-rot-classic-phone's
        wait_change step deleted), observed red:
            AssertionError: 'wait_change' not found in '[{"selector":
            "[data-flow-id=\\\\"probe-s7-rot\\\\"]", "required": true}, ...,
            {"shot": "monitor"}]' : s7-rot-classic-phone"""
        for name in SCENARIOS["1"] + SCENARIOS["2"] + SCENARIOS["4"]:
            r = _route(name)
            spec = r.get("readouts")
            self.assertTrue(spec and spec.get("fields"), name)
            self.assertTrue(spec.get("same_session"), name)
            self.assertTrue(spec["flow"].startswith("probe-s7-"), name)
            self.assertIn(spec["flow"], json.dumps(r["click"]), name)
        for name in SCENARIOS["1"]:
            self.assertIn("wait_change", json.dumps(_route(name)["click"]), name)
        stage = _route("s7-rot-run-phone")["readouts"]["fields"][0]["template"]
        self.assertEqual(stage, "{group.name} {group.panel} \u00b7 pass {group.pass}")

    def test_the_fault_walks_hold_the_set_aside_panel_in_panels_and_on_the_sky(self):
        """Scenario 2: 2-2 set aside, named by the state in both reads; PANELS'
        line and the sky's label equal to it; one panel drawn set aside, and
        it is row 1, column 1 (2-2)."""
        for name in SCENARIOS["2"]:
            r = _route(name)
            self.assertEqual(r["readouts"]["require"], {"state.group.set_aside.0.panel": "2-2"})
            templates = [f["template"] for f in r["readouts"]["fields"]]
            self.assertIn("set aside tonight: {group.set_aside.0.reason}", templates)
            self.assertIn("{group.set_aside.0.panel}", templates)
            counts = {c["selector"]: c["equals"] for c in r["count"]}
            self.assertEqual(counts['[data-testid="framing-sky"] [data-role="panel"]'
                                    '[data-row="1"][data-col="1"][data-state="set_aside"]'], 1)

    def test_the_continue_walks_hold_night_two_the_confirm_and_the_replay_notice(self):
        """Scenario 3: CONTINUE on night 2 with the route's counts, graded
        before the edit and after the save; no replay line before the edit
        (the CONTROL) and the progress route's line after it; START OVER
        opened and kept, and no run request sent.

        Mutation "the replay line's control dropped" (routes_s7.json:
        s7-cont-phone's pre-edit count deleted from its check), observed red:
            AssertionError: 0 != 1 : s7-cont-phone"""
        for name in SCENARIOS["3"]:
            r = _route(name)
            self.assertEqual(r["forbid_requests"], [{"method": "POST", "url": "**/api/flows/*/run"}])
            self.assertEqual((r["run_copy"]["night"], r["run_copy"]["min_banked"]), (2, 2))
            checks = [s["check"] for s in r["click"] if "check" in s]
            self.assertEqual(sum(1 for c in checks if "run_copy" in c), 1, name)
            before_edit = [c for c in checks if "count" in c]
            self.assertEqual(len(before_edit), 1, name)
            self.assertEqual(before_edit[0]["count"][0]["equals"], 0)
            steps = json.dumps(r["click"])
            self.assertLess(steps.index("Start this flow over?"), steps.index('"value": "21"'))
            self.assertIn("|localdate}", r["readouts"]["fields"][0]["template"])
            self.assertIs(r["readouts"]["require"]["progress.session.armed"], True)

    def test_the_continue_seed_runs_first_nights_before_daylight_and_moves_them(self):
        """Both first nights run (real frames) before the daylight site is
        saved, and each width moves and arms its own flow's session."""
        seeds = _route_file()["seed"]
        order = [sorted(k for k in op if k not in ("widths", "routes"))[0] for op in seeds
                 if set(op.get("routes", [])) & set(SCENARIOS["3"])]
        self.assertEqual(order, ["save_flow", "save_flow", "run_flow", "run_flow",
                                 "daylight_site", "seed_session", "seed_session"])
        moved = [(op["widths"], op["seed_session"]["flow"], op["seed_session"]["arm"])
                 for op in seeds if "seed_session" in op]
        self.assertEqual(moved, [([390], "probe-s7-cont", True), ([1440], "probe-s7-cont-d", True)])

    def test_the_straddle_is_computed_at_a_fixture_site_and_states_its_wall_time(self):
        """Scenario 4: the harness's 40 N 74 W, saved through the API and dark,
        the TARGET's RA from the probe's clock (never a fixed RA), the wait
        graded with meridian_wait true, the flip with the pier side changed
        from the one remembered, and a wall time stated in the file."""
        seeds = _route_file()["seed"]
        site = next(op["set_site"] for op in seeds if "set_site" in op)
        self.assertEqual((site["latitude"], site["longitude"], site["night"]), (40.0, -74.0, True))
        flow = next(op["save_flow"] for op in seeds
                    if op.get("save_flow", {}).get("id") == "probe-s7-merid")
        self.assertEqual(flow["meridian"], {"node": "n2", "minutes_east": 16.0})
        phone, desk = _route("s7-merid-phone"), _route("s7-merid-desktop")
        self.assertIs(phone["readouts"]["require"]["state.group.meridian_wait"], True)
        self.assertIn("waiting for the meridian", phone["readouts"]["fields"][0]["template"])
        remembered = [s["remember_api"]["as"] for s in phone["click"] if "remember_api" in s]
        differs = [s["wait_api"].get("differs_from") for s in desk["click"] if "wait_api" in s]
        self.assertEqual(remembered, ["s7_pier_before", "s7_frames_at_wait"])
        self.assertIn("s7_pier_before", differs)
        self.assertIn("s7_frames_at_wait", differs)
        # During the wait the modal draws no panel as shot, graded between two
        # reads that both say the group waits.
        steps = desk["click"]
        i = next(k for k, s in enumerate(steps) if "check" in s)
        self.assertEqual(steps[i - 1]["wait_api"], steps[i + 1]["wait_api"])
        self.assertEqual(steps[i - 1]["wait_api"]["field"], "group.meridian_wait")
        self.assertIs(steps[i - 1]["wait_api"]["equals"], True)
        self.assertEqual({c["equals"] for c in steps[i]["check"]["count"]}, {0})
        wall = _route_file()["_wall_time"]
        self.assertRegex(wall, r"phone walk takes \d+ min")
        # No measurement is left a placeholder anywhere in the file: s7-fault-
        # phone's note once read "measured WALL_2P" while `_wall_time` alone
        # was checked. Mutation "a placeholder for a measurement" (routes_s7.json:
        # that note's measurement put back to WALL_2P), observed red (verifier's
        # scratchpad S7-PROBE-verify-mut, 2026-09-29):
        #     AssertionError: Lists differ: ['WALL_2P'] != []
        self.assertEqual(re.findall(r"WALL_\w*", ROUTES_PATH.read_text(encoding="utf-8")), [])

    def test_no_route_can_reach_the_rig(self):
        """The first seed op refuses a server that is not the simulator, at
        every width and for every scenario, and the run instructions name no
        port but private ones."""
        seeds = _route_file()["seed"]
        self.assertEqual(seeds[0], {"require_sim": True})
        for walks in SCENARIOS.values():
            for width in (390, 1440):
                self.assertEqual(probe._seed_for_width(seeds, width, set(walks))[0],
                                 {"require_sim": True})
        self.assertNotIn("8800 ", _route_file()["_run"].replace("any port but 8800", ""))

    def test_the_route_file_is_ascii(self):
        """The house rule, and the file quotes no observing-site value: its one
        site is the harness's fixture, and the daylight one is computed."""
        self.assertTrue(all(b < 128 for b in ROUTES_PATH.read_bytes()))


if __name__ == "__main__":
    unittest.main()
