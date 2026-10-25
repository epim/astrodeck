# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""One rule for when a test that needs the real renderer may skip (#954).

Only a browser that cannot start is an environment fact. A page that starts and
then fails to build the scene (a script error in ``render.js`` or
``materials.js``, a texture the recipe cannot make) is the very thing the
renderer tests exist to catch, so that error propagates and the test fails.
"""

from __future__ import annotations

import unittest

from sim import render as render_module

#: The two texts Playwright puts in the error of a browser that will not start.
LAUNCH_FAILURES = ("BrowserType.launch", "Executable doesn't exist")


def open_renderer(scene, camera):
    """A loaded :class:`sim.render.ThreeRenderer`, or a named skip.

    Skips when playwright is not installed or when its Chromium cannot launch,
    and says which. Raises every other error as it came.
    """
    try:
        return render_module.ThreeRenderer(scene, camera)
    except ImportError as error:
        raise unittest.SkipTest(f"playwright is not installed: {error!r}")
    except Exception as error:  # noqa: BLE001 - classified below, never swallowed
        if any(text in str(error) for text in LAUNCH_FAILURES):
            raise unittest.SkipTest(f"headless Chromium unavailable: {error!r}")
        raise
