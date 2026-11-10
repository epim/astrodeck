# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.2 fix round 1: two small claims.

#817. When the wheel cannot be returned after a solve, the warning used to say
"the next frame's filter move will correct it". That corrects the WHEEL, not
the focus: the next ``_apply_filter`` reads the wheel's real slot (the solve
filter), takes the offset delta from it, and applies that to a focuser still
focused for the ORIGINAL filter, so the frame can be out of focus. The line
now says what will happen.

#1108. ``run_autofocus`` forwards a ``frame_context`` to ``run_native_autofocus``
(the sweep judge's classifier context, WP 2.2), so a caller that holds the
camera's no-light reference can have a sweep classify ``no_light``.

MUTANT RUN, 2026-11-04, from a byte backup of ``astrodeck/hub.py`` and of
``astrodeck/focus/autofocus.py``, restored and sha256-verified: the old
wording put back; ``frame_context`` dropped from the forwarded call. They fail
with the assertions quoted in the report of the round.
"""
from __future__ import annotations

import types

import pytest

import astrodeck.focus.native as native_mod
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus import run_autofocus
from astrodeck.hub import Hub
from astrodeck.imaging.frame_class import FrameContext


class _StuckWheel:
    connected = True
    filter_names = ["L", "R", "G", "B"]

    async def set_position(self, slot):
        raise RuntimeError("the wheel did not answer")


async def test_a_failed_wheel_return_does_not_promise_a_correction():
    hub = Hub()
    hub.devices["filterwheel"] = _StuckWheel()
    q = bus.subscribe()
    try:
        await hub._return_wheel_after_solve(2)
        lines = []
        while not q.empty():
            ev = q.get_nowait()
            msg = str(ev.data.get("message", "")) if ev.type == "log" else ""
            if "could not return the wheel" in msg:
                lines.append(msg)
    finally:
        bus.unsubscribe(q)
    assert len(lines) == 1, lines
    line = lines[0]
    assert "will correct it" not in line, line
    assert "G" in line
    assert "turns it back" in line and "out of focus" in line, line


async def test_a_wheel_a_cancel_could_not_send_back_does_not_promise_a_correction():
    """The cancel-restore warning made the same promise as the after-solve
    one (#817): the next filter move turns the wheel back, not the focus."""
    hub = Hub()
    q = bus.subscribe()
    try:
        await hub._restore_wheel_after_cancel(_StuckWheel(), 1)
        lines = []
        while not q.empty():
            ev = q.get_nowait()
            msg = str(ev.data.get("message", "")) if ev.type == "log" else ""
            if "could not be sent back" in msg:
                lines.append(msg)
    finally:
        bus.unsubscribe(q)
    assert len(lines) == 1, lines
    line = lines[0]
    assert "will correct it" not in line, line
    assert "R" in line
    assert "turns it back" in line and "out of focus" in line, line


@pytest.mark.native
async def test_run_autofocus_forwards_its_frame_context_to_the_native_sweep(
        monkeypatch):
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    reached = {}

    async def native(camera, focuser, **kw):
        reached.update(kw)
        return "native-result"

    monkeypatch.setattr(native_mod, "run_native_autofocus", native)
    context = FrameContext()

    result = await run_autofocus(cam, foc,
                                 provider=types.SimpleNamespace(kind="astrodeck"),
                                 frame_context=context)

    assert result == "native-result"
    assert reached.get("frame_context") is context
