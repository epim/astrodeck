"""Last-known device state, so a power cut is DETECTABLE rather than guessed.

This module RECORDS; it never restores. Driving a focuser back to a remembered
number would be a guess about a device that has just told us it does not know
where it is — the recovery ladder measures instead (autofocus, plate solve).

Detecting the CONDITION rather than the CAUSE is deliberate. The alternative,
reading Windows' unexpected-shutdown event, answers a different question: it
would miss a yanked USB hub, a browned-out powered hub, or a device that reset
on its own while the PC stayed up. Comparing what a device reports against what
it last reported catches all of those, and ports to any OS.

There is deliberately NO pointing verdict. The AM5 is a harmonic drive with no
brake, so a heavy OTA can sag while the motors are unpowered, and the mount's
own encoders cannot report a shift that happened while it was off. Re-centering
is therefore unconditional, and a verdict about pointing would only ever be a
reason to skip a check that must never be skipped.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..persist import read_json_or, write_json_atomic

#: A tracked field rarely changes, but the status poll runs several times a
#: second. Coalesce so this is not rewriting the file continuously.
FINGERPRINT_WRITE_INTERVAL_S = 10.0

_PATH: Path | None = None
_last_write: float = 0.0


def _now() -> float:
    return time.monotonic()


def _path() -> Path:
    """Resolved on EVERY call, never cached.

    Caching it in the module global was order-dependent: whichever caller
    touched this first froze the path to the CAPTURE_DIR of that moment, so a
    later reader looked in a directory the writer had stopped using. It surfaced
    as two unrelated resume tests failing in the full suite while passing alone
    — the signature of shared state, and the same class of flake as reading the
    process-wide bus ring. A Path join costs nothing; correctness here is worth
    more than the microsecond.

    ``_PATH`` remains as a test override only.
    """
    if _PATH is not None:
        return _PATH
    from ..hub import CAPTURE_DIR
    return Path(CAPTURE_DIR) / "device_fingerprint.json"


@dataclass(frozen=True)
class Verdict:
    #: False when the focuser's live position disagrees with the record — the
    #: EAF forgets its position on power loss, so a mismatch means it lost count.
    focus_trusted: bool


def record(*, focuser_position: int | None, filter_slot: int | None,
           ra_hours: float | None, dec_deg: float | None,
           parked: bool | None, tracking: bool | None) -> None:
    """Persist current device state, at most once per interval.

    Written with the atomic writer so a power cut mid-write cannot leave a
    truncated file — the one failure that would make this module lie exactly
    when it matters. Swallows its own errors: bookkeeping must never break a run.
    """
    global _last_write
    now = _now()
    if _last_write and now - _last_write < FINGERPRINT_WRITE_INTERVAL_S:
        return
    _last_write = now
    try:
        write_json_atomic(_path(), {
            "focuser_position": focuser_position,
            "filter_slot": filter_slot,
            "ra_hours": ra_hours,
            "dec_deg": dec_deg,
            "parked": parked,
            "tracking": tracking,
        }, backup=False)
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        pass


def verdict(*, focuser_position: int | None) -> Verdict:
    """Compare live device state against the record.

    An absent or unreadable file trusts NOTHING. That is a first-ever boot or a
    wiped state directory, and distrust costs only an autofocus, while misplaced
    trust costs a night of blurred frames.
    """
    raw = read_json_or(_path(), None)
    if not isinstance(raw, dict):
        return Verdict(focus_trusted=False)
    known = raw.get("focuser_position")
    # EXACT match. The EAF is a stepper reporting integers, so any difference at
    # all means it lost count; there is no meaningful tolerance to allow.
    try:
        trusted = (known is not None and focuser_position is not None
                   and int(known) == int(focuser_position))
    except (TypeError, ValueError):
        trusted = False
    return Verdict(focus_trusted=bool(trusted))


def reset_for_tests() -> None:
    """Drop the coalescing latch so a test's first record() always writes."""
    global _last_write
    _last_write = 0.0
