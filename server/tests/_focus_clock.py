"""What one native autofocus sweep costs, measured on a virtual clock.

THE POINT. A sweep on the rig takes 7 to 9 minutes and nothing in the tree can
say where those minutes go, so no change to the sweep can be shown to have
helped. This runs the REAL host loop (``astrodeck.focus.native.
run_native_autofocus``) against the REAL Rust state machine
(``astrodeck_native.FocusSweep``) with clocked stand-ins for the camera, the
focuser and the two measurement calls, and reports what the run spent.

Nothing here sleeps for real. ``VirtualLoop`` jumps its clock to the earliest
scheduled timer whenever the loop would otherwise wait, so a nine-point sweep
that bills nine minutes of virtual time returns in a fraction of a second. CPU
is billed on the same clock: the loop offloads measurement with
``asyncio.to_thread``, and the replacement installed here runs the (instant)
stub, then sleeps for the cost the stub reported. That is what makes "the
camera exposes while the CPU measures" show up as overlap rather than as a
comment.

WHAT IS STUBBED, and what is deliberately not. ``native.focus_size`` and
``native._native.detect_and_measure`` answer from a V-curve model and bill
their cost scaled by ``arr.size / full_pixels``, so a crop shows up as a
cheaper pass. ``native_sweep_metric`` is NOT stubbed -- a later change puts the
crop inside it, and the harness has to be able to see through it.
``saturation_fraction`` stays real (it runs on the zeros the stand-in camera
returns).

-------------------------------------------------------------------------------
CALIBRATION -- the sweep of 2026-09-07 21:39:38..21:48:49, replayed
-------------------------------------------------------------------------------

Scenario ``sparse_83``: start 11176, step 83, 4 each side, 34 stars, 3.5 px at
focus, 12 steps per px of defocus, 6 s at gain 200 bin 1. The cost model is the
one in ``Cost`` below. Its constants were NOT fitted to this replay -- every one
of them was measured on the rig on 2026-09-08 against real 26 MP frames -- and
the replay is what checks them:

    rust_base_s    25.0   } 67.1 s on a 1999-star frame, 59.9 s on a 1462-star
    rust_per_star_s 0.021 } one, ~27 s on this replay's 34-star probe
    size_fixed_s    0.5   } 9.5 s at 4.6 px and 8.8 s at 4.0 px on the whole
    size_base_s     6.0   } frame (the allowed band was 3..12), 2.2 s and 1.0 s
    size_per_px_s   0.9   } on a 0.4-per-axis window (allowed 0.5..1.5)

    stage                     logged      harness
    ------------------------  ----------  ----------
    probe measured               35 s        33.7 s
    point 1  (11508)            103 s        96.4 s
    point 2  (11425)            177 s       147.1 s
    point 3  (11342)            212 s       191.9 s
    point 4  (11259)            244 s       230.9 s
    point 5  (11176)            283 s       266.3 s
    point 6  (11093)            331 s       305.3 s
    point 7  (11010)            375 s       350.1 s
    point 8  (10927)            418 s       400.8 s
    point 9  (10844)            466 s       457.7 s
    validation (11176)          550 s       502.9 s
    ------------------------  ----------  ----------
    total wall                   550 s       503.2 s   (-8.5 percent)

The per-point residuals are not all small -- the logged run is a real sky, and
its second point cost 23 s more than a clean hyperbola can explain -- but the
shape and the total are right, and the total is the calibration target.

-------------------------------------------------------------------------------
THE STATUS QUO, 2026-09-08, git 93692d3
-------------------------------------------------------------------------------

    metric                 rich_at_focus  rich_turnround       sparse_83         rich_83
    --------------------  --------------  --------------  --------------  --------------
    success                         True            True            True            True
    wall_s                         689.8           700.1           503.2           678.1
    exposures                         11              12              11              11
    wasted_frames                      0               1               0               0
    rust_passes                       11              11              11              11
    rust_mean_frac                  1.00            1.00            1.00            1.00
    size_passes                       10              10              10              10
    size_mean_frac                  1.00            1.00            1.00            1.00
    moves                             11              12              11              11
    total_steps                     5601            8395            1330            1330
    reversals                          2               3               3               3
    camera_idle_s                  543.2           544.9           378.4           531.8
    cpu_s                          660.9           650.1           477.3           652.2
    final_commanded                11201           11905           11176           11200
    final_physical                 11161           11905           11137           11161
    physical_error_steps             -39               5             -39             -39
    approach_of_final                out              in              in              in

    rich_at_focus:  12600:29.04 12250:22.48 11900:15.10 11550:7.86 11200:2.50
                    10850:7.86 10500:15.10 10150:22.48 9800:29.89 11201:2.63
    rich_turnround: 12600:14.26 12250:7.86 11900:2.50 11550:7.86 11200:15.10
                    10850:22.48 10500:29.89 12950:21.63 13300:29.04 11905:2.50
    sparse_83:      11508:24.58 11425:21.04 11342:14.27 11259:7.75 11176:3.50
                    11093:7.75 11010:14.27 10927:21.04 10844:27.89 11177:4.78
    rich_83:        11532:24.58 11449:21.04 11366:14.27 11283:7.75 11200:3.50
                    11117:7.75 11034:14.27 10951:21.04 10868:27.89 11201:4.78

Read it as: eleven exposures hold the shutter open for 66 s of an 11-minute
run, every measured frame pays a FULL-FRAME Rust pass (``rust_mean_frac`` 1.00
on every scenario) whose per-star half is the biggest single cost on a rich
field, and the camera is idle for four fifths of the run. The rich sweeps cost
180 s more than the sparse one for the same nine points, all of it in the
detector. ``rich_turnround`` pays one exposure it never measures (the
predictor's one-strike rule) and 8395 steps of travel against 5601 for the same
sweep centred.

FOUR THINGS THE LOOP DOES THAT THE PLAN DID NOT PREDICT, all visible above:

1. The FIRST point of every sweep is measured 40 steps low. The initial move to
   the top of the window is the run's only OUT move before the validation, so
   it eats the whole backlash -- 12600 reads 29.04 px where the model says
   29.79, and 11508 reads 24.58 where the model says 27.89. That distortion is
   why the fitted vertex lands 1 to 5 steps off a focus the model puts exactly
   on a swept point.
2. ``sparse_83`` and ``rich_83`` end with ``approach_of_final`` IN, not OUT.
   The validation frame is exposed at the vertex reached moving OUT, so the
   tube is 40 steps short and the frame reads 4.78 px against the 3.50 already
   measured one step away -- 36 percent worse, which trips ``confirmed_best``'s
   15 percent gate. The loop then overrides the fit and moves ONE step back in.
   The backlash causes the override, and the override does not cure the
   backlash: a 1-step reversal turns the motor and not the tube, so the final
   physical error is still -39.
3. ``rich_turnround`` ends its validation move going DOWN (the engine extends
   left to quota first, so the last swept point sits above the vertex), so its
   approach is IN and its physical error is +5, not -40. The backlash penalty
   belongs to sweeps that end BELOW their vertex -- which is every centred one.
4. ``rust_passes == size_passes + 1`` on every scenario: the probe pays a Rust
   pass and no ``focus_size``, every point pays both.

Run it: ``python tests/_focus_clock.py`` from ``server/``.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_HERE)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

# ``resolve_sweep`` reads -- and a successful sweep WRITES -- a focus
# calibration file under ``astrodeck.config.CONFIG_DIR``, which is fixed from
# ASTRODECK_CONFIG_DIR at import time. Point it at a throwaway directory before
# astrodeck.config can be imported, so running this harness can never touch the
# developer's server/config/. Under pytest the session fixture in conftest.py
# repoints CONFIG_DIR again; this is what protects the script path.
if not (os.environ.get("ASTRODECK_CONFIG_DIR") or "").strip():
    os.environ["ASTRODECK_CONFIG_DIR"] = tempfile.mkdtemp(prefix="focus-clock-")

import numpy as np                                              # noqa: E402

from astrodeck.devices.base import Camera, CameraFrame, Focuser  # noqa: E402
from astrodeck.focus import native as _host                      # noqa: E402


# --------------------------------------------------------------- virtual clock

class VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock is a number, not the wall.

    Everything the sweep waits for is a timer -- an exposure, a focuser move,
    the billed cost of a measurement -- so when nothing is ready to run and a
    timer is scheduled, the honest answer is "that timer is next" and the clock
    can simply be moved there. Concurrency is preserved exactly: two tasks
    sleeping 5 s each still both finish at virtual time 5.

    ``SelectorEventLoop`` rather than the proactor loop because this needs
    nothing but timers and the self-pipe, and the selector loop is the one that
    computes its select timeout from ``self.time()``.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._virtual_time = 0.0

    def time(self) -> float:
        return self._virtual_time

    def _run_once(self) -> None:
        # Only when there is nothing runnable: a jump while callbacks are still
        # pending would run them at a time that has already moved on.
        if not self._ready and self._scheduled and not self._stopping:
            # Cancelled timers at the head are not a reason to advance the
            # clock. Mirrors BaseEventLoop._run_once's own purge (including the
            # counter it keeps) so the base's bookkeeping stays consistent.
            while self._scheduled and self._scheduled[0]._cancelled:
                self._timer_cancelled_count -= 1
                handle = heapq.heappop(self._scheduled)
                handle._scheduled = False
            if self._scheduled:
                when = self._scheduled[0]._when
                if when > self._virtual_time:
                    self._virtual_time = when
        super()._run_once()


def run_on_virtual_clock(coro_factory):
    """Run ``coro_factory()`` on a fresh :class:`VirtualLoop`; return
    ``(result, virtual_seconds_elapsed)``.

    Takes a factory, not a coroutine, because the coroutine must be created
    inside the runner -- a coroutine built against no loop and then abandoned
    if the runner raises is a warning nobody needs.
    """
    box: dict = {}

    async def _wrapper():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            box["result"] = await coro_factory()
        finally:
            box["elapsed"] = loop.time() - t0
        return box["result"]

    with asyncio.Runner(loop_factory=VirtualLoop) as runner:
        runner.run(_wrapper())
    return box["result"], box["elapsed"]


class Bill:
    """Virtual CPU seconds a measurement stub has run up, waiting to be charged.

    The stubs are instant; the replacement ``asyncio.to_thread`` reads what the
    call cost off here and sleeps for it, which is what puts the cost on the
    clock with the right concurrency (the speculative exposure runs underneath).
    """

    def __init__(self) -> None:
        self.accrued = 0.0
        self.total = 0.0
        #: (what was offloaded, what it cost, when it finished) -- the run's
        #: own timeline, which is what makes the 2026-09-07 replay checkable
        #: instead of quoted.
        self.timeline: list[tuple[str, float, float]] = []

    def charge(self, seconds: float) -> None:
        self.accrued += float(seconds)
        self.total += float(seconds)

    def take(self) -> float:
        owed, self.accrued = self.accrued, 0.0
        return owed


def clocked_to_thread(bill: Bill):
    """The stand-in for ``asyncio.to_thread``: run it now, bill it on the clock.

    The real one hands ``fn`` to a worker thread and awaits the result. Here
    ``fn`` is a stub that returns immediately, so the *duration* has to come
    from somewhere else -- the bill the stub wrote its own cost onto. The
    ``await`` is what lets the speculative exposure overlap it.
    """

    async def to_thread(fn, /, *args, **kwargs):
        result = fn(*args, **kwargs)
        charge = bill.take()
        await asyncio.sleep(charge)
        bill.timeline.append((getattr(fn, "__name__", repr(fn)), charge,
                              asyncio.get_running_loop().time()))
        return result

    return to_thread


@contextlib.contextmanager
def installed(*pairs):
    """``(obj, name, value)`` triples, set for the block and restored after.

    Deliberately not ``monkeypatch``: this module has to work as a script as
    well as under pytest, and a scenario that left ``asyncio.to_thread``
    replaced would poison every test that ran after it.
    """
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in pairs]
    try:
        for obj, name, value in pairs:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)


# ------------------------------------------------------------------ cost model

@dataclass
class Cost:
    """What each part of a point costs, in seconds. Rig-calibrated; see the
    module docstring for the sweep these came from."""

    #: Sensor readout + transfer, added to every exposure's own duration.
    download_s: float = 2.0
    #: Focuser move: a fixed settle plus distance / rate.
    move_fixed_s: float = 0.3
    move_rate_steps_per_s: float = 700.0
    #: ``detect_and_measure``: a per-pixel part (25 s for the whole 26 MP
    #: frame) plus a per-STAR part, because the detector pays again for every
    #: source it measures. Both measured on the rig 2026-09-08: 67.1 s on a
    #: 1999-star frame, 59.9 s on a 1462-star one, and ~27 s on the 34-star
    #: probe of the 2026-09-07 replay. A 0.4-per-axis window of the 1999-star
    #: frame held 569 stars and cost 14.3 s.
    rust_base_s: float = 25.0
    rust_per_star_s: float = 0.021
    #: ``focus_size``: a fixed part, plus a part that scales with the frame AND
    #: with how big the stars are (the binned pyramid walks further out).
    #: Measured on the rig 2026-09-08: 9.5 s at 4.6 px and 8.8 s at 4.0 px on
    #: the whole frame, 2.2 s and 1.0 s on a 0.4-per-axis window.
    size_fixed_s: float = 0.5
    size_base_s: float = 6.0
    size_per_px_s: float = 0.9
    #: Mechanical backlash, in focuser steps. Measured in the tens on the EAF.
    backlash_steps: int = 40
    #: The rig's sensor. ``full_pixels`` is the denominator every measurement
    #: cost is scaled by, so a centred crop bills its own fraction.
    sensor_shape: tuple[int, int] = (4168, 6224)

    @property
    def full_pixels(self) -> int:
        return self.sensor_shape[0] * self.sensor_shape[1]


# ------------------------------------------------------------------- the model

@dataclass
class Scenario:
    """One sweep to measure: where focus is, how the field behaves, what it
    costs."""

    name: str
    start: int
    focus: int
    step: int
    k: float                       #: steps of defocus per pixel of star size
    n0: int                        #: stars at focus
    hfr0: float                    #: star size at focus, px
    steps_each_side: int = 4
    exposure_s: float = 6.0
    gain: int = 200
    binning: int = 1
    #: Stars never quite reach zero: a floor keeps the model from producing a
    #: count no real detector would report.
    n_floor: int = 1
    cost: Cost = field(default_factory=Cost)

    def hfr(self, position: float) -> float:
        """The V (a hyperbola, which is what the engine fits)."""
        return math.sqrt(self.hfr0 ** 2
                         + ((position - self.focus) / self.k) ** 2)

    def stars(self, position: float) -> float:
        """Detectable stars at ``position``: the field thins as it defocuses,
        reaching a third of its best count at the outer requested point."""
        length = self.steps_each_side * self.step / math.log(3.0)
        return max(float(self.n_floor),
                   self.n0 * math.exp(-abs(position - self.focus) / length))


# ------------------------------------------------------------------ stand-ins

class PositionedArray(np.ndarray):
    """Frame pixels that remember which point they are, through a crop.

    The sweep exposes the next point while this one is measured, so the
    focuser's live position no longer identifies the frame in hand -- and a
    stub that read the live position would be measuring the wrong point. The
    attributes survive slicing (``__array_finalize__``), so when a later change
    hands ``focus_size`` a centred window instead of the whole frame, the stub
    still knows what it is looking at and ``arr.size`` says how much of it it
    got.
    """

    physical_pos: float
    exposure_index: int

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.physical_pos = getattr(obj, "physical_pos", float("nan"))
        self.exposure_index = getattr(obj, "exposure_index", -1)


@dataclass
class Move:
    frm: int
    to: int
    direction: str          #: "in" (position down), "out" (up), "none"


class ClockedFocuser(Focuser):
    """A focuser that costs time to move and has mechanical backlash.

    Two positions, because they are not the same number and the difference is
    the point: ``get_position`` reports the COMMANDED count (that is all a real
    EAF can report), while ``physical_position`` is where the drawtube actually
    is. Approaching from below (an OUT move, position increasing) leaves the
    tube ``backlash_steps`` short; approaching from above (IN) takes the slack
    up and lands on the commanded position. A reversal shorter than the
    backlash turns the motor and does not move the tube at all.
    """

    max_position = 60000

    def __init__(self, start: int, cost: Cost):
        super().__init__("clocked-focuser")
        self.cost = cost
        self.commanded = int(start)
        self._physical = float(start)
        self.moves: list[Move] = []
        self.total_steps = 0
        self.reversals = 0
        self.connected = True

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.commanded

    @property
    def physical_position(self) -> float:
        return self._physical

    async def halt(self) -> None:
        pass

    async def move_to(self, position: int) -> None:
        target = int(position)
        frm = self.commanded
        delta = target - frm
        direction = "out" if delta > 0 else ("in" if delta < 0 else "none")
        if direction != "none":
            last = next((m.direction for m in reversed(self.moves)
                         if m.direction != "none"), None)
            if last is not None and last != direction:
                self.reversals += 1
        self.moves.append(Move(frm, target, direction))
        self.total_steps += abs(delta)
        self.commanded = target
        # The clamp IS the backlash model: an IN move drags the tube down onto
        # the commanded position, an OUT move leaves it a backlash short, and a
        # reversal smaller than the backlash moves neither bound past it.
        lo = target - self.cost.backlash_steps
        self._physical = min(max(self._physical, lo), float(target))
        await asyncio.sleep(self.cost.move_fixed_s
                            + abs(delta) / self.cost.move_rate_steps_per_s)


class ClockedCamera(Camera):
    """A camera that costs ``exposure + download`` and hands back a frame that
    knows where the tube was while the shutter was open."""

    def __init__(self, focuser: ClockedFocuser, cost: Cost):
        super().__init__("clocked-camera")
        self.focuser = focuser
        self.cost = cost
        self.exposures = 0
        #: (start, end) on the virtual clock, one per exposure.
        self.busy: list[tuple[float, float]] = []
        self.connected = True
        self.sensor_height, self.sensor_width = cost.sensor_shape

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def abort_exposure(self) -> None:
        pass

    async def expose(self, seconds: float, gain: int, offset: int,
                     binning: int = 1, light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        loop = asyncio.get_running_loop()
        started = loop.time()
        index = self.exposures
        self.exposures += 1
        # Read the tube position when the shutter OPENS, not when it closes:
        # that is the position the photons were taken at.
        physical = self.focuser.physical_position
        commanded = self.focuser.commanded
        await asyncio.sleep(float(seconds) + self.cost.download_s)
        self.busy.append((started, loop.time()))
        data = np.zeros(self.cost.sensor_shape, dtype=np.uint16).view(
            PositionedArray)
        data.physical_pos = float(physical)
        data.exposure_index = index
        return CameraFrame(
            data=data,
            exposure_s=float(seconds),
            gain=int(gain),
            offset=int(offset),
            binning=int(binning),
            bayer_pattern=None,
            temperature_c=None,
            timestamp=float(started),
            focuser_position=int(commanded),
        )

    def idle_s(self) -> float:
        """Virtual seconds the camera spent doing nothing, between its first
        exposure starting and its last one finishing."""
        if len(self.busy) < 2:
            return 0.0
        span = self.busy[-1][1] - self.busy[0][0]
        return span - sum(end - start for start, end in self.busy)


# -------------------------------------------------------------- the two stubs

@dataclass
class Pass:
    """One measurement call: which frame, and how much of it."""

    exposure_index: int
    fraction: float
    position: float


def measurement_stubs(sc: Scenario, bill: Bill):
    """``(detect_and_measure, focus_size, rust_passes, size_passes, measured)``.

    Both answer from the V model at the frame's PHYSICAL position and bill
    their cost scaled by how much of the frame they were handed, so a change
    that measures a centred window shows up as a cheaper pass rather than as a
    claim.
    """
    rust: list[Pass] = []
    size: list[Pass] = []
    measured: set[int] = set()
    full = float(sc.cost.full_pixels)

    def detect_and_measure(arr, params=None):
        frac = float(arr.size) / full
        pos = float(arr.physical_pos)
        hfr = sc.hfr(pos)
        n = int(round(sc.stars(pos) * frac))
        bill.charge(sc.cost.rust_base_s * frac + sc.cost.rust_per_star_s * n)
        rust.append(Pass(int(arr.exposure_index), frac, pos))
        measured.add(int(arr.exposure_index))
        return [], {"star_count": n, "hfr_median": hfr, "hfr_mad": 0.1 * hfr}

    def focus_size(arr, min_stars: int = 3):
        frac = float(arr.size) / full
        pos = float(arr.physical_pos)
        hfr = sc.hfr(pos)
        # The fine path caps its voters near 25, so a crop of a 34-star field
        # yields one or two -- below MIN_STARS_PER_POINT, which is the fallback
        # the harness has to be able to produce.
        n = int(round(min(25.0, sc.stars(pos)) * frac))
        bill.charge(sc.cost.size_fixed_s
                    + (sc.cost.size_base_s + sc.cost.size_per_px_s * hfr) * frac)
        size.append(Pass(int(arr.exposure_index), frac, pos))
        measured.add(int(arr.exposure_index))
        return hfr, n

    return detect_and_measure, focus_size, rust, size, measured


# ---------------------------------------------------------------- the report

@dataclass
class Report:
    name: str
    success: bool
    message: str
    wall_s: float
    exposures: int
    wasted_frames: int
    rust_passes: int
    rust_mean_frac: float
    size_passes: int
    size_mean_frac: float
    moves: int
    total_steps: int
    reversals: int
    camera_idle_s: float
    cpu_s: float
    final_commanded: int
    final_physical: float
    physical_error_steps: float
    approach_of_final: str
    points: list[tuple[int, float]]
    #: When the probe's measurement finished, on the virtual clock.
    probe_s: float
    #: When each SWEPT point's measurement finished -- the sweep's own
    #: timeline, in the same terms the 2026-09-07 night log reports.
    point_times: list[float]


#: (label, how to render it) -- the table is transposed (metrics down the side,
#: scenarios across) because eighteen columns is not a table anyone reads.
_ROWS: list[tuple[str, callable]] = [
    ("success", lambda r: str(r.success)),
    ("wall_s", lambda r: f"{r.wall_s:.1f}"),
    ("exposures", lambda r: str(r.exposures)),
    ("wasted_frames", lambda r: str(r.wasted_frames)),
    ("rust_passes", lambda r: str(r.rust_passes)),
    ("rust_mean_frac", lambda r: f"{r.rust_mean_frac:.2f}"),
    ("size_passes", lambda r: str(r.size_passes)),
    ("size_mean_frac", lambda r: f"{r.size_mean_frac:.2f}"),
    ("moves", lambda r: str(r.moves)),
    ("total_steps", lambda r: str(r.total_steps)),
    ("reversals", lambda r: str(r.reversals)),
    ("camera_idle_s", lambda r: f"{r.camera_idle_s:.1f}"),
    ("cpu_s", lambda r: f"{r.cpu_s:.1f}"),
    ("final_commanded", lambda r: str(r.final_commanded)),
    ("final_physical", lambda r: f"{r.final_physical:.0f}"),
    ("physical_error_steps", lambda r: f"{r.physical_error_steps:.0f}"),
    ("approach_of_final", lambda r: r.approach_of_final),
]


def format_table(reports: list[Report]) -> str:
    """The whole set as one plain ASCII table, plus each sweep's points."""
    if not reports:
        return "(no scenarios)"
    label_w = max(len(name) for name, _ in _ROWS)
    cols = [max(len(r.name), 14) for r in reports]
    out = ["  ".join(["metric".ljust(label_w)]
                     + [r.name.rjust(w) for r, w in zip(reports, cols)]),
           "  ".join(["-" * label_w] + ["-" * w for w in cols])]
    for name, render in _ROWS:
        out.append("  ".join([name.ljust(label_w)]
                             + [render(r).rjust(w)
                                for r, w in zip(reports, cols)]))
    out.append("")
    for r in reports:
        pts = " ".join(f"{p}:{h:.2f}" for p, h in r.points)
        out.append(f"{r.name}: {pts}")
        if not r.success:
            out.append(f"{r.name}: FAILED -- {r.message}")
    return "\n".join(out)


