"""Simulated guider: produces a believable guide-error stream."""
from __future__ import annotations

import asyncio
import math
import random
import time
from collections import deque

from ..events import bus
from .base import Guider, GuideStats


class SimGuider(Guider):
    name = "Sim Guider"

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._samples: deque[dict] = deque(maxlen=300)
        self._guiding = False
        self.dither_count = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        await self.stop_guiding()
        self.connected = False

    async def start_guiding(self) -> None:
        if self._guiding:
            return
        await asyncio.sleep(2.0)  # "calibrating"
        self._guiding = True
        self._task = asyncio.create_task(self._loop())
        bus.log("info", "sim guider calibrated and guiding", "guide")

    async def stop_guiding(self) -> None:
        self._guiding = False
        if self._task:
            self._task.cancel()
            self._task = None
        bus.publish("guide", **self.stats().__dict__)

    async def dither(self, pixels: float = 3.0) -> None:
        self.dither_count += 1
        # kick then settle
        for decay in (1.0, 0.55, 0.25, 0.1):
            self._push(kick=pixels * decay)
            await asyncio.sleep(0.8)
        await asyncio.sleep(1.5)

    def _push(self, kick: float = 0.0) -> None:
        t = time.time()
        ra = random.gauss(0, 0.45) + 0.3 * math.sin(t / 47) + kick * random.choice((-1, 1))
        dec = random.gauss(0, 0.38) + kick * 0.6 * random.choice((-1, 1))
        self._samples.append({"t": t, "ra": round(ra, 3), "dec": round(dec, 3)})

    async def _loop(self) -> None:
        while self._guiding:
            self._push()
            bus.publish("guide", **self.stats().__dict__)
            await asyncio.sleep(1.0)

    def stats(self) -> GuideStats:
        recent = list(self._samples)
        if recent:
            ras = [s["ra"] for s in recent[-100:]]
            decs = [s["dec"] for s in recent[-100:]]
            rms_ra = math.sqrt(sum(r * r for r in ras) / len(ras))
            rms_dec = math.sqrt(sum(d * d for d in decs) / len(decs))
        else:
            rms_ra = rms_dec = 0.0
        return GuideStats(
            guiding=self._guiding,
            rms_ra=round(rms_ra, 2), rms_dec=round(rms_dec, 2),
            rms_total=round(math.hypot(rms_ra, rms_dec), 2),
            snr=22.0, recent=recent[-120:],
        )
