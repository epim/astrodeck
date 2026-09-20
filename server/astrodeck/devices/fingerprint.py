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

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..persist import read_json_or, write_json_atomic

#: A tracked field rarely changes, but the status poll runs several times a
#: second. Coalesce so this is not rewriting the file continuously.
FINGERPRINT_WRITE_INTERVAL_S = 10.0

#: A write that takes longer than this announces itself. The disk cost of this
#: file was invisible for months because nothing ever timed it; on the rig it
#: reached 7 s because the write re-hardened the whole capture tree.
FINGERPRINT_SLOW_WRITE_S = 1.0

#: record() is no longer single-threaded: the status poll dispatches it with
#: asyncio.to_thread, and poll_status runs both from the status loop and from
#: the /api/status route, so two worker threads can enter at once.
#:
#: TWO LOCKS, and they are never held at the same time, so there is no lock
#: order to get wrong. record takes _state_lock for the observation, releases
#: it, then takes _write_lock for the latch and the write.
#:
#: _state_lock guards ONLY the observation trio -- _known/_last_pos/_confirmed
#: -- wherever it is touched: _observe (with its one-time _ensure_boot read),
#: verdict's in-process branch, and vouch. Unlocked, a vouch carrying a
#: MEASURED position (an autofocus result) could be overwritten by a worker
#: mid-_observe holding a stale device reading; verdict would then answer
#: focus_trusted=False and the resume ladder would re-run a full autofocus
#: every ten minutes -- precisely the waste vouch exists to prevent. Held for
#: microseconds, which is why the loop may take it.
#:
#: IT DOES NOT COVER THE WRITE, and that is the point. MEASURED around a real
#: record(): a small already-private state directory holds 3.46 ms, 2000 files
#: already private 7.52 ms, and 2000 files NOT yet private 89.64 ms -- and the
#: rig is roughly 10x slower on this path. The case that decides it is the
#: first write of a process into a captures/ that is not yet protected: the
#: full propagating SetSecurityInfo, 7057 ms on the rig, reachable on a
#: first-ever boot, a fresh install, an upgrade from a pre-hardening release
#: or a restored capture tree. A verdict() or vouch() on the loop behind one
#: lock covering that write is a multi-second event-loop stall -- the class
#: this whole change exists to kill, and harder to see than the original,
#: because py-spy would show verdict waiting on a lock rather than the write.
_state_lock = threading.Lock()

#: The coalescing latch AND the write, together and never apart. Splitting
#: those two would let two callers both read _last_write before either set it
#: and then interleave two atomic writes over the same staging directory,
#: which is the thing the original single lock was added for.
#:
#: The cost of the split: another thread may advance _last_pos between this
#: call's _observe and its write, so the file can carry a reading microseconds
#: newer than the one this caller saw. Same device, newer number -- and the
#: written value was always "the latest reading", never "this call's argument"
#: (see the comment on the payload). Nothing downstream can tell the
#: difference, and nothing that can block indefinitely is held under it.
_write_lock = threading.Lock()

#: A slow write recorded by the worker thread, for the coroutine that
#: dispatched it to publish. NOT ``bus.log`` from inside ``record``:
#: ``EventBus.publish`` hands each event to ``asyncio.Queue.put_nowait``, which
#: sets futures and calls ``loop.call_soon`` -- loop-affine, not thread-safe,
#: and its night-log append does file I/O. Same rule, same reason, as the ZWO
#: pulse watchdog thread (``devices/backends/zwo_am5``): the thread records,
#: the loop says it.
#:
#: NO ABSOLUTE PATH IN THE MESSAGE. It goes to the bus, so it reaches the WS
#: stream, the /api/logs ring (CAP_VIEW_STATUS, the lowest capability) and the
#: durable night log. The owner ruling at the top of
#: ``tests/test_no_absolute_paths_externally.py`` is "no absolute filesystem
#: path leaves this process, for anybody", and CAPTURE_DIR names the
#: operator's Windows account. The elapsed time is the whole signal; the file
#: name is carried the way every other bus.log in this codebase carries one.
_slow_write_notice: str | None = None