# ------------------------------------------------------------------- the run

async def _sweep(sc: Scenario) -> Report:
    bill = Bill()
    focuser = ClockedFocuser(sc.start, sc.cost)
    camera = ClockedCamera(focuser, sc.cost)
    detect, size_fn, rust, size, measured = measurement_stubs(sc, bill)

    with installed(
        (asyncio, "to_thread", clocked_to_thread(bill)),
        (_host, "focus_size", size_fn),
        (_host._native, "detect_and_measure", detect),
    ):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        result = await _host.run_native_autofocus(
            camera, focuser,
            exposure_s=sc.exposure_s, gain=sc.gain, step=sc.step,
            steps_each_side=sc.steps_each_side, binning=sc.binning)
        wall = loop.time() - t0

    real_moves = [m for m in focuser.moves if m.direction != "none"]
    approach = real_moves[-1].direction if real_moves else "none"
    # The probe is the first offload of the run; every point ends with the
    # ``native_sweep_metric`` half of its measurement.
    probe_s = bill.timeline[0][2] - t0 if bill.timeline else 0.0
    point_times = [when - t0 for name, _c, when in bill.timeline
                   if name == "native_sweep_metric"]
    return Report(
        name=sc.name,
        success=bool(result.success),
        message=result.message,
        wall_s=wall,
        exposures=camera.exposures,
        wasted_frames=camera.exposures - len(measured),
        rust_passes=len(rust),
        rust_mean_frac=(sum(p.fraction for p in rust) / len(rust)) if rust else 0.0,
        size_passes=len(size),
        size_mean_frac=(sum(p.fraction for p in size) / len(size)) if size else 0.0,
        moves=len(focuser.moves),
        total_steps=focuser.total_steps,
        reversals=focuser.reversals,
        camera_idle_s=camera.idle_s(),
        cpu_s=bill.total,
        final_commanded=focuser.commanded,
        final_physical=focuser.physical_position,
        physical_error_steps=focuser.physical_position - sc.focus,
        approach_of_final=approach,
        points=[(int(p), float(h)) for p, h in result.points],
        probe_s=probe_s,
        point_times=point_times,
    )


