"""Drive ``renderer/render.js`` in headless Chromium and hand back frames.

This module is the Python half of Task 4's renderer: it serves
``tools/photosphere_sim`` over a loopback HTTP server, opens
``renderer/index.html`` in a Playwright Chromium, hands the page the scene and
the camera, and then asks it for one frame per pose. The page owns every
graphics decision; this module owns the process, the transport and the
conversion back to numpy.

Two things here are not incidental:

- The HTTP server exists because Chromium refuses ES module scripts over
  ``file://``, and ``renderer/index.html`` loads Three.js through an import
  map. It binds an ephemeral port on 127.0.0.1 and serves nothing but this
  directory.
- The MIME types for ``.js``, ``.html`` and ``.json`` are forced rather than
  guessed. ``SimpleHTTPRequestHandler`` asks :mod:`mimetypes`, which on
  Windows reads the registry, where ``.js`` is often ``text/plain``; Chromium
  then refuses the module and the page comes up blank. Forcing them makes the
  renderer behave the same on every machine.

Frames cross the boundary as base64 of packed RGB bytes, top row first: the
page has already flipped the rows WebGL hands back bottom-first and dropped
the alpha. A frame is ``(height, width, 3)`` uint8, the contract's frame.

Nothing here reads the process under test, and nothing here decides what the
picture should contain: that is ``renderer/render.js`` against CONTRACT.md,
and ``tests/test_render_cardinal.py`` scores the result against
:mod:`sim.truth`'s analytic prediction.
"""

from __future__ import annotations

import base64
import functools
import http.server
import json
import threading
from pathlib import Path

import numpy as np

__all__ = ["CHROMIUM_ARGS", "CaseRenderer", "ThreeRenderer", "three_renderer"]

ROOT = Path(__file__).resolve().parent.parent

#: Chromium's launch arguments. Playwright's bundled Chromium already falls
#: back to SwiftShader for WebGL when it is headless with no GPU, but saying
#: so explicitly keeps the renderer off a real driver whose rasterisation
#: rules could differ from one machine to the next: the frames are hashed into
#: a case manifest, so the software rasteriser is the point, not a fallback.
CHROMIUM_ARGS = [
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]

#: How long the page gets to build the 4096 x 2048 background and compile its
#: shaders under a software rasteriser before the driver gives up on it.
LOAD_TIMEOUT_MS = 180_000


class _Handler(http.server.SimpleHTTPRequestHandler):
    """A quiet static handler with the module MIME types nailed down."""

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".html": "text/html",
        ".json": "application/json",
        ".png": "image/png",
    }

    def log_message(self, *args):  # noqa: D102 - silence, not documentation
        return


class _StaticServer:
    """``tools/photosphere_sim`` on an ephemeral loopback port."""

    def __init__(self, directory: Path):
        handler = functools.partial(_Handler, directory=str(directory))
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="photosphere-sim-http", daemon=True)
        self._thread.start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5.0)


def _scene_payload(scene) -> dict:
    """The part of a :class:`sim.scene.Scene` the page needs, as plain JSON."""
    return {
        "name": scene.name,
        "seed": scene.seed,
        "background": scene.background,
        "landmarks": scene.landmarks,
        "objects": scene.objects,
        "surface_landmarks": scene.surface_landmarks,
    }


def _camera_payload(camera) -> dict:
    return {
        "width": camera.width,
        "height": camera.height,
        "fov_short_deg": camera.fov_short_deg,
        "fx": camera.fx,
        "fy": camera.fy,
        "cx": camera.cx,
        "cy": camera.cy,
    }


def _three_version() -> str:
    """The installed ``three`` version, or the version this package asks for."""
    installed = ROOT / "node_modules" / "three" / "package.json"
    if installed.is_file():
        return str(json.loads(installed.read_text(encoding="utf-8"))["version"])
    declared = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    return str(declared.get("devDependencies", {}).get("three", "unknown"))


