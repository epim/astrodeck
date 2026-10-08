# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Fold the subs a run ALREADY captured into the session stack.

Switching the session stack on used to mean "stack the next frame and every one
after it", so a stack armed at 2am showed two of the night's ninety subs and the
composite was noise. The point of the composite is the question "is this going
to be a picture", and the answer is worth much more four hours in than four
minutes in. This module is the other half: given the run's own ledger, it walks
back over the accepted subs, reads each one off disk and hands it to the same
``SessionStacker.add`` the live path uses.

Nothing here decides anything the live path does not. Five things do have to be
decided, and they are decided here because this is the only place that can see
them:

**Which frames.** The session ledger (``sequence/session.py``), not the
directory. A quality-rejected sub is still WRITTEN, under the same
``Light_<target>_<FILTER>_<date>_<time>_<n>.fits`` name as an accepted one, with
nothing in the name or the header to tell them apart (``cfg.escalation.
hfr_reject_action`` defaults to ``warn``, which keeps the file). The one record
of the verdict is ``SessionFrame.auto_accepted``, plus the operator's own
``override`` on top of it -- ``SessionFrame.effective()`` is the whole rule. So
a directory scan is not a shortcut here, it is a different and wrong answer, and
it would put the trailed frames the gate refused into the only picture anyone is
looking at. :func:`plan_backfill` reads the ledger; the filenames are parsed
(``nightstack.parse_frame_name``) only to recover a filter when the header and
the plan have both gone quiet.

**Which run.** The frames of THIS run only, matched on the report id the
stacker already uses as half its identity. A multi-night session's earlier
nights were shot after a different centring and, on a rig whose mount has no
brake, sometimes a different rotation; ``sessionstack``'s own docstring says
last night's stack is not this night's, and the backfill does not get to
disagree with the live path about that.

