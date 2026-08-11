"""Push a night's frames to a destination while the run is still going.

Phase 1 gave the rig a manifest a puller could read. This is the same
reconciliation driven from the rig's side, for the case where the processing box
cannot reach in — a laptop on a different network, a NAS that only accepts
writes, a machine that is asleep when you would have pulled.

WHAT IT DOES, once, per pass:

    source = build(walk_facts(captures), settle_s)     # only settled files
    dest   = destination.manifest()
    for entry in diff(source, dest).transfers:          # oldest first
        destination.put(entry.relpath, captures / entry.relpath)

That is the whole algorithm, and it is deliberately the same three lines the
pull agent runs. No queue, no "sent" flag, no per-file state: a pass that dies
halfway leaves a destination that is simply missing some files, and the next
pass computes exactly those. See :mod:`.manifest` for why that property is
non-negotiable here.

WHAT DRIVES A PASS. The ``saved`` bus event, debounced — not a directory
watcher, and not a timer alone:

* a watcher fires on ``writeto`` OPENING the file, which is the torn-frame
  hazard the settle window exists to survive;
* a timer alone would push a frame up to its whole interval late, and the point
  of this feature is that a frame is on the processing box shortly after it
  lands.

The debounce matters because frames arrive in bursts around a filter change, and
each pass costs a full destination hash. A periodic sweep still runs underneath
so that a missed event, a restart, or a destination that was offline for an hour
all heal without anyone noticing they broke.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import manifest as _manifest
from .destination import Destination

#: How long after the last ``saved`` event a pass actually starts. Long enough
#: that a burst of frames costs one destination hash rather than five, short
#: enough that a frame is on the far end well before the next one lands.
DEBOUNCE_S = 20.0

#: The unconditional sweep. Nothing depends on an event being delivered — this
#: is what makes a missed event, a restart mid-pass, or an hour of destination
#: downtime self-healing rather than a permanent hole.
SWEEP_INTERVAL_S = 900.0

#: Consecutive failing passes before the runner says so loudly. One failure is a
#: NAS asleep; several in a row is a broken destination, and a night that
#: silently never left the rig is the outcome this whole feature exists to stop.
FAIL_LOUD_AFTER = 3


@dataclass
class PushResult:
    """What one pass did. Every field is measured, none is remembered."""
    considered: int = 0          # entries the source offered (settled only)
    sent: int = 0
    bytes_sent: int = 0
    failed: int = 0
    already_there: int = 0
    extra_at_destination: int = 0    # reported, NEVER acted on
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.failed == 0

    def summary(self) -> str:
        if self.error:
            return f"push failed: {self.error}"
        if not self.sent and not self.failed:
            return (f"push: nothing to send "
                    f"({self.already_there} already there)")
        parts = [f"pushed {self.sent} frame{'' if self.sent == 1 else 's'}",
                 f"{self.bytes_sent / 1e9:.2f} GB",
                 f"{self.elapsed_s:.0f}s"]
        if self.failed:
            parts.append(f"{self.failed} FAILED")
        return "push: " + ", ".join(parts)


def push_once(source_root: Path, dest: Destination, *,
              settle_s: float = _manifest.DEFAULT_SETTLE_S,
              limit: int = 0,
              now: float | None = None) -> PushResult:
    """One reconciliation pass. Never raises: a destination that is unreachable
    is a fact to report, not an exception to unwind a capture through.

    ``limit`` bounds a pass to that many transfers, so a first run against a
    9 GB backlog can be spread over several passes instead of holding the disk
    for twenty minutes straight. What is left is simply still missing next pass
    — no bookkeeping is required to resume, which is the point.
    """
    t0 = time.time()
    out = PushResult()
    try:
        t_now = time.time() if now is None else now
        src = _manifest.build(_manifest.walk_facts(source_root),
                              root=source_root, now=t_now, settle_s=settle_s)
        have = dest.manifest()
    except Exception as e:  # noqa: BLE001 — an offline destination is data
        out.error = f"{type(e).__name__}: {e}"
        out.elapsed_s = time.time() - t0
        return out

    d = _manifest.diff(src, have)
    out.considered = len(src.entries)
    out.already_there = d.identical
    out.extra_at_destination = len(d.extra)

    todo = d.transfers
    if limit > 0:
        todo = todo[:limit]
    for entry in todo:
        try:
            out.bytes_sent += dest.put(entry.relpath, source_root / entry.relpath)
            out.sent += 1
        except Exception:  # noqa: BLE001 — one bad file is not a bad night
            out.failed += 1
    out.elapsed_s = time.time() - t0
    return out


@dataclass
class PushState:
    """The runner's view of itself — for the status payload and the log.

    Everything here is an OBSERVATION of passes that already happened. Nothing
    in it is consulted to decide what to send; that always comes from a fresh
    diff. A field here going wrong can make the UI wrong, but it cannot make the
    sync wrong, and that separation is deliberate.
    """
    last_result: PushResult | None = None
    last_ok_at: float = 0.0
    last_attempt_at: float = 0.0
    consecutive_failures: int = 0
    total_sent: int = 0
    total_bytes: int = 0
    passes: int = 0

    def record(self, r: PushResult, *, now: float | None = None) -> None:
        t = time.time() if now is None else now
        self.last_result = r
        self.last_attempt_at = t
        self.passes += 1
        self.total_sent += r.sent
        self.total_bytes += r.bytes_sent
        if r.ok:
            self.last_ok_at = t
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1

    @property
    def should_alarm(self) -> bool:
        return self.consecutive_failures >= FAIL_LOUD_AFTER

    def payload(self) -> dict:
        r = self.last_result
        return {
            "passes": self.passes,
            "total_sent": self.total_sent,
            "total_bytes": self.total_bytes,
            "last_attempt_at": self.last_attempt_at or None,
            "last_ok_at": self.last_ok_at or None,
            "consecutive_failures": self.consecutive_failures,
            "alarm": self.should_alarm,
            "last": None if r is None else {
                "sent": r.sent, "failed": r.failed,
                "bytes_sent": r.bytes_sent,
                "already_there": r.already_there,
                "extra_at_destination": r.extra_at_destination,
                "elapsed_s": round(r.elapsed_s, 1),
                "error": r.error,
                "summary": r.summary(),
            },
        }


def due(state: PushState, *, pending_event: bool, now: float,
        debounce_s: float = DEBOUNCE_S,
        sweep_interval_s: float = SWEEP_INTERVAL_S,
        last_event_at: float = 0.0) -> bool:
    """Should a pass run right now? Pure, so the cadence is testable without
    sleeping and without a filesystem.

    Two independent reasons, and the sweep is NOT conditional on the event:
    that is what makes a dropped event, a restart, or an offline hour heal by
    itself rather than leaving a hole nobody notices until the night is over.
    """
    if pending_event and now - last_event_at >= debounce_s:
        return True
    return now - state.last_attempt_at >= sweep_interval_s