#: Its own lock, so the read-then-clear in ``take_slow_write_notice`` is atomic
#: against the worker that sets it. A third lock rather than _write_lock, for
#: the same reason _state_lock is not _write_lock: the reader is the event
#: loop, and the notice is set from inside the write, so sharing that lock
#: would make the loop queue behind the write. Held for one assignment, never
#: across I/O.
#:
#: The only nesting anywhere in this module: record acquires it while holding
#: _write_lock, and nothing acquires _write_lock while holding it. _state_lock
#: is never held together with either. One direction, so no cycle.
_notice_lock = threading.Lock()

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

    Callable from any thread; the caller on the status path dispatches it off
    the event loop. See ``_state_lock`` for why the observation and the write
    take different locks, and why neither is ever held across the other.
    """
    global _last_write
    with _state_lock:
        _observe(focuser_position)
    with _write_lock:
        now = _now()
        if _last_write and now - _last_write < FINGERPRINT_WRITE_INTERVAL_S:
            return
        _last_write = now
        try:
            path = _path()
            started = time.monotonic()
            outcome = "took"
            try:
                write_json_atomic(path, {
                    # _last_pos, not the argument: a focuser that has dropped
                    # off the bus reports None, and writing that would blank
                    # the one number the next boot has to compare against — an
                    # unrelated cable would make the power cut undetectable.
                    # Read outside _state_lock, so a concurrent caller's
                    # _observe may have advanced it since this call's own:
                    # the same device, a reading microseconds newer. See the
                    # note on _write_lock.
                    "focuser_position": _last_pos,
                    "filter_slot": filter_slot,
                    "ra_hours": ra_hours,
                    "dec_deg": dec_deg,
                    "parked": parked,
                    "tracking": tracking,
                }, backup=False)
            except Exception:
                outcome = "failed after"
                raise
            finally:
                # IN THE FINALLY. Timed only on success, a 7 s propagating
                # SetSecurityInfo that then raised PrivatePermissionsError
                # would be swallowed in silence -- which is the invisibility
                # this self-report exists to end. Say so, every time it
                # happens: a bookkeeping write is meant to be free, and one
                # that is not has to be visible without a profiler attached to
                # the live server. Handed to the loop -- see
                # _slow_write_notice, and note what may NOT go in the text.
                elapsed = time.monotonic() - started
                if elapsed >= FINGERPRINT_SLOW_WRITE_S:
                    _set_slow_write_notice(
                        f"device fingerprint write {outcome} "
                        f"{elapsed:.1f}s ({path.name})"
                    )
        except Exception:  # noqa: BLE001 — telemetry must never break a run
            pass


def _set_slow_write_notice(notice: str) -> None:
    global _slow_write_notice
    with _notice_lock:
        _slow_write_notice = notice


def take_slow_write_notice() -> str | None:
    """Hand any pending slow-write warning to a caller ON THE LOOP THREAD.

    Called once per dispatch by whoever ran :func:`record` off the loop; the
    bus publish has to happen there rather than in the worker. See
    ``_slow_write_notice``. The read-and-clear is atomic against the worker
    that sets it, so one slow write is announced exactly once.
    """
    global _slow_write_notice
    with _notice_lock:
        notice, _slow_write_notice = _slow_write_notice, None
    return notice


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

    Reads the in-process trio under ``_state_lock``, because a worker thread
    can be inside ``_observe`` mutating it. The lock does NOT cover the disk
    read: that would put the loop behind whatever the file system is doing.

    THE DECISION IS RE-TAKEN AFTER THE READ, not before it. Deciding "nothing
    has recorded in this process, so read the file", releasing, and then
    reading leaves a window in which a worker's ``record`` can complete both
    ``_ensure_boot`` and its ``os.replace``. The file read back is then one
    this boot has just written, and the comparison is the device against
    itself: focus_trusted=True after a power cut that moved the focuser, the
    ladder skips the autofocus, and the night is soft. That is the exact
    "comparing the boot against itself" hazard the ``_boot`` snapshot exists
    to prevent, and it is the dangerous direction -- misplaced trust, not a
    wasted autofocus. So the file is read speculatively and thrown away if the
    snapshot has appeared in the meantime.
    """
    path = _path()
    with _state_lock:
        if _boot_loaded and path == _boot_path:
            return _judge(_known, focuser_position)
    raw = read_json_or(path, None)
    with _state_lock:
        if _boot_loaded and path == _boot_path:
            # A worker recorded while we were reading; that file is now this
            # boot's own output. The snapshot is the only honest basis left.
            return _judge(_known, focuser_position)
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

    Under ``_state_lock`` for the whole adoption. A worker thread inside
    ``_observe`` writes the same trio, and with ``_confirmed`` true it assigns
    the device's LIVE reading to ``_known``; interleaved, that reading lands
    after the measurement and silently replaces it. ``_ensure_boot`` is inside
    the lock too, because it writes the trio as well.
    """
    global _known, _last_pos, _confirmed
    pos = _as_int(focuser_position)
    if pos is None:
        return
    with _state_lock:
        _ensure_boot()    # never let this be the read that skips the snapshot
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
    global _last_write, _boot, _boot_loaded, _boot_path, _slow_write_notice
    global _known, _last_pos, _confirmed
    _last_write = 0.0
    _slow_write_notice = None
    _boot = None
    _boot_loaded = False
    _boot_path = None
    _known = None
    _last_pos = None
    _confirmed = False
