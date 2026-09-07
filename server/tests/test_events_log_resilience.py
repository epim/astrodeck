"""The night log has to survive the night (2026-09-06 22:13:46).

A recursion in the sequence engine published "holding for clear sky" 323 times
in one second. Two things broke at once:

* the RecursionError surfaced inside ``NightLogWriter.append``, whose
  ``except Exception: self.failed = True`` turned the disk log off for the rest
  of the process. ``captures/logs/2026-09-06.jsonl`` ends at 22:13:46 and the
  run went on until dawn. ``/api/logs`` kept serving the 200-entry ring, so
  nothing looked wrong until the morning.
* nothing anywhere collapsed the storm, so those 323 identical lines evicted
  the whole ring and would have filled the file if the writer had lived.

So: the writer pauses and retries instead of giving up, and identical lines
arriving faster than STORM_PASS per STORM_WINDOW_S collapse into one summary.
"""
from __future__ import annotations

import pytest

from astrodeck import events
from astrodeck.events import (NIGHTLOG_RETRY_S, STORM_PASS, STORM_WINDOW_S,
                              EventBus, night_key)


@pytest.fixture()
def clock(monkeypatch):
    """A monotonic clock the test drives, for both cooldowns."""

    class _Clock:
        t = 1000.0

        def advance(self, dt: float) -> None:
            self.t += dt

    c = _Clock()
    monkeypatch.setattr(events, "_now", lambda: c.t)
    return c


@pytest.fixture()
def bus(tmp_path, monkeypatch):
    """A private bus whose night log writes under ``tmp_path``."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    return EventBus()


def _messages(bus) -> list[str]:
    return [e["data"]["message"] for e in bus.log_history]


class _Breaker:
    """Stands in for ``open`` in the writer's module namespace."""

    def __init__(self, exc: BaseException | None):
        self.exc = exc
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return open(*a, **k)


# ------------------------------------------------- A: the writer must recover

@pytest.mark.parametrize("exc", [OSError("disk full"),
                                 ValueError("not an OSError")])
def test_a_failed_write_pauses_for_the_cooldown_then_writes_again(
        bus, clock, monkeypatch, tmp_path, exc):
    """The bug: ONE failure disabled the file for the life of the process.

    A disk that is full at 22:13 usually is not at 23:13, and the night the
    writer gives up is the night the operator needs the file. Both an OSError
    and anything else must pause, not stop."""
    breaker = _Breaker(exc)
    monkeypatch.setattr(events, "open", breaker, raising=False)

    bus.log("info", "the line that fails", "sequence")
    assert bus.night_log.failed is True, "a failed write must pause the writer"
    assert breaker.calls == 1

    # Inside the cooldown the writer does not even try: no thrashing on a disk
    # that is still full.
    clock.advance(NIGHTLOG_RETRY_S - 1.0)
    bus.log("info", "still inside the cooldown", "sequence")
    assert breaker.calls == 1, "the writer retried before its cooldown elapsed"
    assert bus.night_log.failed is True

    # Past it, it tries again -- and the disk is back.
    clock.advance(2.0)
    monkeypatch.setattr(events, "open", _Breaker(None), raising=False)
    bus.log("info", "after the cooldown", "sequence")
    assert bus.night_log.failed is False, (
        "a write that worked must un-pause the writer; on 2026-09-06 the file "
        "stayed dead for nine hours after one exception")
    rows = bus.night_log.read(night_key())
    assert [r["data"]["message"] for r in rows] == ["after the cooldown"]


def test_the_pause_and_the_resume_are_each_announced_exactly_once(
        bus, clock, monkeypatch):
    """Silence is how this defect hid for a night: the API looked healthy.

    One warning when persistence stops, one line when it comes back, and no
    repetition of either -- a line per dropped event would be its own storm."""
    monkeypatch.setattr(events, "open", _Breaker(OSError("read-only fs")),
                        raising=False)
    bus.log("info", "one", "sequence")
    bus.log("info", "two", "sequence")          # drains the pause notice first
    bus.log("info", "three", "sequence")

    paused = [m for m in _messages(bus) if "paused" in m]
    assert paused == ["night log file writer paused: OSError: read-only fs; "
                      f"retrying in {NIGHTLOG_RETRY_S:.0f} s"], _messages(bus)
    entry = [e for e in bus.log_history if "paused" in e["data"]["message"]][0]
    assert entry["data"]["level"] == "warning"
    assert entry["data"]["source"] == "log"

    clock.advance(NIGHTLOG_RETRY_S + 1.0)
    monkeypatch.setattr(events, "open", _Breaker(None), raising=False)
    bus.log("info", "four", "sequence")         # the write that works again
    bus.log("info", "five", "sequence")         # carries the resume notice
    bus.log("info", "six", "sequence")

    resumed = [m for m in _messages(bus) if "resumed" in m]
    assert resumed == [f"night log file writer resumed after "
                       f"{NIGHTLOG_RETRY_S + 1.0:.0f} s"], _messages(bus)
    assert [m for m in _messages(bus) if "paused" in m] == paused


