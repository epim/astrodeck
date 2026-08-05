"""Last-known device state, so a power cut is DETECTABLE rather than guessed.

This module RECORDS; it never restores. Driving a focuser back to a remembered
number would be a guess about a device that has just told us it does not know
where it is — the recovery ladder measures instead (autofocus, plate solve).

Detecting the CONDITION rather than the CAUSE is deliberate. The alternative,
reading Windows' unexpected-shutdown event, answers a different question: it
would miss a yanked USB hub, a browned-out powered hub, or a device that reset
on its own while the PC stayed up. Comparing what a device reports against what
it last reported catches all of those, and ports to any OS. "What it last
reported" means the number on disk BEFORE this process started: the status poll
rewrites the file within seconds of connect, so anything comparing against the
live file is comparing the boot against itself.

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

#: The file AS IT WAS WHEN THIS PROCESS STARTED, read once inside record()
#: before that record's own first write. Without it the record is destroyed by
#: the boot it exists to describe: the status poll writes the POST-restart
#: position within ~2 s of connect, so by the resume ladder's first tick the
#: file holds this boot's own reading and the comparison answers "trusted"
#: after a cut that moved nothing AND after one that moved everything. Same
#: read-before-write shape as zwo_usb._check_position_reference.
_boot: dict | None = None
_boot_loaded: bool = False
#: WHICH file the snapshot came from. A snapshot describes one state directory,
#: so if _path() ever resolves somewhere else the snapshot is about a different
#: file and is re-taken. In production the path never moves and this is a
#: no-op; it exists so this cached state cannot become the shared-state flake
#: _path() itself was uncached to kill — see its docstring.
_boot_path: Path | None = None

#: What the focuser is BELIEVED to hold, carried across every gap in
#: observation — a restart, a USB drop, a browned-out hub. Advances only while
#: the device has been watched continuously (see _observe): a move we saw
#: happen is a move, a number that appeared while we were not looking is not.
_known: int | None = None
#: The last position the device actually reported, so a disconnect writes the
#: remembered number forward rather than blanking it.
_last_pos: int | None = None
#: Whether the device has re-proved itself since the last gap.
_confirmed: bool = False


def _now() -> float:
    return time.monotonic()


def _as_int(value: object) -> int | None:
    """Ints or nothing — a stepper count that will not coerce is not a count."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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


def _ensure_boot() -> None:
    """Take the pre-boot snapshot once, before this process's first write.

    Read BEFORE that write because afterwards the file is our own post-restart
    reading, and comparing that to the device is comparing the device to
    itself. Loaded lazily rather than at import because _path() is resolved
    late on purpose — see its docstring.
    """
    global _boot, _boot_loaded, _boot_path, _known, _last_pos, _confirmed
    path = _path()
    if _boot_loaded and path == _boot_path:
        return
    _boot = read_json_or(path, None)
    _boot_loaded = True
    _boot_path = path
    _known = _last_pos = (_as_int(_boot.get("focuser_position"))
                          if isinstance(_boot, dict) else None)
    _confirmed = False            # a restart IS a gap; nothing watched yet


def _observe(focuser_position: int | None) -> None:
    """Track the focuser across gaps — on EVERY call, not once per write.

    Coalescing exists to spare the disk. Running this behind it would let a
    device that dropped and came back inside one 10 s window look continuous,
    which is precisely the event this module exists to catch.
    """
    global _known, _last_pos, _confirmed
    _ensure_boot()
    pos = _as_int(focuser_position)
    if pos is None:
        # Disconnected, or no focuser on this rig at all. Keep the number and
        # open a gap: whatever comes back has to prove it is the same device
        # holding the same count.
        _confirmed = False
        return
    if _confirmed:
        _known = pos              # watched the whole way: a real move
    elif _known is not None and pos == _known:
        _confirmed = True         # came back holding what it held
    # A first reading that DISAGREES leaves _known alone. It is the power-cut
    # tell, and nothing in here can re-establish it — only a measurement
    # (autofocus, plate solve) knows where the device really is.
    _last_pos = pos


