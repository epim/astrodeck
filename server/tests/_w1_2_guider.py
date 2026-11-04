# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A guider for the WP 1.2 tests: it publishes timestamped samples.

The clocked simulator's `ScriptedGuider` answers every `stats()` with an empty
`GuideStats`, which is right for the tests that only script a start. The
per-exposure guide verdict (#1096, #1107) reads the guider's own timestamped
error samples, so this one publishes them: one per second on the night's fake
clock, ``HISTORY`` of them, each carrying ``err(t)`` arcsec on RA and nothing
on Dec, exactly as the native guider's ``recent`` list is shaped. Its
``rms_total`` is the RMS of everything it holds, the rolling window the engine
used to judge a frame on.
"""
from __future__ import annotations

import math
from typing import Callable

from astrodeck.guide.base import GuideStats

from _group_harness import ScriptedGuider

#: How many one-second samples the guider holds, as the native guider's
#: ``stats()`` caps its list.
HISTORY = 120


class SampledGuider(ScriptedGuider):
    """``err(t)`` is the RA error in arcsec at fake time ``t``.

    ``clock`` is the night's fake clock (set by `bind`). ``cleared_by_start``
    makes every start drop the history, as the native engine's
    ``reset_guiding_state`` does: after a start only the samples taken from the
    start's own instant on exist.
    """

    name = "sampled guider"

    def __init__(self, err: Callable[[float], float], *,
                 cleared_by_start: bool = False,
                 phase_when_idle: str = "idle") -> None:
        super().__init__()
        self.err = err
        self.cleared_by_start = cleared_by_start
        self.phase_when_idle = phase_when_idle
        self.clock: Callable[[], float] = lambda: 0.0
        self.since: float | None = None
        self.started_at: list[float] = []
        self.lost = False

    def bind(self, night) -> None:
        self.clock = night.clock.time

    async def start_guiding(self) -> None:
        await super().start_guiding()
        self.lost = False
        self.started_at.append(self.clock())
        if self.cleared_by_start:
            self.since = math.floor(self.clock()) - 1.0

    def stats(self) -> GuideStats:
        now = math.floor(self.clock())
        times = [float(t) for t in range(now - HISTORY + 1, now + 1)
                 if self.since is None or t >= self.since]
        recent = [{"t": t, "ra": float(self.err(t)), "dec": 0.0}
                  for t in times]
        rms = (math.sqrt(sum(r["ra"] ** 2 for r in recent) / len(recent))
               if recent else 0.0)
        if self.lost:
            phase = "lost"
        elif self.active:
            phase = "guiding"
        else:
            phase = self.phase_when_idle
        return GuideStats(guiding=self.active, rms_ra=rms, rms_total=rms,
                          recent=recent, is_arcsec=True, phase=phase)
