# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#993 and #994: the hub, the app and the engine's delete say what they swallow.

#993. After #811 (the dispatcher's timer), #936 (the per-frame dead-man) and
#964 (the engine's fifteen), ``hub.py`` still held 46 handlers whose whole body
was ``pass`` and ``api/app.py`` 11. A raise on any of them was lost without a
trace, and the only evidence was the thing that failed: a status poll that
raised as a whole published nothing (every client's status simply stopped), a
block that raised was missing from the frame, a header card that could not be
read was left off the file, the guider that would not stop for a meridian flip
went on pulsing through the slew, and the operator's PARK could leave the sun
watch's blind fallback stale (the false 'Parking now' page in #696).

The ones on a path whose failure matters now hand what they caught to
``Hub.say_swallowed``: one warning per (step, exception type) per observing
night, the type's name and never its text. The rest keep their ``pass`` with
the reason beside them, and the last cases here are the ratchets that keep the
list honest.

#994. ``SequenceEngine._unlink_saved`` swallowed an ``OSError``, so a rejected
frame that ``discard`` should have deleted could stay in the capture folder,
where a stacker's glob picks it up, with nothing logged. It now says which file
is still there, by name, one line per file.

The cases are RED on the code before the fix (each said nothing, or the method
they call did not exist); the mutants run against it are in
``scratchpad/w22/WP-206-report.md``.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrodeck.api.app as app_module
import astrodeck.hub as hub_mod
from _simhub import sim_hub  # noqa: F401  (fixture import)
from astrodeck.config import ConfigStore
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, Target

#: Text no line may carry: what an exception's message could quote.
_TEXT = "SECRET-PATH-C:/private/place.fits"


def _boom(kind: type[Exception] = RuntimeError) -> Exception:
    return kind(f"boom {_TEXT}")


def _said(bus_lines, what: str, source: str) -> list[str]:
    """The warnings on ``source`` that carry ``what``."""
    return [m for lvl, m, src in bus_lines
            if lvl == "warning" and src == source and what in m]


@pytest.fixture(autouse=True)
def _fresh_latch(monkeypatch):
    """The module-level ``hub`` outlives a test, and so does its once-per-night
    latch: start each case with nothing said (``say_swallowed`` builds a fresh
    set when the night it last saw is not today's)."""
    monkeypatch.setattr(app_module.hub, "_swallowed_night", None, raising=False)
    monkeypatch.setattr(app_module.hub, "_swallowed_said", set(), raising=False)


# ------------------------------------------------------------------------ #994

def _capture_dir(tmp_path, monkeypatch) -> Path:
    cap = tmp_path / "captures"
    cap.mkdir()
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    return cap


def _rejected(cap: Path, name: str = "NGC6946_L_0007.fits") -> Path:
    f = cap / "NGC6946" / name
    f.parent.mkdir(exist_ok=True)
    f.write_text("a rejected frame", encoding="utf-8")
    return f


def _unlink_refuses(monkeypatch, victim: Path) -> None:
    """``Path.unlink`` raising for ``victim`` alone, as a file held open by
    antivirus or a thumbnail reader does on Windows (WinError 32 is a
    ``PermissionError``)."""
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == victim.name:
            raise PermissionError(13, f"locked {_TEXT}", str(self))
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_a_rejected_frame_that_cannot_be_deleted_is_said_by_name(
        tmp_path, monkeypatch, bus_lines):
    """THE DEFECT: the method swallowed the OSError, so the frame stayed on disk
    unmentioned. Now one warning names the FILE (not its folder) and the
    exception's type (not its text), and the frame is still there."""
    cap = _capture_dir(tmp_path, monkeypatch)
    f = _rejected(cap)
    _unlink_refuses(monkeypatch, f)

    SequenceEngine._unlink_saved({"saved_path": str(f)})    # must not raise

    assert f.exists(), "premise: the delete was refused"
    said = _said(bus_lines, "could not be deleted", "sequence")
    assert len(said) == 1, ("a leftover frame must be said once", bus_lines)
    line = said[0]
    assert f.name in line, line
    assert "PermissionError" in line, line
    assert str(cap) not in line and "NGC6946/" not in line.replace("\\", "/"), (
        "the line carried the capture folder's path", line)
    assert _TEXT not in line and "locked" not in line, (
        "the line carried the exception's text, not its type", line)


def test_each_leftover_frame_is_said_not_only_the_first(
        tmp_path, monkeypatch, bus_lines):
    """One line per FILE: they are different frames the owner has to deal with,
    so a run-wide latch keyed on the exception type would hide the second."""
    cap = _capture_dir(tmp_path, monkeypatch)
    a, b = _rejected(cap, "a_0001.fits"), _rejected(cap, "b_0002.fits")
    real = Path.unlink

    def unlink(self, missing_ok=False):
        raise PermissionError(13, "locked", str(self))

    monkeypatch.setattr(Path, "unlink", unlink)
    SequenceEngine._unlink_saved({"saved_path": str(a)})
    SequenceEngine._unlink_saved({"saved_path": str(b)})
    monkeypatch.setattr(Path, "unlink", real)

    said = _said(bus_lines, "could not be deleted", "sequence")
    assert len(said) == 2, bus_lines
    assert "a_0001.fits" in said[0] and "b_0002.fits" in said[1], said


@pytest.mark.skipif(sys.platform != "win32",
                    reason="a file held open blocks its delete on Windows only")
def test_a_frame_held_open_by_another_reader_is_said(
        tmp_path, monkeypatch, bus_lines):
    """The real trigger rather than a stand-in: a handle another process holds
    open (an antivirus scan, a thumbnail reader) makes ``unlink`` raise."""
    cap = _capture_dir(tmp_path, monkeypatch)
    f = _rejected(cap)
    with open(f, "rb"):
        SequenceEngine._unlink_saved({"saved_path": str(f)})
        held = f.exists()
    assert held, "premise: an open handle blocks the delete on this platform"
    said = _said(bus_lines, "could not be deleted", "sequence")
    assert len(said) == 1 and f.name in said[0], bus_lines


async def test_a_discarded_frame_that_stays_on_disk_is_said_before_the_discard(
        sim_hub, tmp_path, monkeypatch, bus_lines):
    """Through the real caller: ``hfr_reject_action=discard`` unlinks the frame
    and then says it discarded one. When the unlink failed the log now says why
    the frame is still there, ahead of that line."""
    cap = _capture_dir(tmp_path, monkeypatch)
    f = _rejected(cap)
    _unlink_refuses(monkeypatch, f)
    engine = SequenceEngine(sim_hub)
    monkeypatch.setattr(engine, "_reject_action", lambda: "discard")
    target = Target(name="NGC 6946", ra_hours=20.34, dec_deg=60.15, steps=[])
    step = ExposureStep(filter="L", exposure_s=0.05, count=1)

    handled = await engine._handle_reject({"saved_path": str(f)}, "k", 0,
                                          target, step)

    assert handled is True
    assert f.exists()
    lines = [m for lvl, m, src in bus_lines if lvl == "warning"]
    left = [i for i, m in enumerate(lines) if "could not be deleted" in m]
    gone = [i for i, m in enumerate(lines) if "discarded a poor frame" in m]
    assert len(left) == 1 and len(gone) == 1, lines
    assert left[0] < gone[0], lines


def test_a_frame_that_deletes_cleanly_says_nothing(
        tmp_path, monkeypatch, bus_lines):
    """CONTROL: the line is for a failed delete, not for every discard."""
    cap = _capture_dir(tmp_path, monkeypatch)
    f = _rejected(cap)
    SequenceEngine._unlink_saved({"saved_path": str(f)})
    assert not f.exists()
    assert not _said(bus_lines, "could not be deleted", "sequence"), bus_lines


def test_a_frame_already_gone_says_nothing(tmp_path, monkeypatch, bus_lines):
    """CONTROL: nothing to delete is not a failure to delete."""
    cap = _capture_dir(tmp_path, monkeypatch)
    f = cap / "never_saved.fits"
    SequenceEngine._unlink_saved({"saved_path": str(f)})
    assert not _said(bus_lines, "could not be deleted", "sequence"), bus_lines


def test_a_path_outside_the_capture_folder_is_neither_deleted_nor_said(
        tmp_path, monkeypatch, bus_lines):
    """CONTROL: a NINA host's path is not ours to delete and not ours to
    report; the existing containment guard stays in front of the new line."""
    _capture_dir(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere.fits"
    outside.write_text("not ours", encoding="utf-8")
    _unlink_refuses(monkeypatch, outside)
    SequenceEngine._unlink_saved({"saved_path": str(outside)})
    assert outside.exists()
    assert not _said(bus_lines, "could not be deleted", "sequence"), bus_lines


def test_a_bus_that_cannot_take_the_leftover_line_does_not_raise(
        tmp_path, monkeypatch, caplog):
    """The method is documented 'never raises': a bus that is what failed falls
    back to the logger, as ``_say_swallowed`` does, instead of turning a
    bookkeeping line into a crash on the reject path."""
    import astrodeck.sequence.engine as engine_mod

    def broken(*a, **k):
        raise RuntimeError("the bus is down")

    cap = _capture_dir(tmp_path, monkeypatch)
    f = _rejected(cap)
    _unlink_refuses(monkeypatch, f)
    monkeypatch.setattr(engine_mod.bus, "log", broken)
    with caplog.at_level(logging.WARNING, logger=engine_mod.__name__):
        SequenceEngine._unlink_saved({"saved_path": str(f)})
    lines = [r.getMessage() for r in caplog.records
             if "could not be deleted" in r.getMessage()]
    assert len(lines) == 1 and f.name in lines[0], caplog.records


# ------------------------------------------------------------------------ #993
# the helper

def _bare_hub() -> hub_mod.Hub:
    """A hub with no ``__init__``: the helper reads nothing else, and a double
    built that way still works."""
    return hub_mod.Hub.__new__(hub_mod.Hub)


def test_a_swallowed_failure_is_said_once_per_type(bus_lines):
    h = _bare_hub()
    for exc in (RuntimeError("a"), RuntimeError("b"), KeyError("c"),
                KeyError("d")):
        h.say_swallowed("a step failed", exc)
    said = _said(bus_lines, "a step failed", "hub")
    assert said == ["a step failed (RuntimeError)", "a step failed (KeyError)"], (
        "the same type again is not news, a different type is", said)


def test_a_failure_still_there_tomorrow_is_in_tomorrows_log_too(
        monkeypatch, bus_lines):
    """The durable record is the night's own file (``captures/logs/<night>.jsonl``),
    so a latch that lasted the process would leave a fault that runs on past
    noon out of every night after the first."""
    import astrodeck.events as events
    h = _bare_hub()
    night = {"key": "2026-10-09"}
    monkeypatch.setattr(events, "night_key", lambda ts=None: night["key"])
    h.say_swallowed("a step failed", RuntimeError("x"))
    h.say_swallowed("a step failed", RuntimeError("x"))
    assert len(_said(bus_lines, "a step failed", "hub")) == 1, bus_lines
    night["key"] = "2026-10-10"
    h.say_swallowed("a step failed", RuntimeError("x"))
    h.say_swallowed("a step failed", RuntimeError("x"))
    assert len(_said(bus_lines, "a step failed", "hub")) == 2, bus_lines


def test_the_line_carries_the_type_and_never_the_text(bus_lines):
    h = _bare_hub()
    h.say_swallowed("a step failed", _boom())
    (line,) = _said(bus_lines, "a step failed", "hub")
    assert "RuntimeError" in line, line
    assert _TEXT not in line and "boom" not in line, line


def test_a_bus_that_cannot_take_the_line_falls_back_to_the_logger(
        monkeypatch, caplog):
    """Saying it can neither raise into the poll, capture or flip it reports on
    nor repeat itself: the key is stamped before the line is published."""
    def broken(*a, **k):
        raise RuntimeError("the bus is down")

    monkeypatch.setattr(hub_mod.bus, "log", broken)
    h = _bare_hub()
    with caplog.at_level(logging.WARNING, logger=hub_mod.__name__):
        h.say_swallowed("a step failed", ValueError("x"))
        h.say_swallowed("a step failed", ValueError("y"))
    lines = [r.getMessage() for r in caplog.records
             if "a step failed" in r.getMessage()]
    assert lines == ["a step failed (ValueError)"], lines


# ---------------------------------------------------------------- the hub sites
#
# Each case provokes ONE swallowed failure on a sim hub and returns. They take
# the hub and monkeypatch.

async def _raises(*a, **k):
    raise _boom()


def _raises_now(*a, **k):
    raise _boom()


class _Guider:
    """A connected guider whose calls raise what the case asks for."""

    connected = True

    async def is_active(self):
        return True

    async def stop_guiding(self):
        raise _boom()


async def _poll(hub):
    await hub.poll_status()


async def _status_loop(hub, mp):
    mp.setattr(hub, "poll_status", _raises)
    task = asyncio.create_task(hub._status_loop())
    await asyncio.sleep(0.05)                 # one pass, then it sleeps 2 s
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _disk(hub, mp):
    def disk_usage(path):
        raise PermissionError(13, f"no access {_TEXT}")
    mp.setattr(hub_mod.shutil, "disk_usage", disk_usage)
    await _poll(hub)


async def _sun_watch(hub, mp):
    mp.setattr(hub, "sun_watch", SimpleNamespace(state=_raises_now),
               raising=False)
    await _poll(hub)


async def _dew(hub, mp):
    mp.setattr(hub, "dew_controller", SimpleNamespace(snapshot=_raises_now),
               raising=False)
    await _poll(hub)


async def _mount(hub, mp):
    mp.setattr(hub.devices["telescope"], "get_position", _raises)
    await _poll(hub)


async def _meridian(hub, mp):
    mp.setattr(hub, "_compute_meridian", _raises)
    await _poll(hub)


async def _focuser(hub, mp):
    mp.setattr(hub.devices["focuser"], "get_position", _raises)
    await _poll(hub)


async def _filterwheel(hub, mp):
    mp.setattr(hub.devices["filterwheel"], "get_position", _raises)
    await _poll(hub)


async def _roof(hub, mp):
    mp.setattr(hub.devices["dome"], "shutter_state", _raises)
    await _poll(hub)


async def _rotator(hub, mp):
    mp.setattr(hub.devices["rotator"], "get_mechanical_position", _raises)
    await _poll(hub)


async def _camera(hub, mp):
    # Before the camera block is built: nothing of it reaches the frame.
    mp.setattr(hub, "_imaging_temperature", _raises)
    await _poll(hub)


async def _cooler_readout(hub, mp):
    # After it is built: the block is in the frame, the cooler is not.
    mp.setattr(hub.devices["camera"], "get_cooler", _raises)
    await _poll(hub)


async def _video_capabilities(hub, mp):
    import astrodeck.imaging.video as video
    mp.setattr(video, "camera_capabilities", _raises_now)
    await _poll(hub)


async def _fingerprint(hub, mp):
    import astrodeck.devices.fingerprint as fingerprint
    mp.setattr(fingerprint, "record_nowait", _raises_now)
    await _poll(hub)


async def _flip_guider(hub, mp):
    mp.setattr(hub, "guider", _Guider(), raising=False)

    async def goto_and_center(*a, **k):
        return {"position_unknown": True}       # the flip stops here, after the guider

    async def pier_side_now():
        return "east"

    mp.setattr(hub, "goto_and_center", goto_and_center)
    mp.setattr(hub, "pier_side_now", pier_side_now)
    await hub.meridian_flip(1.0, 2.0)


async def _guide_provider(hub, mp):
    mp.setattr(hub.guider, "is_active", _raises)
    await hub.select_guide_provider()


async def _header_mount_position(hub, mp):
    mp.setattr(hub.devices["telescope"], "get_position", _raises)
    await hub.capture(0.05, 100, 30, 1, save=False, target="")


_BLANK_FRAME = SimpleNamespace(hfr=None, stars=None, gain=-1,
                               egain_e_per_adu=None, timestamp=0.0)


async def _header_optics(hub, mp):
    mp.setattr(hub, "effective_optics", _raises_now)
    await hub._frame_meta(_BLANK_FRAME, None, None)


async def _header_cooler(hub, mp):
    mp.setattr(hub.devices["camera"], "get_cooler", _raises)
    await hub._frame_meta(_BLANK_FRAME, None, None)


async def _header_focuser(hub, mp):
    mp.setattr(hub.devices["focuser"], "get_position", _raises)
    await hub._frame_meta(_BLANK_FRAME, None, None)


async def _header_focuser_temperature(hub, mp):
    mp.setattr(hub.devices["focuser"], "get_temperature", _raises)
    await hub._frame_meta(_BLANK_FRAME, None, None)


async def _header_rotator(hub, mp):
    mp.setattr(hub.devices["rotator"], "get_mechanical_position", _raises)
    await hub._frame_meta(_BLANK_FRAME, None, None)


async def _egain_persist(hub, mp):
    import astrodeck.config as config_mod
    import astrodeck.imaging.egain as egain_module
    # The sim's frames have no shot noise to measure; the measurement is not
    # what this case grades.
    mp.setattr(egain_module, "measure_egain", lambda flats, biases: 3.25)
    mp.setattr(config_mod, "save_egain_config", _raises_now)
    await hub.learn_egain(gain=100, count=2, exposure_s=0.01)


async def _sync_note(hub, mp):
    from astrodeck.sync.runner import runner
    mp.setattr(runner, "note_saved", _raises_now)
    hub._note_frame_saved()


#: (id, case, the step as the line words it, the exception type it must name)
_SITES = [
    ("status_loop", _status_loop,
     "the status poll failed, so no status frame was published", "RuntimeError"),
    ("disk", _disk,
     "the status frame went without its capture-disk free space",
     "PermissionError"),
    ("sun_watch", _sun_watch,
     "the status frame went without its sun watch state", "RuntimeError"),
    ("dew", _dew,
     "the status frame went without its dew loop state", "RuntimeError"),
    ("mount", _mount,
     "the status frame went without its mount block", "RuntimeError"),
    ("meridian", _meridian,
     "the status frame went without its meridian block", "RuntimeError"),
    ("focuser", _focuser,
     "the status frame went without its focuser block", "RuntimeError"),
    ("filterwheel", _filterwheel,
     "the status frame went without its filter-wheel block", "RuntimeError"),
    ("roof", _roof,
     "the status frame went without its roof block", "RuntimeError"),
    ("rotator", _rotator,
     "the status frame went without its rotator block", "RuntimeError"),
    ("camera", _camera,
     "the status frame went without its camera readings", "RuntimeError"),
    ("cooler_readout", _cooler_readout,
     "the status frame went without its cooler readout", "RuntimeError"),
    ("video_capabilities", _video_capabilities,
     "the camera's video capabilities could not be read, so video is offered "
     "as unavailable", "RuntimeError"),
    ("fingerprint", _fingerprint,
     "the device fingerprint could not be recorded", "RuntimeError"),
    ("flip_guider", _flip_guider,
     "the guider would not stop for the meridian flip", "RuntimeError"),
    ("guide_provider", _guide_provider,
     "the current guider would not say whether it is guiding, so it was "
     "treated as idle", "RuntimeError"),
    ("header_mount_position", _header_mount_position,
     "the mount's position could not be read for a frame's header",
     "RuntimeError"),
    ("header_optics", _header_optics,
     "the frame header went without the optics cards", "RuntimeError"),
    ("header_cooler", _header_cooler,
     "the frame header went without the cooler set point", "RuntimeError"),
    ("header_focuser", _header_focuser,
     "the frame header went without the focuser position and temperature",
     "RuntimeError"),
    ("header_focuser_temperature", _header_focuser_temperature,
     "the frame header went without the focuser temperature", "RuntimeError"),
    ("header_rotator", _header_rotator,
     "the frame header went without the rotator angle", "RuntimeError"),
    ("egain_persist", _egain_persist,
     "the measured e-/ADU could not be saved, so it is lost at the next "
     "restart", "RuntimeError"),
    ("sync_note", _sync_note,
     "the file-sync push was not told a frame was saved", "RuntimeError"),
]


@pytest.mark.parametrize("case,what,kind",
                         [pytest.param(c, w, k, id=i) for i, c, w, k in _SITES])
async def test_a_swallowed_failure_is_said_once_and_the_call_goes_on(
        case, what, kind, sim_hub, monkeypatch, bus_lines):
    """Each guarded step, made to raise: the call returns instead of raising
    (the guard still guards), the failure is said ONCE however many times it
    happens, as a warning that is exactly the step and the exception's type and
    carries none of its text."""
    await case(sim_hub, monkeypatch)        # raising here is the bug
    await case(sim_hub, monkeypatch)
    await case(sim_hub, monkeypatch)
    said = _said(bus_lines, what, "hub")
    assert said == [f"{what} ({kind})"], (
        "expected one warning, exactly the step and its type; the log held",
        bus_lines)


def _flag_spy(monkeypatch) -> list[tuple[str, str, str, bool]]:
    """Every ``bus.log`` line as ``(level, message, source, site_derived)``: the
    shared ``bus_lines`` spy drops the flag, and the flag is what is graded."""
    seen: list[tuple[str, str, str, bool]] = []
    monkeypatch.setattr(
        hub_mod.bus, "log",
        lambda level, message, source="hub", **kw: seen.append(
            (level, message, source, bool(kw.get("site_derived")))))
    return seen


def test_a_swallowed_failure_is_flagged_site_derived_only_when_asked(
        monkeypatch):
    seen = _flag_spy(monkeypatch)
    h = _bare_hub()
    h.say_swallowed("a flagged step failed", RuntimeError("x"),
                    site_derived=True)
    h.say_swallowed("an ordinary step failed", RuntimeError("x"))
    assert seen == [
        ("warning", "a flagged step failed (RuntimeError)", "hub", True),
        ("warning", "an ordinary step failed (RuntimeError)", "hub", False),
    ], seen


async def test_the_guider_line_at_a_meridian_flip_is_flagged_site_derived(
        sim_hub, monkeypatch):
    """The flip is due at the target's computed transit, a function of the
    site's longitude (#166, #302). The hub's own "meridian flip: stopping
    guiding" line is flagged for that reason; the warning that the guider
    would not stop is worded at the same moment, so it carries the same flag,
    or a viewer reads the flip time off the log routes that withhold the
    first."""
    seen = _flag_spy(monkeypatch)
    # CONTROL, first (the flip case swaps in a bare guider a poll cannot
    # describe): a swallowed failure that is not timed by the sky stays
    # unflagged, so the spy is reading the keyword and not flagging everything.
    await _mount(sim_hub, monkeypatch)
    plain = [s for s in seen if "its mount block" in s[1]]
    assert plain and not any(s[3] for s in plain), seen
    await _flip_guider(sim_hub, monkeypatch)
    info = [s for s in seen if s[1].startswith("meridian flip: stopping guiding")]
    warn = [s for s in seen if s[1].startswith("the guider would not stop")]
    assert info and all(s[3] for s in info), (
        "premise: the info line is flagged", seen)
    assert warn and all(s[3] for s in warn), (
        "the guider warning at the flip moment was not flagged site_derived",
        seen)


async def test_a_cooler_that_raises_costs_the_frame_only_the_cooler(
        sim_hub, monkeypatch, bus_lines):
    """The line names what was LOST. The cooler is read after the camera block
    is built, so when only it raises the frame still has the camera's
    temperature, and a line saying the camera readings went would overstate it."""
    await _cooler_readout(sim_hub, monkeypatch)
    status = await sim_hub.poll_status()
    camera = status["camera"]
    assert "temperature" in camera and "cooler" not in camera, camera
    assert not _said(bus_lines, "its camera readings", "hub"), bus_lines
    assert _said(bus_lines, "its cooler readout", "hub"), bus_lines


async def test_a_camera_block_that_cannot_be_built_costs_the_frame_the_camera(
        sim_hub, monkeypatch, bus_lines):
    """The other half: a failure before the block is built does take the camera
    off the frame, and that is the case the 'camera readings' line is for."""
    await _camera(sim_hub, monkeypatch)
    status = await sim_hub.poll_status()
    assert "camera" not in status, status.get("camera")
    assert _said(bus_lines, "its camera readings", "hub"), bus_lines


async def test_a_thermometer_that_raises_leaves_the_focuser_position_in_the_header(
        sim_hub, monkeypatch, bus_lines):
    """The header's position is set before the thermometer is asked, so a
    thermometer that raises leaves the position in the file and the line says
    only the temperature went. A position that raises leaves neither, and says
    both (the control)."""
    foc = sim_hub.devices["focuser"]
    await foc.move_to(19200)
    monkeypatch.setattr(foc, "get_temperature", _raises)
    meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
    assert meta.focuser_pos == 19200 and meta.focuser_temp_c is None, meta
    assert _said(bus_lines, "went without the focuser temperature", "hub")
    assert not _said(bus_lines, "focuser position", "hub"), bus_lines

    monkeypatch.setattr(foc, "get_position", _raises)
    meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
    assert meta.focuser_pos is None and meta.focuser_temp_c is None, meta
    assert _said(bus_lines, "went without the focuser position and temperature",
                 "hub"), bus_lines


async def test_a_healthy_poll_says_none_of_it(sim_hub, bus_lines):
    """CONTROL: on a rig where every read works the new lines never appear, so a
    warning from one of them is a fault and not the sim's background noise."""
    await sim_hub.poll_status()
    await sim_hub.poll_status()
    new = [m for lvl, m, src in bus_lines
           if lvl == "warning" and src == "hub"
           and ("the status frame went without" in m
                or "the device fingerprint" in m
                or "video capabilities" in m)]
    assert not new, new


async def test_a_stalled_read_is_not_said_a_second_time(
        sim_hub, monkeypatch, bus_lines):
    """A read that does not return is already said by ``_status_read`` (once,
    with the bound it missed) and raises ``StatusReadStalled`` into the block's
    guard. That is the same absence, not a new failure: the guard must not turn
    it into a second line about the same stall."""
    monkeypatch.setattr(hub_mod, "STATUS_DEVICE_READ_TIMEOUT_S", 0.05)
    tel = sim_hub.devices["telescope"]

    async def hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(tel, "get_position", hang)
    try:
        await sim_hub.poll_status()
        await sim_hub.poll_status()
    finally:
        for _dev, probe in list(getattr(sim_hub, "_status_reads", {}).values()):
            probe.cancel()
        await asyncio.sleep(0)
    stall = [m for lvl, m, src in bus_lines
             if lvl == "warning" and "has not returned" in m]
    assert len(stall) == 1, ("premise: the stall is said once", bus_lines)
    assert not _said(bus_lines, "the status frame went without", "hub"), bus_lines


# ----------------------------------------------------------------- the app sites

def _hub_lines(bus_lines, what: str) -> list[str]:
    return _said(bus_lines, what, "hub")


class _BrokenSunWatch:
    def note_parked(self):
        raise _boom()


def test_a_sun_watch_that_cannot_be_told_of_a_park_is_said_once(
        monkeypatch, bus_lines):
    """The concrete twin of the engine's two sites (#696): the operator's PARK
    and the roof close's park call this, and a raise left the sun watch's blind
    fallback stale with no line saying so."""
    monkeypatch.setattr(app_module.hub, "sun_watch", _BrokenSunWatch(),
                        raising=False)
    for _ in range(3):
        app_module._tell_sun_watch_the_mount_parked()     # must not raise
    what = "the sun watch could not be told the mount parked"
    assert _hub_lines(bus_lines, what) == [f"{what} (RuntimeError)"], bus_lines


def _lifespan_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    return TestClient(app_module.create_app())


def test_a_stop_the_camera_would_not_obey_is_said(
        tmp_path, monkeypatch, bus_lines):
    """The operator pressed STOP and the exposure may still be running. The
    route answers as it always did; the log now says the abort was refused."""
    with _lifespan_client(tmp_path, monkeypatch) as client:
        try:
            assert client.post("/api/connect/sim").status_code == 200
            cam = app_module.hub.devices["camera"]
            monkeypatch.setattr(cam, "abort_exposure", _raises)
            for _ in range(2):
                r = client.post("/api/capture/stop")
                assert r.status_code == 200 and r.json() == {"looping": False}
        finally:
            client.post("/api/disconnect")
    what = "the camera would not abort its exposure when capture was stopped"
    assert _hub_lines(bus_lines, what) == [f"{what} (RuntimeError)"], bus_lines


def test_a_rig_that_will_not_disconnect_at_shutdown_is_said(
        tmp_path, monkeypatch, bus_lines):
    """Shutdown still completes (it never raises out of the lifespan), and the
    line is written before the night-log flush that follows it."""
    with _lifespan_client(tmp_path, monkeypatch):
        monkeypatch.setattr(app_module.hub, "disconnect_all", _raises)
    what = "the rig did not disconnect cleanly at shutdown"
    assert _hub_lines(bus_lines, what) == [f"{what} (RuntimeError)"], bus_lines


# ---------------------------------------------------------------- the ratchets

#: Every handler whose whole body is ``pass``, by the function it sits in:
#: (function, exception type as written) -> (how many, why it stays silent). A
#: new one fails the ratchet below until it is said (``Hub.say_swallowed``) or
#: listed here with a reason. Not a behaviour test: it grades a property of the
#: whole file that no single case can, which is that the next best-effort guard
#: does not go back to a bare ``pass``.
_HUB_SILENT = {
    ("_connect_alpaca_device_unlocked", "Exception"): (
        2, "the device or session being replaced is dropped either way"),
    ("connect_phd2", "Exception"): (
        1, "the guider being replaced is dropped either way"),
    ("select_guide_provider", "Exception"): (
        1, "the guider being replaced is dropped either way"),
    ("_teardown", "Exception"): (
        1, "cooler_owed stays set when cancel_warm raises, so the cleanup "
           "switches the cooler off"),
    ("_nina_heartbeat", "Exception"): (
        1, "a failed ping is the signal: last_ok goes stale and the status "
           "frame publishes its age"),
    ("_frame_meta", "Exception"): (
        2, "formatting two floats and an airmass from numbers in hand: a card "
           "is left off"),
    ("_seed_filter_config", "(TypeError, ValueError)"): (
        1, "narrowed to the conversion: the slot keeps its offset"),
    ("set_filter_names", "(TypeError, ValueError)"): (
        1, "narrowed to the conversion: the slot keeps its offset"),
    ("_enqueue_thumb", "Exception"): (
        1, "thumbnail warming is an optimisation; the lazy route covers it"),
    ("_thumb_worker", "Exception"): (
        1, "thumbnail warming is an optimisation; the lazy route covers it"),
    ("_note_solve_exposure", "Exception"): (
        1, "a bookkeeping record that must not cost a solve its exposure"),
    ("_retire_solve_frame", "Exception"): (
        1, "housekeeping of a diagnostic copy after the solve has its answer"),
    ("_run_to_its_bound", "Exception"): (
        1, "the step's own end is read from the step below"),
    ("_run_to_its_bound", "asyncio.CancelledError"): (
        1, "control flow: the loop re-reads the step below"),
    ("poll_status", "Exception"): (
        8, "readings whose key is documented absent when the backend cannot "
           "say (moving x2, dew heater, fan), the derived focus displays "
           "(temp_comp, sweep), the guide camera's probe and its describe()"),
}

_APP_SILENT = {
    ("_lifespan", "Exception"): (
        4, "shutdown steps with nothing after them to tell: the relay "
           "dial-out and the update poller die with the process, the COM "
           "host's stop() swallows a failed terminate itself and a child "
           "left behind is reaped through its pidfile at the next spawn, "
           "and the night-log writer is what failed if its flush does"),
    ("_materialize_bundle", "OSError"): (
        2, "control flow: a failed hardlink or comparison falls to the copy, "
           "which reports its own failure"),
    ("_normalise_allowed_host", "ValueError"): (
        1, "not an IP literal: validated as a DNS name next, which raises"),
    ("ws", "(WebSocketDisconnect, RuntimeError)"): (
        1, "the client went away: the normal end of a socket"),
}


def _silent_handlers(tree: ast.AST) -> dict[tuple[str, str], int]:
    """``{(function, type text): count}`` for each ``except`` whose body is
    only ``pass``."""
    out: dict[tuple[str, str], int] = {}

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            f = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func)
            if (isinstance(child, ast.ExceptHandler) and len(child.body) == 1
                    and isinstance(child.body[0], ast.Pass)):
                key = (func, ast.unparse(child.type) if child.type else "bare")
                out[key] = out.get(key, 0) + 1
            walk(child, f)

    walk(tree, "<module>")
    return out


@pytest.mark.parametrize("module,allowed", [
    pytest.param(hub_mod, _HUB_SILENT, id="hub"),
    pytest.param(app_module, _APP_SILENT, id="app"),
])
def test_no_unexplained_silent_pass_handlers(module, allowed):
    src = Path(module.__file__).read_text(encoding="utf-8")
    found = _silent_handlers(ast.parse(src))
    listed = {k: n for k, (n, _why) in allowed.items()}
    stray = {k: n - listed.get(k, 0) for k, n in found.items()
             if n > listed.get(k, 0)}
    assert not stray, (
        f"an except handler whose whole body is `pass` in "
        f"{Path(module.__file__).name}: say what it caught with "
        f"hub.say_swallowed(...) or list it with a reason in the table "
        f"above: {stray}")
    gone = {k: n for k, n in listed.items() if found.get(k, 0) < n}
    assert not gone, (
        f"listed as silent but no longer a bare pass; drop or lower the "
        f"entry: {gone}")