**Which targets.** All of the run's, each into its own panel. The stacker used
to hold one target and reset when it changed, so backfilling two would keep the
second's frames and throw the first's away after reading both; panels are
slots now (#172), so a mosaic night's every panel is caught up and the frames
are handed over in capture order, which interleaves them. A caller that wants
one passes ``target=``. Each item carries the target's ID as well as its name,
because that is what the live path keys the panel by: a backfill that named a
panel differently would build a second picture of it.

**Where the metadata comes from.** The FITS header first (``FILTER`` as typed,
``EXPTIME``, ``BAYERPAT``, ``XBINNING``) because it describes the file actually
being stacked; the frozen plan step second; the filename last. A step edited
after the frame was shot is not evidence about the frame.

**What a failure means.** One unreadable sub is a counted failure and the pass
carries on. A missing file is normal -- the operator can have moved or deleted
subs since -- and is not worth ending a backfill over.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fitsio import full_name
from .nightstack import parse_frame_name
from .sessionstack import BackfillProgress, SessionStacker, effective_bayer, frame_key

__all__ = ["BackfillItem", "plan_backfill", "read_backfill_frame",
           "run_backfill"]


@dataclass(frozen=True)
class BackfillItem:
    """One already-captured sub, located and labelled but not yet read.

    Deliberately holds no pixels: a plan over a few hundred 26-megapixel subs
    must cost a few kilobytes, and the pixels are read one at a time inside the
    worker so peak memory is one frame regardless of how long the night was.
    """
    path: Path
    #: The filter as the PLAN believed it; the header wins when it has one.
    filter_name: str = ""
    exposure_s: float = 0.0
    target: str = ""
    session: str = ""
    #: The target's id in the plan, which is the panel key the live path uses.
    #: Last, and defaulted, so an item built positionally or by a caller that
    #: does not know panels still works (it then keys by name).
    target_id: str = ""

    @property
    def key(self) -> str:
        return frame_key(self.path)


def _steps_by_id(plan) -> dict:
    out: dict = {}
    for t in getattr(plan, "targets", []) or []:
        for s in getattr(t, "steps", []) or []:
            out[getattr(s, "id", "")] = s
    return out


def _targets_by_id(plan) -> dict[str, str]:
    return {getattr(t, "id", ""): (getattr(t, "name", "") or "")
            for t in (getattr(plan, "targets", []) or [])}


def plan_backfill(session, *, run: str = "", target: str = "",
                  stacker: SessionStacker | None = None) -> list[BackfillItem]:
    """The subs of ``session`` that belong in the stack and are not in it yet.

    ``run`` restricts to one report id (empty = whatever the session's most
    recent frame belongs to). ``target`` restricts to one target, by NAME or by
    id (empty = EVERY target of the run, each of which is a panel in the
    stack). ``stacker``, when given, drops the frames it has already consumed,
    in whichever panel -- a pre-filter for the disk read, not the decision,
    which ``add`` re-makes under its own lock.

    Ordered oldest first, which is capture order. That matters for more than
    tidiness: the first frame of a channel seeds that channel's registration
    reference, so stacking in capture order is what makes a backfilled composite
    the same picture the live path would have built. Across panels it means the
    items interleave as the visits did.
    """
    if stacker is not None:
        # A stack that already holds pixels has ALREADY chosen its run; the
        # backfill joins that picture rather than proposing another one, or the
        # first frame it stacks would drop away the live frames it was meant to
        # be catching up with. Only the run: every target is wanted.
        run = run or stacker.session
    frames = list(getattr(session, "frames", []) or [])
    if not frames:
        return []
    plan = getattr(session, "plan", None)
    steps = _steps_by_id(plan)
    names = _targets_by_id(plan)

    usable = [f for f in frames
              if f.effective() and str(getattr(f, "path", "") or "")]
    if not usable:
        return []
    if not run:
        run = str(getattr(usable[-1], "night", "") or "")
    usable = [f for f in usable if str(getattr(f, "night", "") or "") == run]
    if not usable:
        return []

    out: list[BackfillItem] = []
    for f in usable:
        tid = str(getattr(f, "target_id", "") or "")
        name = names.get(tid, "")
        if target and target not in (name, tid):
            continue
        path = Path(str(f.path))
        if stacker is not None and stacker.has_frame(str(path)):
            continue
        step = steps.get(getattr(f, "step_id", ""))
        out.append(BackfillItem(
            path=path,
            filter_name=str(getattr(step, "filter", "") or "") if step else "",
            exposure_s=float(getattr(step, "exposure_s", 0.0) or 0.0)
            if step else 0.0,
            target=name, session=run, target_id=tid))
    return out


def read_backfill_frame(item: BackfillItem
                        ) -> tuple[np.ndarray, str, float, str | None]:
    """``(pixels, filter, exposure_s, bayer)`` for one sub. Raises on a bad read.

    Pixels come back as uint16, which is what the camera produced and what
    ``block_mean`` and ``LiveStacker`` expect. astropy applies BZERO/BSCALE, so
    the ``int16 + 32768`` encoding ``save_fits`` writes is already the unsigned
    values by the time we see it; the cast is a no-op on that path and a clamp
    on anything else (a float or a calibrated frame from another tool).

    The header wins over the plan for every field, INCLUDING the filter -- a
    plan step edited mid-run says nothing about a frame shot an hour earlier.
    The filter is the slot's name as typed (``fitsio.full_name``, #332), which
    is also the name the live frame loop hands the stacker for the same frame:
    the FILTER card is folded to ASCII, and slots named H-alpha and H-beta in
    Greek both read 'H?' there, one filter for two.
    Binning is read here and folded into the Bayer answer through
    ``effective_bayer``, because a 2x2-binned OSC frame has no mosaic left in it
    however confidently ``BAYERPAT`` is still written.
    """
    from astropy.io import fits

    with fits.open(item.path, memmap=False) as hdul:
        hdu = hdul[0]
        raw = np.asarray(hdu.data)
        hdr = hdu.header
        filt = full_name(hdr, "FILTER").strip()
        exposure = hdr.get("EXPTIME", None)
        bayer = str(hdr.get("BAYERPAT", "") or "").strip()
        binning = hdr.get("XBINNING", 1)

    if raw.ndim != 2:
        raise ValueError(f"{item.path.name}: not a 2-D frame {raw.shape}")
    if raw.dtype != np.uint16:
        raw = np.clip(np.nan_to_num(raw.astype(np.float32), nan=0.0),
                      0, 65535).astype(np.uint16)

    if not filt:
        filt = item.filter_name
    if not filt:
        ref = parse_frame_name(item.path)
        filt = ref.filter_name if ref is not None else ""

    try:
        exp = float(exposure) if exposure is not None else 0.0
    except (TypeError, ValueError):
        exp = 0.0
    if exp <= 0:
        exp = float(item.exposure_s or 0.0)

    try:
        bin_x = int(binning or 1)
    except (TypeError, ValueError):
        bin_x = 1
    return raw, filt, exp, effective_bayer(bayer, bin_x)


def run_backfill(stacker: SessionStacker, items: Iterable[BackfillItem], *,
                 on_error: Callable[[str], None] | None = None
                 ) -> BackfillProgress:
    """Read and stack ``items``, reporting progress through ``stacker``.

    Meant to be run on a worker thread. The live frame loop keeps calling
    ``stacker.add`` throughout and both land, because:

      * every mutation is inside ``SessionStacker``'s own lock, and the "have I
        seen this frame" test is inside the SAME critical section as the
        accumulation, so if a sub is offered twice exactly one of the two calls
        stacks it whichever order they arrive in;
      * the stack is a running MEAN, so the order frames arrive in does not
        change the result. Order changes only which frame seeds a channel's
        registration reference, and a reference is a reference: a composite
        seeded by a live frame differs from one seeded by the oldest backfilled
        frame by a whole-pixel shift of the whole picture, not by its contents;
      * the disk read and the FITS decode are OUTSIDE the lock (that is why this
        function, and not ``add``, does them), so a backfill can never hold the
        event loop for the length of an I/O.

    It gives up when the stack is switched off, when the operator resets it (the
    generation moves), or when the live path has moved the stack onto a
    different RUN -- in each case the pixels this was filling are gone or are no
    longer the pixels asked for. Not when the live path moves to another
    TARGET: panels are slots, the foreground changes at every live add of a
    mosaic, and a guard that read that as "the stack moved" would end every
    backfill at the first hop (#172).

    No pacing: the rate limit that matters is the disk, and between exposures a
    run is not competing for the CPU anyway. If it ever needs slowing down, the
    honest place is a sleep here with a measurement behind it, not a knob with
    nothing setting it.
    """
    items = list(items)
    stacker.backfill_begin(len(items))
    if not items:
        return stacker.backfill

    gen = stacker.generation
    wanted = items[0].session
    error = ""
    try:
        for item in items:
            if not stacker.enabled or stacker.generation != gen:
                error = "stopped"
                break
            if stacker.session and stacker.session != wanted:
                error = "the stack moved to another run"
                break
            if stacker.has_frame(str(item.path)):
                stacker.backfill_step(skipped=True)
                continue
            try:
                data, filt, exposure, bayer = read_backfill_frame(item)
            except Exception as exc:
                if on_error is not None:
                    on_error(f"{item.path.name}: {exc}")
                stacker.backfill_step(failed=True)
                continue
            landed = stacker.add(data, filt, exposure, target=item.target,
                                 session=item.session, bayer_pattern=bayer,
                                 key=str(item.path),
                                 target_id=item.target_id or None)
            del data
            if landed:
                stacker.backfill_step(added=landed)
            elif stacker.has_frame(str(item.path)):
                # The live path took it while this frame was being read.
                stacker.backfill_step(skipped=True)
            else:
                stacker.backfill_step(failed=True)
    except Exception as exc:                       # pragma: no cover - guard
        error = f"{type(exc).__name__}: {exc}"
        if on_error is not None:
            on_error(error)
    return stacker.backfill_finish(error)
