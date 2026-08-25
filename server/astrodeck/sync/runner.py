"""The thing that actually calls :func:`~astrodeck.sync.push.push_once`.

Phase 2 built the algorithm and left this seam empty on purpose — a push that
runs but has no honest surface is the "built, tested, never reached by the real
path" shape this project has been bitten by before. This module is that surface's
engine: one asyncio task, started with the app, that reads the config every tick
and does nothing at all until an operator names a destination.

WHY A TICK LOOP AND NOT A CALLBACK ON ``saved``. Both, actually. The capture path
pokes :meth:`PushRunner.note_saved` — cheap, non-blocking, no I/O — and the loop
turns those pokes into passes through :func:`~astrodeck.sync.push.due`, which
debounces a burst of frames into one destination hash and ALSO sweeps
unconditionally every 15 minutes. The sweep is what makes a dropped poke, a
restart mid-pass or an hour of NAS downtime heal on their own. Nothing here
remembers which files were sent; every pass re-derives that from a fresh diff,
which is the property that lets a pass die anywhere without leaving a hole.

WHAT IT WILL NOT DO. Two passes at once (a manual "Push now" during a sweep would
hash the same tree twice and race on the same ``.part`` names), and it will not
push at all with the block disabled or the path empty. Both are checked on the
loop's side rather than trusted from the caller.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

# Imported as a MODULE, never `from ..config import config_store`: that would
# bind whichever store existed at import time, and every consumer here has to
# read the LIVE one (the same rule the factory-reset routes follow for
# CONFIG_DIR, and the reason a test's store was invisible to this runner).
from .. import config as _config
from ..events import bus
from . import manifest as _manifest
from . import push as _push
from .destination import Destination, LocalDirDestination

#: How often the loop looks. Much finer than either cadence it enforces — the
#: 20 s debounce and the 900 s sweep both live in ``push.due`` — because a tick
#: that only reads config and compares two floats costs nothing, and a coarse
#: tick would round the debounce up to itself.
TICK_INTERVAL_S = 5.0

#: A pass is bounded so a first run against a 9 GB backlog cannot hold the disk
#: for twenty minutes with a guider running. Config can lower it; this is the
#: ceiling applied when config says "unbounded", because unbounded is a fine
#: intent and a poor first night.
DEFAULT_PASS_LIMIT = 200


def build_destination(cfg) -> Destination | None:
    """The configured destination, or None when there isn't one.

    Returns None — never raises and never invents a default — for the disabled
    case, the empty-path case and an unknown ``kind``. A runner that guessed a
    destination would be a runner that could copy a night somewhere nobody asked
    for it to go.
    """
    sp = getattr(cfg, "sync_push", None)
    if sp is None or not sp.enabled:
        return None
    path = (sp.path or "").strip()
    if not path:
        return None
    if sp.kind != "local_dir":
        return None
    return LocalDirDestination(path, label=(sp.label or "").strip() or path)


class PushRunner:
    """The lifespan task. One instance, created in ``api.app`` beside the other
    services (dawn_park / sun_watch / weather) and started unconditionally: a
    tick with the feature off is one attribute read, and it is armed the moment
    someone turns it on, with no restart."""

    def __init__(self, *, clock=None, interval_s: float = TICK_INTERVAL_S) -> None:
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # never reached it -- and production builds this WITHOUT a clock
        # (api/app.py:147-185). A simulated night would tick this hundreds of
        # times at one frozen instant with every assertion green.
        self._clock = clock or (lambda: time.time())
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self.state = _push.PushState()
        # A frame landed and no pass has run since. Not a count and not a list of
        # paths: the diff decides what to send, so all this has to carry is
        # "something changed", and the debounce needs to know when.
        self._pending_event = False
        self._last_event_at = 0.0
        # Serialises the sweep against a manual push. asyncio.Lock and not a
        # bool: "Push now" awaits its turn and reports a real result, rather
        # than being told to try again.
        self._lock = asyncio.Lock()
        # The label we last announced, so a destination change is logged once
        # instead of every pass.
        self._announced: str | None = None
        # Latch for the loud consecutive-failure line (see FAIL_LOUD_AFTER).
        self._alarmed = False

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ----------------------------------------------------------- the capture seam

    def note_saved(self) -> None:
        """A frame just landed on this box. Never blocks, never raises.

        Called from inside the capture path, so it does exactly two assignments.
        Whether a pass is warranted, whether the feature is even on, and what to
        send are all decided later, on the loop's own time.
        """
        try:
            self._pending_event = True
            self._last_event_at = self._clock()
        except Exception:  # noqa: BLE001 - a capture must never fail for this
            pass

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        """What the settings panel shows. Config first (so the panel can say
        "off" without guessing), then the measured state of the last passes."""
        cfg = _config.config_store.cfg()
        sp = getattr(cfg, "sync_push", None)
        dest = build_destination(cfg)
        out = {
            "enabled": bool(sp.enabled) if sp else False,
            "kind": sp.kind if sp else "local_dir",
            "path": sp.path if sp else "",
            "label": (dest.label if dest else ""),
            "configured": dest is not None,
            "running": self._lock.locked(),
            "debounce_s": _push.DEBOUNCE_S,
            "sweep_interval_s": _push.SWEEP_INTERVAL_S,
        }
        out.update(self.state.payload())
        return out

    # -------------------------------------------------------------------- work

    async def push_now(self) -> dict:
        """Run one pass immediately, whatever the cadence says.

        This is the button that makes the panel honest: an operator who has just
        typed a UNC path finds out NOW whether the rig can write to it, instead
        of at 02:00 when nobody is watching. It still refuses when the feature is
        off — "push now" is not a way around the enable.
        """
        cfg = _config.config_store.cfg()
        dest = build_destination(cfg)
        if dest is None:
            return {"ok": False, "error": "no destination configured",
                    **self.status()}
        await self._pass(dest, cfg, forced=True)
        return self.status()

    async def _run(self) -> None:
        while True:
            # Sleep first: a tick at t=0 lands mid-boot, where the gallery scan
            # competes with connecting devices for a disk that is already busy.
            await asyncio.sleep(self._interval_s)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the loop outlives any pass
                bus.log("warning", f"sync push tick failed: {e}", "sync")

    async def _tick(self) -> None:
        cfg = _config.config_store.cfg()
        dest = build_destination(cfg)
        if dest is None:
            # Off, or pointed nowhere. Drop any pending poke on the floor: when
            # the feature is turned on, the sweep finds everything anyway — that
            # is what makes the sweep unconditional.
            self._pending_event = False
            self._announced = None
            return
        if self._announced != dest.label:
            bus.log("info", f"file sync: pushing frames to {dest.label}", "sync")
            self._announced = dest.label
        if not _push.due(self.state, pending_event=self._pending_event,
                         now=self._clock(), last_event_at=self._last_event_at):
            return
        await self._pass(dest, cfg)

    async def _pass(self, dest: Destination, cfg, *, forced: bool = False) -> None:
        """One reconciliation pass, off the event loop, with the poke cleared
        BEFORE the work rather than after.

        Clearing first is deliberate: a frame that lands during a pass must leave
        the flag set, so the next tick reconsiders. Clearing afterwards would
        swallow exactly that frame until the 15-minute sweep, which is the one
        case the debounce exists to serve.
        """
        async with self._lock:
            self._pending_event = False
            limit = int(getattr(cfg.sync_push, "limit_per_pass", 0) or 0)
            result = await asyncio.to_thread(self._pass_blocking, dest, limit)
            self.state.record(result, now=self._clock())
            self._announce(result, forced=forced)

    def _pass_blocking(self, dest: Destination, limit: int) -> "_push.PushResult":
        """The disk-bound half: scan the gallery, hash, copy. On a worker thread
        so a guiding loop and an exposure in flight never wait for it."""
        from .. import gallery as _gallery
        try:
            rows, truncated = _gallery.scan()
            facts = _manifest.rig_facts(rows)
        except Exception as e:  # noqa: BLE001 - report it, don't unwind the loop
            return _push.PushResult(error=f"{type(e).__name__}: {e}")
        if truncated:
            # SAY WHAT WAS DROPPED. The scan stops at gallery.SCAN_MAX_FILES, and
            # a pass that quietly offers a subset of the library reads exactly
            # like a pass that offered all of it — right up until the frames that
            # were never in the list turn out never to have been copied.
            bus.log("warning",
                    f"file sync is only seeing the first {len(rows)} files: the "
                    f"library scan hit its cap, so frames beyond it are NOT being "
                    f"pushed", "sync")
        return _push.push_once(
            Path(_gallery.capture_root()), dest,
            facts=facts, cache=_manifest.SHARED_HASH_CACHE,
            limit=limit if limit > 0 else DEFAULT_PASS_LIMIT)

    def _announce(self, result: "_push.PushResult", *, forced: bool) -> None:
        """Say what happened — and say it at the right volume.

        A quiet pass ("nothing to send") is the common case all night and is
        logged at debug, because 96 lines of "nothing to send" is how a log stops
        being read. A pass that moved something says so. A pass that failed says
        so once; the LOUD line waits for FAIL_LOUD_AFTER consecutive failures,
        which is what tells a sleeping NAS apart from a broken destination.

        The loud line goes out at ``error`` level, which is already the channel
        the alert dispatcher forwards to a configured sink — deliberately NOT a
        new notification path of its own (see #146, which owns that decision).
        """
        if result.sent or result.failed or forced:
            bus.log("warning" if result.failed else "info", result.summary(), "sync")
        elif result.error:
            bus.log("warning", result.summary(), "sync")
        else:
            bus.log("debug", result.summary(), "sync")

        if self.state.should_alarm and not self._alarmed:
            self._alarmed = True
            bus.log("error",
                    f"file sync has failed {self.state.consecutive_failures} "
                    f"times in a row — tonight's frames are NOT leaving the rig "
                    f"({result.error or 'see the log above'})", "sync")
        elif not self.state.should_alarm and self._alarmed:
            self._alarmed = False
            bus.log("info", "file sync is working again", "sync")


#: The instance the app lifespan starts and the capture path pokes.
runner = PushRunner()