class ThreeRenderer:
    """A loaded scene in a headless Chromium, rendered one pose at a time.

    Construction starts the server and the browser and loads the scene, so a
    machine without a usable Chromium raises here rather than at the first
    frame. Usable as a context manager; :meth:`close` is idempotent.
    """

    def __init__(self, scene, camera, *, args=None):
        self.camera = camera
        self._closed = False
        self._server = None
        self._playwright = None
        self._browser = None
        self._page = None
        self._chromium_version = None
        self._console: list[str] = []
        self.diagnostics: dict = {}
        try:
            self._start(scene, camera, CHROMIUM_ARGS if args is None else list(args))
        except BaseException:
            self.close()
            raise

    # -- lifecycle ---------------------------------------------------------

    def _start(self, scene, camera, args) -> None:
        from playwright.sync_api import sync_playwright

        self._server = _StaticServer(ROOT)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True, args=args)
        self._chromium_version = self._browser.version
        self._page = self._browser.new_page(viewport={"width": 640, "height": 480})
        self._page.set_default_timeout(LOAD_TIMEOUT_MS)
        self._page.on("console", lambda message: self._console.append(
            f"{message.type}: {message.text}"))
        self._page.on("pageerror", lambda error: self._console.append(f"pageerror: {error}"))
        self._page.goto(f"{self._server.origin}/renderer/index.html",
                        wait_until="load", timeout=LOAD_TIMEOUT_MS)
        self._page.wait_for_function(
            "() => window.simRender !== undefined || window.simLoadError !== undefined",
            timeout=LOAD_TIMEOUT_MS)
        load_error = self._page.evaluate("() => window.simLoadError || null")
        if load_error:
            raise RuntimeError(f"renderer/render.js failed to load: {load_error}")
        self.diagnostics = self._page.evaluate(
            "([s, c]) => window.simRender.load(s, c)",
            [_scene_payload(scene), _camera_payload(camera)])

    def __enter__(self) -> "ThreeRenderer":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        for name, shut in (("_browser", lambda b: b.close()),
                           ("_playwright", lambda p: p.stop()),
                           ("_server", lambda s: s.close())):
            handle = getattr(self, name, None)
            if handle is None:
                continue
            try:
                shut(handle)
            except Exception:  # noqa: BLE001 - closing must not mask the real error
                pass
            setattr(self, name, None)
        self._page = None

    # -- rendering ---------------------------------------------------------

    @property
    def version(self) -> dict:
        """What the manifest records: the Three.js and Chromium versions."""
        return {"three": _three_version(), "chromium": self._chromium_version}

    @property
    def console(self) -> list:
        """Everything the page logged, for a failure that needs explaining."""
        return list(self._console)

    def render(self, position, basis) -> np.ndarray:
        """The frame this camera sees from ``position`` in attitude ``basis``.

        ``(height, width, 3)`` uint8, top row first, exactly the frame a case
        directory stores.
        """
        if self._closed or self._page is None:
            raise RuntimeError("this ThreeRenderer is closed")
        position = np.asarray(position, dtype=np.float64)
        pose = {
            "position": [float(v) for v in position],
            "right": [float(v) for v in basis.right],
            "up": [float(v) for v in basis.up],
            "forward": [float(v) for v in basis.forward],
        }
        encoded = self._page.evaluate("(p) => window.simRender.render(p)", pose)
        raw = base64.b64decode(encoded)
        expected = self.camera.height * self.camera.width * 3
        if len(raw) != expected:
            raise RuntimeError(f"renderer returned {len(raw)} bytes, expected {expected}")
        return np.frombuffer(raw, dtype=np.uint8).reshape(
            self.camera.height, self.camera.width, 3).copy()


class CaseRenderer:
    """:func:`sim.cases.build_case`'s ``renderer`` argument, Three.js backed.

    One browser for the whole case: the scene is loaded once and each frame is
    one round trip. It remembers the versions it rendered with so that
    ``build_case`` can put them in the manifest, which is the only reason this
    is an object rather than a plain generator function.
    """

    def __init__(self):
        self.versions: dict = {}

    def __call__(self, scene, camera, frames):
        with ThreeRenderer(scene, camera) as renderer:
            self.versions = dict(renderer.version)
            for frame in frames:
                yield renderer.render(frame.position, frame.basis)


#: The name Task 4's contract exposes. It keeps the versions of its last run,
#: so a caller that needs them for two cases at once builds its own.
three_renderer = CaseRenderer()