def run_scenario(sc: Scenario) -> Report:
    """Run one scenario end to end on a virtual clock. SYNC on purpose: it owns
    its event loop, so it cannot be awaited from inside another one."""
    report, _elapsed = run_on_virtual_clock(lambda: _sweep(sc))
    return report


# ------------------------------------------------------------------ scenarios

def rich_at_focus() -> Scenario:
    """The good case: a rich field, already in focus, swept at the shipped
    step. Nothing is wasted and nothing turns round, so whatever this costs is
    the floor."""
    return Scenario(name="rich_at_focus", start=11200, focus=11200, step=350,
                    k=47.0, n0=1200, hfr0=2.5)


def rich_turnround() -> Scenario:
    """Focus two steps ABOVE the start, so the initial pass brackets it, the
    engine extends down twice and then turns and extends up twice -- the case
    where the descending predictor misses once and gives up for the rest of
    the run."""
    return Scenario(name="rich_turnround", start=11200, focus=11200 + 2 * 350,
                    step=350, k=47.0, n0=1200, hfr0=2.5)


def sparse_83() -> Scenario:
    """The calibration: the logged sweep of 2026-09-07 21:39:38, a 34-star
    field on an 83-step sweep. On the sky its fit was rejected; here, with a
    clean hyperbola, it succeeds -- the wall time to the validation frame is
    the target, not the outcome."""
    return Scenario(name="sparse_83", start=11176, focus=11176, step=83,
                    k=12.0, n0=34, hfr0=3.5)


def rich_83() -> Scenario:
    """The same narrow sweep on a rich field: the star count is not what makes
    it slow."""
    return Scenario(name="rich_83", start=11200, focus=11200, step=83,
                    k=12.0, n0=1200, hfr0=3.5)


SCENARIOS = [rich_at_focus, rich_turnround, sparse_83, rich_83]


def run_all() -> list[Report]:
    return [run_scenario(make()) for make in SCENARIOS]


if __name__ == "__main__":
    print(format_table(run_all()))
