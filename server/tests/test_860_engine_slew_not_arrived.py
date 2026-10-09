# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#860 (engine seam): a centring-off target whose goto does not arrive is
skipped, not imaged and not a run error (ruling R2).

``_setup_target``'s centring-off branch slews straight to the target and
images. The AM5 driver now raises ``GotoNotArrived`` when the mount accepted
the goto and stopped short (or a stop was sent); unhandled, that reached the
engine's generic handler and ended the night. The engine now logs ONE
warning with the figure and raises ``StopTarget`` in fixed words (D-03). A
plain ``DeviceError`` from the slew is NOT this arm's and still propagates.

These drive the REAL ``SequenceEngine._setup_target`` over the recording hub
double of ``test_centring_settings_reach_goto``, with the telescope's
``slew`` (the real signature) raising. Each test names the mutant of
engine.py it was shown red under; each mutant was applied to a byte copy and
the file restored from that copy (sha256 checked).
"""
from __future__ import annotations

import re

import pytest

from test_850_hub_sync_refused import _humanizer_rewrites
from test_centring_settings_reach_goto import _setup, _target

from astrodeck.devices.base import DeviceError, GotoNotArrived
from astrodeck.hub import GOTO_NOT_ARRIVED_REASON
from astrodeck.sequence.engine import StopTarget

_STALLED = "the mount stopped short of the target"


def _raising_slew(hub, exc: BaseException) -> None:
    async def slew(ra_hours, dec_deg):
        hub.tel.calls.append(("slew", ra_hours, dec_deg))
        raise exc

    hub.tel.slew = slew


async def test_a_centring_off_goto_that_does_not_arrive_skips_the_target(
        bus_lines):
    """The goto stalled: ``StopTarget`` in the fixed words, no digit in it,
    and one ``sequence`` warning carrying the driver's words and the
    separation, inside the UI's cut and safe from its humanizer.

    NAMED MUTANT E-M1 "seam removed" (the ``except GotoNotArrived`` arm made
    ``except ZeroDivisionError``): RED, ``GotoNotArrived`` escapes instead of
    ``StopTarget``.
    """
    t = _target(center=False)
    e, hub = _setup(t)
    _raising_slew(hub, GotoNotArrived("Mount: goto did not arrive",
                                      reason=_STALLED, residual_deg=3.21))

    with pytest.raises(StopTarget) as info:
        await e._setup_target(0, t)

    said = str(info.value)
    assert said == f"slew at acquisition: {GOTO_NOT_ARRIVED_REASON}", said
    assert not re.search(r"\d", said), said
    assert hub.gotos == [], "premise: the centring-off branch"
    warnings = [(m, s) for lvl, m, s in bus_lines
                if lvl == "warning" and "the goto did not arrive" in m]
    assert warnings == [(f"NGC 7331: slew at acquisition: the goto did not "
                         f"arrive ({_STALLED}, 3.21 deg off)", "sequence")], \
        warnings
    line = warnings[0][0]
    assert len(line) <= 137, (len(line), line)
    assert not _humanizer_rewrites(line), line
    assert not _humanizer_rewrites(said), said


async def test_control_a_plain_slew_failure_still_propagates(bus_lines):
    """CONTROL. A plain ``DeviceError`` from the slew is not a goto that did
    not arrive: it propagates unchanged, as before.

    NAMED MUTANT E-M2 "seam too wide" (``except GotoNotArrived`` ->
    ``except DeviceError``): RED, ``AttributeError: 'DeviceError' object has
    no attribute 'residual_deg'`` instead of the ``DeviceError``.

    NAMED MUTANT E-M2b, the same widening written so it cannot crash (the
    arm reads ``residual_deg`` and ``reason`` with ``getattr``): RED,
    ``StopTarget: slew at acquisition: the goto did not arrive, so the tube
    is not on the target`` instead of the ``DeviceError``.
    """
    t = _target(center=False)
    e, hub = _setup(t)
    plain = DeviceError("Mount: slew failed to settle within 120s")
    _raising_slew(hub, plain)

    with pytest.raises(DeviceError) as info:
        await e._setup_target(0, t)

    assert info.value is plain
    assert not isinstance(info.value, StopTarget)