def record(*, focuser_position: int | None, filter_slot: int | None,
           ra_hours: float | None, dec_deg: float | None,
           parked: bool | None, tracking: bool | None) -> None:
    """Persist current device state, at most once per interval.

    Written with the atomic writer so a power cut mid-write cannot leave a
    truncated file — the one failure that would make this module lie exactly
    when it matters. Swallows its own errors: bookkeeping must never break a run.
    """
    global _last_write
    _observe(focuser_position)
    now = _now()
    if _last_write and now - _last_write < FINGERPRINT_WRITE_INTERVAL_S:
        return
    _last_write = now
    try:
        write_json_atomic(_path(), {
            # _last_pos, not the argument: a focuser that has dropped off the
            # bus reports None, and writing that would blank the one number the
            # next boot has to compare against — an unrelated cable would make
            # the power cut undetectable.
            "focuser_position": _last_pos,
            "filter_slot": filter_slot,
            "ra_hours": ra_hours,
            "dec_deg": dec_deg,
            "parked": parked,
            "tracking": tracking,
        }, backup=False)
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        pass


def _judge(known: object, focuser_position: int | None) -> Verdict:
    # EXACT match. The EAF is a stepper reporting integers, so any difference at
    # all means it lost count; there is no meaningful tolerance to allow.
    try:
        trusted = (known is not None and focuser_position is not None
                   and int(known) == int(focuser_position))
    except (TypeError, ValueError):
        trusted = False
    return Verdict(focus_trusted=bool(trusted))


def verdict(*, focuser_position: int | None) -> Verdict:
    """Compare live device state against the record.

    An absent or unreadable file trusts NOTHING. That is a first-ever boot or a
    wiped state directory, and distrust costs only an autofocus, while misplaced
    trust costs a night of blurred frames.

    The basis is the PRE-BOOT number (_known), not the live file, once anything
    has recorded in this process — the recorder overwrites the file with this
    boot's own reading long before the resume ladder's first tick. The file is
    still read when nothing has recorded yet: that is the un-restarted case,
    where the file has not been touched since the last process wrote it.
    """
    path = _path()
    if _boot_loaded and path == _boot_path:
        return _judge(_known, focuser_position)
    raw = read_json_or(path, None)
    if not isinstance(raw, dict):
        return Verdict(focus_trusted=False)
    return _judge(raw.get("focuser_position"), focuser_position)


def vouch(*, focuser_position: int | None) -> None:
    """Adopt a MEASURED position as the trusted reference.

    Nothing else in this module can re-establish trust once a gap has opened,
    and that is correct: a number the device reports after a power cut is a
    default, not a measurement. But an autofocus IS a measurement, and the
    recovery ladder runs one precisely because the fingerprint said the focuser
    had lost count. Without a way to say so, a ladder that autofocuses and then
    refuses at the plate solve re-runs the whole autofocus on every ten-minute
    retry — minutes of mount time and focuser travel per tick, under exactly
    the cloudy conditions that cause the retries.

    Only a caller that has just MEASURED may call this. The status poll must
    not: it reads what the device claims, which is the thing being doubted.
    """
    global _known, _last_pos, _confirmed
    pos = _as_int(focuser_position)
    if pos is None:
        return
    _ensure_boot()        # never let this be the read that skips the snapshot
    _known = _last_pos = pos
    _confirmed = True


def reset_for_tests() -> None:
    """Drop the coalescing latch AND the boot snapshot: a test's first record()
    writes, and re-reads the file as a fresh process would.

    The snapshot has to be here or every test after the first in a worker
    inherits the previous one's — trust it never established, showing up only as
    an order-dependent flake. It is also how a test SIMULATES a restart: the
    file survives, the process state does not.
    """
    global _last_write, _boot, _boot_loaded, _boot_path
    global _known, _last_pos, _confirmed
    _last_write = 0.0
    _boot = None
    _boot_loaded = False
    _boot_path = None
    _known = None
    _last_pos = None
    _confirmed = False
