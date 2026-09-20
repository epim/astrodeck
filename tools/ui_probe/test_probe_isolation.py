"""Real-browser regression for #31. Run with the probe's Playwright Python.

Uses only a temporary loopback fixture; no AstroDeck server or rig needed.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest

from playwright.sync_api import sync_playwright

from probe import _run_isolated_route


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
                # The same runner works with bundled Chromium elsewhere.
                import os
                browser = p.chromium.launch(headless=True, **(
                    {'channel': 'msedge'} if os.name == 'nt' else {}))
                context = browser.new_context(viewport={'width': 800, 'height': 800})
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
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
