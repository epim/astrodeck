"""Polar alignment session: sim convergence + NINA message parsing."""
import asyncio

import pytest

from astrodeck.hub import Hub


async def _wait(predicate, timeout=45.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    return False


async def test_sim_polar_converges():
    h = Hub()  # mode "none" → sim polar driver
    try:
        await h.polar.start()
        assert h.polar.running
        assert await _wait(lambda: h.polar.state["state"] == "done")
        assert h.polar.state["total_error"] < 1.0
        assert h.polar.state["progress"] == 1.0
    finally:
        await h.polar.stop()


async def test_sim_polar_stop():
    h = Hub()
    await h.polar.start()
    await asyncio.sleep(0.5)
    await h.polar.stop()
    assert not h.polar.running
    assert h.polar.state["state"] == "idle"


def test_nina_tppa_parsing_deg_to_arcmin():
    h = Hub()
    sess = h.polar
    # NINA reports degrees; 0.05° == 3.0′, 0.03° == 1.8′
    sess._handle_nina({"Response": {"AzimuthError": 0.05, "AltitudeError": 0.03,
                                    "TotalError": 0.058}})
    assert abs(sess.state["az_error"] - 3.0) < 0.05
    assert abs(sess.state["alt_error"] - 1.8) < 0.05
    assert abs(sess.state["total_error"] - 3.5) < 0.1
    assert sess.state["state"] == "running"


def test_nina_tppa_status_progress():
    h = Hub()
    sess = h.polar
    sess._handle_nina({"Response": {"Status": "measuring point 2/3", "Progress": 0.4}})
    assert sess.state["message"] == "measuring point 2/3"
    assert sess.state["progress"] == 0.4