def test_a_recursion_error_inside_the_write_does_not_propagate(bus, monkeypatch):
    """What actually happened: the RecursionError arrived from the CALLER's
    stack, surfacing at the first call ``append`` made. Re-raising it -- or
    logging from the handler, which re-enters the bus at that same depth --
    takes the publish down with it. The handler does stores only."""
    monkeypatch.setattr(events, "open",
                        _Breaker(RecursionError("maximum recursion depth "
                                                "exceeded")), raising=False)
    bus.log("warning", "holding for clear sky", "sequence")   # must not raise
    assert bus.night_log.failed is True
    assert _messages(bus) == ["holding for clear sky"], (
        "the notice must wait for the next publish, not be emitted from the "
        "failing write's stack")
    bus.log("info", "the next line", "sequence")
    assert any("paused" in m for m in _messages(bus))


def test_the_writer_never_stops_trying(bus, clock, monkeypatch):
    """Ten failed cooldowns later it is still retrying."""
    breaker = _Breaker(OSError("disk full"))
    monkeypatch.setattr(events, "open", breaker, raising=False)
    for _ in range(10):
        clock.advance(NIGHTLOG_RETRY_S + 0.1)
        bus.log("info", "keep trying", "sequence")
    assert breaker.calls >= 10, f"gave up after {breaker.calls} attempts"


# ------------------------------------------------------- B: the storm limiter

def test_a_storm_of_identical_lines_collapses_to_a_count(bus, clock):
    """323 copies of one sentence in one second is not information, and it
    evicts the 200-entry ring that IS."""
    for _ in range(500):
        bus.log("warning", "holding for clear sky", "sequence")
    bus.flush()
    assert _messages(bus) == ["holding for clear sky"] * STORM_PASS + [
        f"holding for clear sky (repeated {500 - STORM_PASS} more times in "
        f"{STORM_WINDOW_S:.1f} s)"]
    summary = bus.log_history[-1]["data"]
    assert summary["level"] == "warning" and summary["source"] == "sequence"


def test_the_first_line_is_never_delayed(bus, clock):
    bus.log("warning", "holding for clear sky", "sequence")
    assert _messages(bus) == ["holding for clear sky"]


def test_two_different_lines_alternating_are_not_collapsed(bus, clock):
    """A busy loop is not a storm. Suppressing interleaved lines would hide the
    other half of the night."""
    for _ in range(50):
        bus.log("info", "holding for clear sky", "sequence")
        bus.log("info", "native guider stopped", "guide")
    bus.flush()
    assert len(_messages(bus)) == 100
    assert not any("repeated" in m for m in _messages(bus))


def test_the_same_line_after_the_window_is_new_news(bus, clock):
    """A once-a-minute repeat is a heartbeat, not a storm: it goes through
    untouched, with the previous window's summary ahead of it."""
    for _ in range(10):
        bus.log("info", "mount slewing", "mount")
    clock.advance(STORM_WINDOW_S + 0.1)
    bus.log("info", "mount slewing", "mount")
    assert _messages(bus) == ["mount slewing"] * STORM_PASS + [
        f"mount slewing (repeated {10 - STORM_PASS} more times in "
        f"{STORM_WINDOW_S:.1f} s)", "mount slewing"]


def test_a_different_line_closes_the_window_and_reports_the_count(bus, clock):
    for _ in range(10):
        bus.log("info", "holding for clear sky", "sequence")
    bus.log("info", "sky is clear, resuming", "sequence")
    assert _messages(bus)[-2:] == [
        f"holding for clear sky (repeated {10 - STORM_PASS} more times in "
        f"{STORM_WINDOW_S:.1f} s)", "sky is clear, resuming"]


def test_the_storm_never_reaches_the_disk_either(bus, clock):
    """The file is the durable record and it has the same 200-lines-of-one-
    sentence problem, at a much larger scale."""
    for _ in range(500):
        bus.log("info", "holding for clear sky", "sequence")
    bus.flush()
    rows = bus.night_log.read(night_key())
    assert len(rows) == STORM_PASS + 1, [r["data"]["message"] for r in rows]


def test_a_level_or_source_change_is_a_different_line(bus, clock):
    """Same words, different verdict: an error is not a repeat of an info, and
    the guider saying it is not the sequence engine saying it. More than
    STORM_PASS of them, so a limiter keyed on the message alone collapses."""
    for _ in range(4):
        bus.log("info", "guider settled", "guide")
        bus.log("error", "guider settled", "guide")
        bus.log("error", "guider settled", "sequence")
    bus.flush()
    assert len(_messages(bus)) == 12, _messages(bus)
    assert not any("repeated" in m for m in _messages(bus))
