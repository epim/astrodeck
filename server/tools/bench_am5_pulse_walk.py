# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Bench walk: does the ZWO AM5's reported position count emulated guide
pulses? (#851, HARDWARE-PENDING H1)

On this driver an east guide pulse is a tracking suspend (``:Td#`` ...
``:Te#``), west is ``:R2#`` + ``:Mw#`` and north/south are ``:R1#`` moves,
each capped at 1000 ms. On 2026-10-07 the field walked 2.8 degrees under an
hour of east pulses while the mount's report stayed on the target. This script
sends a known number of pulses per direction through the driver's own
``pulse_guide`` (the guider's code path) and reads ``:GR#`` / ``:GD#`` before
and after, so the answer is a measured ratio, not a guess.

It prints DELTAS ONLY: never a position, never alt/az, never a wall-clock
time. A position read at a known moment locates the site (#140, #166).

PRECONDITIONS (the script refuses to run without ``--i-checked``):
  1. The tube's position is KNOWN: Trust position at a real home, or a solved
     and synced pointing in AstroDeck; then a goto away from the pole to a
     field near Dec +35 and within 3 h of the meridian; then Solve & Sync
     there.
  2. Guider stopped and tracking on in AstroDeck, then disconnect the mount
     in AstroDeck so this script can open the port.

RUN on astrotown, from C:\\Users\\James\\AstroDeck, with the venv's python:
  .\\venv\\Scripts\\python.exe server\\tools\\bench_am5_pulse_walk.py --port COM5 --i-checked
(the port is the mount's; if the deploy does not ship server/tools, copy this
one file across: it imports only ``astrodeck``).

EXPECTED READINGS (defaults: 40 pulses x 500 ms, 1.5 s apart, so 20 s of
motion per block; the report's RA resolution is 1 s of time = 15"):
  east   dRA  +300.8" if the report counts the suspend, ~0" if it does not
  west   dRA  -300.8"  (or ~0")
  north  |dDec| 150.4" (0.5x sidereal; the sign follows the pier side)
  south  |dDec| 150.4"
  --long-east  one raw 10 s suspend: dRA +150.4" (re-measures 2026-07-20)
  --lunar      300 s at the lunar rate: dRA +300 x (15.041 - 14.685) = +106.8"
The verdict is "counted" for a ratio in 0.75..1.25, "ignored" under 0.25 in
size (75" on a 300.8" block, five steps of the RA resolution), else
"partial". East then west, and north then south, cancel physically.

THE ONE PLACE THIS BYPASSES THE 1000 ms CAP is ``--long-east``, so it carries
its own stop: the 10 s wait is an asyncio sleep inside ``try``/``finally``,
and the ``finally`` always resumes tracking and reads it back, on any
exception including KeyboardInterrupt and cancellation; ``main`` resumes it
once more if an interrupt arrives while a suspend is open. ``--lunar`` has
the same shape around its 300 s.

AFTERWARDS: reconnect the mount in AstroDeck, then Solve & Sync in place
before any goto.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

# Run as a script, the astrodeck beside it comes first on the path (#915): the
# venv's editable install points at one checkout, which in a worktree is not
# this one. The directory is tested for, because the header says this one file
# may be copied to the rig alone, where nothing sits beside it and nothing is
# added.
_SERVER = Path(__file__).resolve().parents[1]
if (_SERVER / "astrodeck").is_dir() and str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

#: RA arcseconds per second of time at the sidereal rate.
SIDEREAL_ARCSEC_PER_S = 15.041
#: The lunar drive rate in the same unit (the Moon's mean motion taken off).
LUNAR_ARCSEC_PER_S = 14.685
#: The north/south guide rate the driver selects (``:R1#``): 0.5x sidereal.
NS_RATE_ARCSEC_PER_S = SIDEREAL_ARCSEC_PER_S * 0.5
#: The driver's own per-pulse cap; a longer request is refused here.
PULSE_CAP_MS = 1000
#: Seconds of commanded motion one block may add up to (count x pulse).
#: 60 s at the sidereal rate is 60 x 15.041" = 902" = 15.0', a quarter of a
#: degree, comfortably inside any field the bench place was solved in; the
#: defaults (40 x 500 ms) are 20 s. Without it ``--count`` was unbounded, and
#: 10000 x 1000 ms would drive the west block alone 10000 x 15.041" = 41.8
#: degrees at the scope.
MAX_BLOCK_MOTION_S = 60.0
#: Arcseconds of RA per hour of RA.
ARCSEC_PER_HOUR = 54000.0
#: The declination band the bench place must be in (away from the pole).
DEC_MIN, DEC_MAX = 20.0, 70.0

FINAL_ADVICE = ("reconnect the mount in AstroDeck, then Solve & Sync in place "
                "before any goto")


def ra_delta_arcsec(ra0_h: float, ra1_h: float) -> float:
    """``ra1 - ra0`` in arcseconds of RA, wrapped to (-12, 12] hours."""
    d = (ra1_h - ra0_h) % 24.0
    if d > 12.0:
        d -= 24.0
    return d * ARCSEC_PER_HOUR


def verdict(ratio: float) -> str:
    """"counted" (0.75..1.25), "ignored" (under 0.25 in size), "partial"."""
    if 0.75 <= ratio <= 1.25:
        return "counted"
    if abs(ratio) < 0.25:
        return "ignored"
    return "partial"


def _expected(direction: str, seconds: float) -> float:
    d = direction.lower()[0]
    if d == "e":
        return seconds * SIDEREAL_ARCSEC_PER_S
    if d == "w":
        return -seconds * SIDEREAL_ARCSEC_PER_S
    return seconds * NS_RATE_ARCSEC_PER_S          # a size; sign by pier side


def _ratio(direction: str, d_ra: float, d_dec: float, expected: float) -> float:
    if expected == 0:
        return 0.0
    if direction.lower()[0] in "ew":
        return d_ra / expected
    return abs(d_dec) / expected


async def run_walk(tel, *, direction: str, pulse_ms: int, count: int,
                   gap_s: float,
                   sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
                   ) -> dict:
    """``count`` pulses of ``pulse_ms`` toward ``direction`` through
    ``tel.pulse_guide``, ``gap_s`` apart, and the report's motion across
    them. Deltas only."""
    if pulse_ms > PULSE_CAP_MS:
        raise ValueError(f"pulse longer than the {PULSE_CAP_MS} ms cap")
    if count * pulse_ms / 1000.0 > MAX_BLOCK_MOTION_S:
        raise ValueError(f"a block over {MAX_BLOCK_MOTION_S:.0f} s of motion")
    ra0, dec0 = await tel.get_position()
    for i in range(count):
        await tel.pulse_guide(direction, pulse_ms)
        if i + 1 < count:
            await sleep(gap_s)
    ra1, dec1 = await tel.get_position()
    d_ra = ra_delta_arcsec(ra0, ra1)
    d_dec = (dec1 - dec0) * 3600.0
    expected = _expected(direction, count * pulse_ms / 1000.0)
    ratio = _ratio(direction, d_ra, d_dec, expected)
    return {"direction": direction, "pulses": count, "pulse_ms": pulse_ms,
            "d_ra_arcsec": d_ra, "d_dec_arcsec": d_dec,
            "expected_arcsec": expected, "ratio": ratio,
            "verdict": verdict(ratio)}


async def long_east(tel, *, hold_s: float = 10.0,
                    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                    state: SimpleNamespace | None = None) -> dict:
    """One raw tracking suspend of ``hold_s`` (``:Td#``, wait, ``:Te#``), the
    2026-07-20 measurement repeated. The resume is in a ``finally``: it runs
    on ANY exception, KeyboardInterrupt and cancellation included, and reads
    tracking back. ``state.suspended`` says whether a suspend is open, for
    `main`'s interrupt handler."""
    state = state if state is not None else SimpleNamespace(suspended=False)
    ra0, dec0 = await tel.get_position()
    state.suspended = True
    await tel.set_tracking(False)                       # :Td#
    try:
        await sleep(hold_s)
    finally:
        await tel.set_tracking(True)                    # :Te#
        state.suspended = not bool(await tel.get_tracking())
    ra1, dec1 = await tel.get_position()
    d_ra = ra_delta_arcsec(ra0, ra1)
    expected = hold_s * SIDEREAL_ARCSEC_PER_S
    ratio = d_ra / expected
    return {"direction": "east, one suspend", "pulses": 1,
            "pulse_ms": int(hold_s * 1000), "d_ra_arcsec": d_ra,
            "d_dec_arcsec": (dec1 - dec0) * 3600.0,
            "expected_arcsec": expected, "ratio": ratio,
            "verdict": verdict(ratio)}


async def lunar(tel, *, hold_s: float = 300.0,
                sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                state: SimpleNamespace | None = None) -> dict:
    """``hold_s`` at the lunar rate (``:TL#``), then back to sidereal
    (``:TQ#``) in a ``finally``, as `long_east` does."""
    state = state if state is not None else SimpleNamespace(lunar=False)
    ra0, dec0 = await tel.get_position()
    state.lunar = True
    await tel.set_tracking_rate("lunar")
    try:
        await sleep(hold_s)
    finally:
        await tel.set_tracking_rate("sidereal")
        state.lunar = False
    ra1, dec1 = await tel.get_position()
    d_ra = ra_delta_arcsec(ra0, ra1)
    expected = hold_s * (SIDEREAL_ARCSEC_PER_S - LUNAR_ARCSEC_PER_S)
    ratio = d_ra / expected
    return {"direction": "lunar rate", "pulses": 1,
            "pulse_ms": int(hold_s * 1000), "d_ra_arcsec": d_ra,
            "d_dec_arcsec": (dec1 - dec0) * 3600.0,
            "expected_arcsec": expected, "ratio": ratio,
            "verdict": verdict(ratio)}


def _line(r: dict) -> str:
    return (f"{r['direction']}: {r['pulses']} x {r['pulse_ms']} ms, "
            f"dRA {r['d_ra_arcsec']:+.1f}\", dDec {r['d_dec_arcsec']:+.1f}\", "
            f"expected {r['expected_arcsec']:+.1f}\", ratio {r['ratio']:.2f}, "
            f"{r['verdict']}")


async def _open_am5(port: str):
    from astrodeck.devices.backends.zwo_am5 import ZwoAm5Session
    return await ZwoAm5Session(port).get_device(
        "telescope", SimpleNamespace(extra={}))


async def _bench(args, tel, state: SimpleNamespace,
                 out: Callable[[str], None],
                 sleep: Callable[[float], Awaitable[Any]]) -> int:
    _ra, dec = await tel.get_position()
    if not (DEC_MIN <= dec <= DEC_MAX) or not await tel.get_tracking():
        out("refused: too near the pole or not tracking; see the "
            "preconditions")
        return 2
    t0 = asyncio.get_running_loop().time()
    for d in [x.strip() for x in args.dirs.split(",") if x.strip()]:
        r = await run_walk(tel, direction=d, pulse_ms=args.pulse_ms,
                           count=args.count, gap_s=args.gap_s, sleep=sleep)
        out(_line(r))
    if args.long_east:
        out(_line(await long_east(tel, state=state, sleep=sleep)))
    if args.lunar:
        out(_line(await lunar(tel, state=state, sleep=sleep)))
    out(f"done after {asyncio.get_running_loop().time() - t0:.0f} s; "
        f"{FINAL_ADVICE}")
    return 0


def main(argv: list[str] | None = None, *,
         open_mount: Callable[[str], Awaitable[Any]] | None = None,
         out: Callable[[str], None] = print,
         sleep: Callable[[float], Awaitable[Any]] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="bench_am5_pulse_walk",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True, help="the mount's serial port")
    p.add_argument("--pulse-ms", type=int, default=500,
                   help=f"pulse length, at most {PULSE_CAP_MS}")
    p.add_argument("--count", type=int, default=40)
    p.add_argument("--gap-s", type=float, default=1.5)
    p.add_argument("--dirs", default="east,west,north,south")
    p.add_argument("--long-east", action="store_true",
                   help="also one raw 10 s tracking suspend")
    p.add_argument("--lunar", action="store_true",
                   help="also 300 s at the lunar rate")
    p.add_argument("--i-checked", action="store_true",
                   help="the preconditions in the help text are met")
    args = p.parse_args(argv)
    if args.pulse_ms > PULSE_CAP_MS or args.pulse_ms <= 0:
        out(f"refused: a pulse must be 1 to {PULSE_CAP_MS} ms, the driver's "
            f"own cap")
        return 2
    block_s = args.count * args.pulse_ms / 1000.0
    if args.count <= 0 or block_s > MAX_BLOCK_MOTION_S:
        out(f"refused: a block of {args.count} pulses of {args.pulse_ms} ms "
            f"is {block_s:.0f} s of motion; the limit is "
            f"{MAX_BLOCK_MOTION_S:.0f} s a block (15' at the sidereal rate)")
        return 2
    if not args.i_checked:
        out("refused: read the preconditions (--help), then pass --i-checked")
        return 2
    opener = open_mount or _open_am5
    state = SimpleNamespace(suspended=False, lunar=False)
    tel_box: dict = {}

    async def run() -> int:
        tel = await opener(args.port)
        tel_box["tel"] = tel
        try:
            return await _bench(args, tel, state, out,
                                sleep or asyncio.sleep)
        finally:
            disconnect = getattr(tel, "disconnect", None)
            if callable(disconnect):
                try:
                    await disconnect()
                except Exception:          # noqa: BLE001 - best effort
                    pass

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        tel = tel_box.get("tel")
        if tel is not None and (state.suspended or state.lunar):
            async def resume() -> bool:
                if state.lunar:
                    await tel.set_tracking_rate("sidereal")
                await tel.set_tracking(True)
                return bool(await tel.get_tracking())
            try:
                ok = asyncio.run(resume())
            except Exception:              # noqa: BLE001 - said, not hidden
                ok = False
            out("tracking resumed" if ok
                else "tracking state unknown: check the mount")
        out(f"interrupted; {FINAL_ADVICE}")
        return 130


if __name__ == "__main__":
    sys.exit(main())
