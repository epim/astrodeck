# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""MERGE-TIME CONTRACT between P4 (#860, a goto that did not arrive) and P2
(#852, a centring miss is not imaged on): seam S4 of DESIGN-P2.

P4's ``hub.goto_and_center`` turns the driver's ``GotoNotArrived`` into a
not-centred result carrying ``goto_not_arrived: True`` (its ``arrival_keys``).
The engine reads that key (`SequenceEngine._unmoved`) and stops the target
with ``CENTRING_NOT_ARRIVED`` before its no-light arm. If either side renames
the key, this goes red instead of the result silently falling through to the
hold for light.

SKIPPED until P4 lands (``GotoNotArrived`` is not importable before it); the
orchestrator's merge checklist names this file, so the merge cannot skip it
silently.

Drives the REAL ``Hub`` on the simulator, the REAL ``goto_and_center`` and
the REAL ``_setup_target``; only the sim telescope's ``slew`` (raising P4's
real exception on every call) and the solver (the field 2.5 degrees north of
the target) are stand-ins. Named mutant: `_unmoved` reading
``"goto_did_not_arrive"`` instead of ``"goto_not_arrived"`` -> RED.
"""
from __future__ import annotations

import pytest

base = pytest.importorskip("astrodeck.devices.base")
GotoNotArrived = getattr(base, "GotoNotArrived", None)
pytestmark = pytest.mark.skipif(
    GotoNotArrived is None,
    reason="P4's GotoNotArrived has not landed; run at merge (seam S4)")

import astrodeck.flows.tonight as tonight_mod  # noqa: E402
from _simhub import sim_hub  # noqa: E402,F401 (fixture import)
from astrodeck.hub import Hub  # noqa: E402
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target  # noqa: E402
from astrodeck.sequence.engine import CENTRING_NOT_ARRIVED, StopTarget  # noqa: E402

from test_850_hub_sync_refused import DEC, FAR_DEC, FAR_RA, RA, _use_solver  # noqa: E402


async def test_a_goto_that_did_not_arrive_stops_at_acquisition(
        sim_hub, monkeypatch, bus_lines):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    tel = sim_hub.devices["telescope"]

    async def slew(ra_hours, dec_deg):
        raise GotoNotArrived("the goto did not arrive",
                             reason="the mount stopped short of the target",
                             residual_deg=2.5)
    monkeypatch.setattr(tel, "slew", slew)
    t = Target(name="Fictional N", ra_hours=RA, dec_deg=DEC, center=True,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    e = SequenceEngine(sim_hub)
    e._cfg = None
    e.plan = SequencePlan(name="p4", guide=False, meridian_flip=False,
                          safety_check=False, targets=[t])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == f"centring at acquisition: {CENTRING_NOT_ARRIVED}"
    assert not any("holding for light" in m for _l, m, _s in bus_lines)
