# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Guiders for the WP 8.1 tests: samples at times the test chooses.

`SampledGuider` (tests/_w1_2_guider.py) publishes one sample per second. The
WP 8.1 cases need the opposite: a guider that stamps a sample only at the
instants a test names (a stalled guide camera, a lone sample inside an
exposure, a guider that has published nothing yet), and one that keeps a long
trace the way `NativeGuider` and `PHD2Guider` now do.

``stamps(now)`` returns every sample time up to ``now`` (fake seconds, any
order); ``err(t)`` is the RA error in arcsec at ``t``. ``trace`` False makes
`guide_samples` absent, so the verdict reads the clipped ``stats().recent``
list, as every guider did before.
"""
from __future__ import annotations

import math
from typing import Callable

from astrodeck.guide.base import GuideStats

from _group_harness import ScriptedGuider

#: The published list, as the real guiders clip theirs.
PUBLISHED = 120


class StampedGuider(ScriptedGuider):
    name = "stamped guider"

    def __init__(self, err: Callable[[float], float],
                 stamps: Callable[[float], list[float]], *,
                 trace: bool = True, figure: float | None = None) -> None:
        super().__init__()
        self.err = err
        self.stamps = stamps
        self.figure = figure
        self.clock: Callable[[], float] = lambda: 0.0
        self.started_at: list[float] = []
        if not trace:
            self.guide_samples = None   # type: ignore[assignment]

    def bind(self, night) -> None:
        self.clock = night.clock.time

    async def start_guiding(self) -> None:
        await super().start_guiding()
        self.started_at.append(self.clock())

    def _all(self) -> list[dict]:
        now = self.clock()
        return [{"t": float(t), "ra": float(self.err(t)), "dec": 0.0}
                for t in sorted(self.stamps(now)) if t <= now]

    def guide_samples(self, lo: float, hi: float):
        return [s for s in self._all() if lo <= s["t"] <= hi]

    def stats(self) -> GuideStats:
        recent = self._all()[-PUBLISHED:]
        if self.figure is not None:
            rms = self.figure
        elif recent:
            rms = math.sqrt(sum(r["ra"] ** 2 for r in recent) / len(recent))
        else:
            rms = 0.0
        return GuideStats(guiding=self.active, rms_ra=rms, rms_total=rms,
                          recent=recent, is_arcsec=True,
                          phase="guiding" if self.active else "idle")
