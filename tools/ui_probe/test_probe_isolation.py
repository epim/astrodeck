# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Real-browser regression for #31, and the browser every walk runs in (#535).
Run with the probe's Playwright Python.

Uses only a temporary loopback fixture; no AstroDeck server or rig needed.

LaunchTest holds the launch rule of mosaic slice H4 (probe.py HIDE_SCROLLBARS,
`_Browsers`): a desktop walk's Chromium draws its scrollbars, a phone walk's
keeps Playwright's default, and nothing in probe.py or in any of the probe's
own test files launches a Chromium any other way. Its mutants were run in a
private mirror of this folder (scratchpad H4-PROBE-LAYOUT-mut, 2026-09-29;
mutate.py and every verbatim output are kept there).
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ast
import tempfile
import threading
import time
import unittest

# Importable from anywhere, and a loud SKIP rather than a collection error
# where the pieces are missing (issue #119).
#
# The interpreter that has Playwright is the SYSTEM python, which is what
# `tools/ui_probe/README.md` requires and what `run.ps1` invokes. A `pytest`
# run from the repository ROOT uses `server/.venv` instead, and this file used
# to take that whole run down with "Interrupted: 3 errors during collection" -
# an unrelated suite failing to start because of a test it was never going to
# be able to run.
#
# `sys.path` likewise: `probe` sits beside this file, which `python -m unittest`
# from this directory supplies for free and a root-level collection does not.
import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:      # pragma: no cover - environment, not logic
    raise unittest.SkipTest(
        f"Playwright is not importable here ({exc}). This test needs the system "
        f"python, per tools/ui_probe/README.md; run it with "
        f"`cd tools/ui_probe && python -m unittest test_probe_isolation`.")

import probe  # noqa: E402
from probe import _run_isolated_route  # noqa: E402

HERE = Path(__file__).resolve().parent


HTML = b"""<!doctype html><header>ASTRODECK</header>
<main><h1>Fixture ready</h1><p>This is a synthetic page for checking route isolation.
It deliberately starts a delayed network failure and console error on its first
route. The next route must keep its login storage and report only its own errors.</p></main>
<script>
if(location.hash === '#first') {
  localStorage.setItem('probe-login', 'yes');
  document.cookie = 'probe-login=yes;path=/';
  setTimeout(() => fetch('/old-missing'), 950);
  setTimeout(() => console.error('previous route late error'), 2100);
} else {
  if(localStorage.getItem('probe-login') !== 'yes' || !document.cookie.includes('probe-login=yes'))
    document.body.textContent = 'Lost login';
  if(location.hash === '#second') {
    console.error('current route startup error');
    fetch('/own-missing');
  }
}
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/old-missing':
            time.sleep(1.6)
        missing = self.path.endswith('missing')
        self.send_response(404 if missing else 200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        try:
            self.wfile.write(b'missing' if missing else HTML)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class RouteIsolationTest(unittest.TestCase):
    def test_late_errors_do_not_cross_routes_and_current_startup_errors_survive(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
                # The same runner works with bundled Chromium elsewhere. The
                # browser is the probe's for this width (LaunchTest).
                import os
                browsers = probe._Browsers(p, **(
                    {'channel': 'msedge'} if os.name == 'nt' else {}))
                context = browsers.for_width(800).new_context(viewport={'width': 800, 'height': 800})
                base = f'http://127.0.0.1:{server.server_port}'
                def run(name):
                    return _run_isolated_route(context, base, {
                        'name': name, 'url': f'/#{name}', 'marker': 'Fixture ready'},
                        Path(temp), 800)
                try:
                    first = run('first')
                    second = run('second')
                    clean = run('clean')
                    self.assertTrue(first['passed'], first['reasons'])
                    self.assertTrue(second['marker_ok'], second['reasons'])
                    self.assertFalse(second['passed'])
                    self.assertIn('current route startup error', second['console_errors'])
                    self.assertNotIn('previous route late error', second['console_errors'])
                    self.assertEqual([r['url'].split('/')[-1] for r in second['failed_requests']],
                                     ['own-missing'])
                    self.assertTrue(clean['passed'], clean['reasons'])
                    self.assertEqual(context.pages, [], 'route pages must always be closed')
                finally:
                    browsers.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


# A 284 px column that scrolls, as the classic inspector's is (#469's column,
# `w-[284px] border-l overflow-y-auto`, and index.css's `scrollbar-width: thin`
# on every element), with a border on its left only.
SCROLLER = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
</head><body><div id="column" style="width:284px;height:300px;overflow-y:auto;
border-left:1px solid #888;scrollbar-width:thin"><div style="height:1200px">rows</div></div>
</body></html>"""

