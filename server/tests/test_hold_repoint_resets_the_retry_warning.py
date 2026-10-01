"""A hold's re-point from elsewhere starts a new idle spell, warning included
(#189 item 10 (a)).

The idle park-hold says a stop the mount did not confirm ONCE per idle spell
(``_idle_hold_retrying``, `_idle_stop_retry`), and `_setup_target` ends the
spell and lowers the flag with the latch. A cloud hold's re-point from
elsewhere is a new acquisition, booked as one (`_hold_repoint`): it re-opens
the idle latch and restarts the idle clock, and it lowers the flag too.
Without that, the next spell's unconfirmed stop, on the target the hold
pointed at, would go unsaid: the flag would still be up from the spell
before, so a mount that will not stop would track on, unwatched, with no line
in the night log to say so.

Held here through its observable, the warning, and not the flag: two idle
spells whose stops the mount refuses, with the hold's re-point between them,
say the unconfirmed stop twice.

THE HARNESS is the simulator's mount with ``set_tracking(False)`` refused
(the call raises the timeout a dead link produces, and the mount goes on
tracking), the retry interval cut to a fraction of a real second, and the
real `_idle_stop_retry` task. The site is a fixture.
"""
from __future__ import annotations

import asyncio
import time

import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.sequence import SequenceEngine, SequencePlan

from test_hold_ends_the_idle_stop_retry import _target
from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _ra_at, _unconfirmed_lines, sim_hub, temp_store)


async def _warned(lines, n: int) -> None:
    """Wait, briefly and for real, until ``n`` unconfirmed-stop warnings
    have been said, or give up quietly and let the assertion say so."""
    for _ in range(400):
        if len(_unconfirmed_lines(lines)) >= n:
            return
        await asyncio.sleep(0.005)


async def test_the_next_spells_unconfirmed_stop_is_said_again_after_a_repoint(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Alpha's idle spell: the stop is refused and said once. A cloud hold
    opened for Bravo points the mount at Bravo (`_hold_repoint` from
    elsewhere), which ends that spell. Bravo's own idle spell then decides a
    stop, and the mount refuses it again: that is said again, because it is
    a new spell.

    Mutant "delete the reset" (``self._idle_hold_retrying = False`` in
    `_hold_repoint`'s elsewhere branch deleted): RED (observed) -
        AssertionError: the second spell's unconfirmed stop went unsaid: 1
        warning(s) over two refused stops, ['the mount did not confirm the
        stop (it still reports tracking) — asking again about once a minute
        until it does']
    """
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.2)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    e = SequenceEngine(sim_hub)
    now = time.time()
    alpha = _target("Alpha", _ra_at(-2.0, now), 60.0)
    bravo = _target("Bravo", _ra_at(-1.0, now), 50.0)
    e.plan = SequencePlan(name="r", guide=False, meridian_flip=False,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    tel = sim_hub.devices["telescope"]
    real_set = tel.set_tracking

    async def set_tracking(on):
        if not on:
            raise asyncio.TimeoutError()      # not taken; still tracking
        return await real_set(on)

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    await real_set(True)
    e._tracked_target = alpha
    try:
        await e._idle_park_hold("Alpha: a long wait")
        await _warned(bus_lines, 1)
        assert len(_unconfirmed_lines(bus_lines)) == 1, (
            "premise: the first spell's unconfirmed stop was said")
        await e._hold_repoint(bravo, elsewhere=True)
        assert e._tracked_target is bravo and e._idle_hold_open, (
            "premise: the re-point booked Bravo as a new acquisition")
        await e._idle_park_hold("Bravo: a long wait")
        await _warned(bus_lines, 2)
        said = _unconfirmed_lines(bus_lines)
        assert len(said) == 2, (
            f"the second spell's unconfirmed stop went unsaid: {len(said)} "
            f"warning(s) over two refused stops, {said[:2]}")
    finally:
        await e._cancel_idle_stop_retry()