# The width the column's scrollbar takes: its border box less its client width
# and its one border.
BAR_JS = """() => { const e = document.getElementById('column');
  return e.offsetWidth - e.clientWidth - 1; }"""


class LaunchTest(unittest.TestCase):
    """#535: Playwright passes Chromium `--hide-scrollbars` unless told not to,
    so every walk measured a scrolling desktop column one bar too wide, and
    #469's select passed whole and was cut on the operator's screen."""

    def test_a_desktop_walk_draws_scrollbars_and_a_phone_walk_keeps_the_default(self):
        """The bar a desktop walk measures is the one a desktop browser draws,
        about 10 px thin; on a phone the bar overlays the content, and the
        default launch it keeps measures that too, 0 px.

        Mutation "default launch restored" (probe.py `_launch_args`: returns
        {} for every width, as Playwright's default), observed red:
            AssertionError: 0 not greater than or equal to 8 : the desktop
            column's scrollbar takes 0 px
        Mutation "phone launched like the desktop" (probe.py `_launch_args`:
        the `is_mobile` branch deleted), observed red:
            AssertionError: {'ignore_default_args': ['--hide-scrollbars']} != {}
        The phone's measurement alone cannot see that one: a phone profile
        overlays its scrollbar whether the flag is passed or not (0 px either
        way, measured 2026-09-29), so it is the arguments that are held."""
        with sync_playwright() as p:
            browsers = probe._Browsers(p)
            try:
                bars = {}
                for width in (390, 1440):
                    context = browsers.context(width)
                    try:
                        page = context.new_page()
                        page.set_content(SCROLLER)
                        bars[width] = page.evaluate(BAR_JS)
                    finally:
                        context.close()
                shared = {"phone and desktop": browsers.for_width(390) is browsers.for_width(1440),
                          "820 and 1440": browsers.for_width(820) is browsers.for_width(1440)}
            finally:
                browsers.close()
        self.assertGreaterEqual(bars[1440], 8, f"the desktop column's scrollbar takes "
                                               f"{bars[1440]} px")
        self.assertEqual(bars[390], 0)
        # What the phone's measurement cannot see (above): its launch keeps
        # Playwright's default, and every desktop width leaves it out.
        self.assertEqual(probe._launch_args(390), {})
        for width in (820, 1440):
            self.assertEqual(probe._launch_args(width),
                             {'ignore_default_args': ['--hide-scrollbars']}, width)
        # CONTROL: one browser per kind of screen, the phone's and the
        # desktops' two different ones.
        self.assertEqual(shared, {"phone and desktop": False, "820 and 1440": True})

    def test_nothing_but_the_probe_launches_a_browser(self):
        """Every walk takes its browser from `_Browsers`: probe.py's `main` and
        every test_probe_*.py here. A test that launched its own Chromium
        would grade the probe's checks in a browser the probe's walks do not
        use, with its scrollbars hidden, and a check that failed on the real
        page would pass there (#535 is that). Read off the source's syntax
        tree, so a docstring that names the call is not a call.

        Mutation "a test launches its own browser" (test_probe_s4.py
        `_Browser.setUpClass`: `cls.browsers = probe._Browsers(cls.pw)` ->
        `cls.browser = cls.pw.chromium.launch(headless=True)`, as every file
        did before H4), observed red:
            AssertionError: Lists differ: [('test_probe_s4.py', '_Browser',
            408)] != []
        (408 being the mutated call's line in that run's copy of the file)."""
        files = sorted(HERE.glob('test_probe_*.py')) + [HERE / 'probe.py']
        # CONTROL: the probe's four test files are among what is read.
        self.assertTrue({'test_probe_isolation.py', 'test_probe_s4.py', 'test_probe_s5_s6.py',
                         'test_probe_s7.py'} <= {f.name for f in files})
        launches = [call for path in files for call in _chromium_launches(path)]
        self.assertEqual([c for c in launches if c[:2] != ('probe.py', '_Browsers')], [])
        # CONTROL: the one launch there is, in probe.py's _Browsers, is found,
        # so the clean answer above is not clean for want of looking.
        self.assertEqual([c[:2] for c in launches], [('probe.py', '_Browsers')])


def _chromium_launches(path: Path):
    """(file, enclosing class or None, line) for every `<x>.chromium.launch(...)`
    call in `path`'s syntax tree."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    classes = [c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'launch'
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == 'chromium'):
            owner = next((c.name for c in classes if any(n is node for n in ast.walk(c))), None)
            yield (path.name, owner, node.lineno)


if __name__ == '__main__':
    unittest.main()
