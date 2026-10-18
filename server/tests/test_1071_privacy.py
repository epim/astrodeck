# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1071: nothing the new instrumentation writes carries an absolute mount
coordinate or a value derived from the site.

The risk is specific. An AM5 at home or park reports an RA that IS the local
sidereal time (a longitude oracle, #140) and a Dec that IS the pole's, and a
key-name redaction filter cannot withhold a value that code computes and names
itself (#19). So the probes work in DIFFERENCES, and this file holds that to
the repo's own rule (``_site_tracking``, the #19 scanner's method): run the
same event under two synthetic "sites" (two absolute RA readings, two
declinations), and a number that moves when only the site moves is a leak.

Three outputs per event are read: the night-log lines, the route response,
and the file written under ``captures/guide``. Every coordinate here is
synthetic. The comparison holds everything else still, as its docstring
asks: the measured elapsed seconds, the wall-clock stamp in a file name and
the monotonic timestamps are normalised out of BOTH sides (they are the
test machine's clock, not the site), and the known positive at the bottom
shows the comparison does find a number that tracks.

Each test names the mutant it was shown RED under, from a byte backup of the
production file restored afterwards.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.devices.backends.zwo_am5 as am5
import astrodeck.hub as hub_module
import astrodeck.pulse_probe as pp
from astrodeck.devices import lx200
from astrodeck.events import bus, night_key
from astrodeck.guide.flight_recorder import FlightRecorder
from astrodeck.sequence.session import session_store
from astrodeck.tracking_probe import probe_line, run_probe

from _oct08_guider_harness import (CAP_MS, Mount, ScriptedEngine, arm_loop,
                                   cal_dict, make_guider, profile_id, pulse,
                                   stop_loop)
from _site_tracking import any_tracking, tracking_tokens
from test_1071_probe_ra_drift import ClockedLink, RaLink, vclock  # noqa: F401
from test_zwo_am5 import fixed_env  # noqa: F401

#: Two synthetic RA readings (seconds of time), 03:12:44 and 17:48:09.
RA_A, RA_B = 3 * 3600 + 12 * 60 + 44, 17 * 3600 + 48 * 60 + 9
#: Two synthetic declinations, degrees.
DEC_A, DEC_B = 41.2345678, 12.3456789


def _forms(ra_s: float) -> list[str]:
    """Ways an RA reading could be printed: HH:MM:SS, hours and degrees at 3
    and 4 decimals (two decimals collide with elapsed times and rates), and
    the raw seconds."""
    h, rem = divmod(int(ra_s), 3600)
    m, s = divmod(rem, 60)
    out = [f"{h:02d}:{m:02d}:{s:02d}", f"{h}h{m:02d}m", str(int(ra_s))]
    for p in (3, 4):
        out += [f"{ra_s / 3600.0:.{p}f}", f"{ra_s / 240.0:.{p}f}"]
    return out


def _dec_forms(dec: float) -> list[str]:
    import math
    out = [f"{dec:.7f}", f"{dec:.4f}", f"{dec:.3f}",
           f"{math.radians(dec):.4f}", f"{math.radians(dec):.3f}"]
    return out


_TIMING = re.compile(r"over [\d.]+ s \(resolution [\d.]+ arcsec/s\)")
_MEAN = re.compile(r"mean cycle [\d.]+ s")
_STAMP = re.compile(r"\d{6}-")


def _norm_line(m: str) -> str:
    m = _TIMING.sub("over S s (resolution R arcsec/s)", m)
    m = _MEAN.sub("mean cycle C s", m)
    m = re.sub(r"\d{4}-\d\d-\d\d", "NIGHT", m)
    return _STAMP.sub("STAMP-", m)


def _norm_file(text: str, drop: tuple[str, ...]) -> str:
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k not in drop}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o
    return json.dumps(clean(json.loads(text)), sort_keys=True)


#: Keys whose value is the test machine's clock, not the site: the wall
#: stamp, the measured intervals and the quanta over them, the sample count.
_CLOCK_KEYS = ("started_at", "seconds", "resolution_arcsec_s",
               "dec_resolution_arcsec_s", "samples", "mono_at_write")


def _find(forms: list[str], text: str) -> list[str]:
    return [f for f in forms if f in text]


# -------------------------------------------------- the pulse probe route


def _run_route(tmp_path, monkeypatch, ra_s: float, rate: float,
               dec: float = DEC_A):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(pp, "PULSE_MS", 60)
    monkeypatch.setattr(pp, "TRAIN_PULSES", 2)
    monkeypatch.setattr(pp, "CADENCE_S", 0.1)
    monkeypatch.setattr(pp, "PROBE_SECONDS", 0.3)
    monkeypatch.setattr(am5, "PROBE_MIN_S", 0.1)
    monkeypatch.setattr(session_store, "armed", lambda: None)
    link = RaLink(float(ra_s), rate)
    link.script["GD"] = lx200.format_dec(dec)
    tel = am5.ZwoAm5Telescope(link)
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "guider", None, raising=False)
    lines: list[str] = []
    real = bus.log
    monkeypatch.setattr(
        bus, "log", lambda level, message, source="hub", **kw: lines.append(
            message))
    app = app_module.create_app()
    with TestClient(app) as client:
        r = client.post("/api/mount/pulse-probe")
        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 30
        folder = tmp_path / "guide" / night_key()
        while time.monotonic() < deadline:
            t = app_module.hub._busy.get("pulse_probe")
            if t is not None and t.done() and list(folder.glob("*.json")):
                break
            time.sleep(0.05)
        files = sorted(folder.glob("*-pulse-probe*.json"))
        assert files, lines
        body = r.text
        file_text = files[0].read_text(encoding="utf-8")
    monkeypatch.setattr(bus, "log", real)
    return body, lines, file_text


def test_the_pulse_probe_outputs_do_not_move_with_the_absolute_ra(
        tmp_path, monkeypatch):
    """The same stopped-drive run from RA 03:12:44 and from RA 17:48:09: the
    response, the log lines and the file hold the same numbers. A stopped
    drive reads about 1 sidereal from either, so the differences are the
    only thing that can show.

    MUTANT 1 (the file's steps carry ``"ra_hours": ra1``): RED, tokens that
    track the reading.
    MUTANT 2 (``probe_line`` appends the reading): RED."""
    out = {}
    for tag, ra, dec in (("a", RA_A, DEC_A), ("b", RA_B, DEC_B)):
        sub = tmp_path / tag
        sub.mkdir()
        with monkeypatch.context() as mp:
            body, lines, file_text = _run_route(sub, mp, ra, 0.0, dec)
        out[tag] = (body, lines, file_text)
        text = body + "\n".join(lines) + file_text
        assert not _find(_forms(ra), text), (tag, _find(_forms(ra), text))
        assert not _find(_dec_forms(dec), text), (
            tag, _find(_dec_forms(dec), text))
    texts = {k: [_norm_line(m) for m in v[1]] + [v[0]] + [_norm_file(
        v[2], _CLOCK_KEYS)] for k, v in out.items()}
    found = tracking_tokens(texts["a"], texts["b"])
    assert not any_tracking(found), found


def test_a_stopped_drive_run_does_not_print_the_reading_either(tmp_path,
                                                                monkeypatch):
    """The same two readings with the sidereal rate (the drive is stopped):
    the rate shows, the reading does not. (No token comparison here: the
    quantisation phase of a moving RA is the clock's, not the site's.)

    MUTANT 1 as above."""
    for ra, dec in ((RA_A, DEC_A), (RA_B, DEC_B)):
        sub = tmp_path / str(ra)
        sub.mkdir()
        with monkeypatch.context() as mp:
            body, lines, file_text = _run_route(sub, mp, ra, 1.0027379, dec)
        text = body + "\n".join(lines) + file_text
        assert not _find(_forms(ra), text), _find(_forms(ra), text)
        assert not _find(_dec_forms(dec), text), _find(_dec_forms(dec), text)
        assert any("sidereal" in m for m in lines)


def test_the_response_carries_no_number_at_all(tmp_path, monkeypatch):
    """``{"started": "pulse_probe", "note": ...}``: no digits to leak.

    MUTANT 3 (the note quoting the mount's position): RED."""
    body, _lines, _file = _run_route(tmp_path, monkeypatch, RA_A, 0.0)
    assert not re.search(r"\d", body), body


def test_the_probe_line_prints_only_its_whitelisted_fields():
    """A result that grows an RA field does not get it printed: the line is
    built from named keys.

    MUTANT 4 (``probe_line`` interpolating ``{r}``): RED."""
    r = {"ra_drift_arcsec_s": 0.1, "sidereal_fraction": 0.01,
         "tracking_flag_before": True, "tracking_flag_after": True,
         "seconds": 10.0, "pier_side": "east", "resolution_arcsec_s": 1.5,
         "dec_drift_arcsec_s": -0.2, "disturbed": True,
         "disturbed_by": ["sync"],
         "ra_before_hours": 3.2122, "ra_after": "03:12:44", "dec": 41.2345678,
         "dec_before_deg": 41.2345678}
    line = probe_line("t", r, 5.0)
    for needle in ("3.2122", "03:12:44", "41.23", "ra_before", "dec_before"):
        assert needle not in line, line
    assert "Dec drift -0.20 arcsec/s" in line and "disturbed by sync" in line


async def test_a_line_with_the_minutes_since_a_flip_is_flagged_site_derived():
    """The flip happens at the target's computed crossing, so the minutes
    since it, with the line's own timestamp, solve for the longitude (the
    flag's own definition, #1065). A line without the figure is not flagged.

    MUTANT 5 (``site_derived`` never passed): RED."""
    seen = []
    real = bus.log
    bus.log = lambda level, message, source="hub", **kw: seen.append(
        (message, kw))
    try:
        tel = SimpleNamespace(
            can_probe_ra_drift=True,
            probe_ra_drift=lambda s: _async({
                "ra_drift_arcsec_s": 0.0, "sidereal_fraction": 0.0,
                "tracking_flag_before": True, "tracking_flag_after": True,
                "seconds": 10.0, "pier_side": "east",
                "resolution_arcsec_s": 1.5}))
        await run_probe(tel, "with", since_flip_min=12.0)
        await run_probe(tel, "without")
    finally:
        bus.log = real
    assert seen[0][1] == {"site_derived": True}
    assert seen[1][1] == {}


async def _async(v):
    return v


# ------------------------------------------------------ the flight recorder


async def _runaway_outputs(tmp_path, monkeypatch, dec_deg: float):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    lines: list[str] = []
    monkeypatch.setattr(
        bus, "log", lambda level, message, source="hub", **kw: lines.append(
            message))
    import math
    mount = Mount(dec_deg=dec_deg, pier="east")
    g = make_guider(profile=profile_id("priv"), mount=mount)
    eng = ScriptedEngine(
        [(pulse("east", CAP_MS), (3.0 + 2.0 * k, 0.0)) for k in range(8)],
        cal=cal_dict(dec_rad=math.radians(dec_deg)))
    task = arm_loop(g, eng, scope_dec_deg=dec_deg)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    finally:
        if not task.done():
            await stop_loop(g)
    await g._fr.flush()
    folder = tmp_path / "guide" / night_key()
    files = sorted(folder.glob("*.json"))
    assert files
    return lines, [f.read_text(encoding="utf-8") for f in files]


async def test_the_flight_recorder_does_not_move_with_the_declination(
        tmp_path, monkeypatch):
    """The same runaway at declination +41.2 and at +12.3 (the calibration
    walked at each): the files and the lines hold the same numbers, and no
    rendering of either declination appears.

    MUTANT 6 (``_CAL_KEYS`` gains ``"declination"``): RED, the declination
    tracks.
    MUTANT 7 (``begin_cycle`` records ``self._scope_dec_rad``): RED."""
    res = {}
    for tag, dec in (("a", DEC_A), ("b", DEC_B)):
        sub = tmp_path / tag
        sub.mkdir()
        with monkeypatch.context() as mp:
            res[tag] = await _runaway_outputs(sub, mp, dec)
        blob = "\n".join(res[tag][0]) + "".join(res[tag][1])
        assert not _find(_dec_forms(dec), blob), _find(_dec_forms(dec), blob)
        assert "declination" not in blob
    drop = ("t", "dt", "written_at", "mono_at_write")
    texts = {k: [_norm_line(m) for m in v[0] if "flight recorder" in m]
             + [_norm_file(f, drop) for f in v[1]] for k, v in res.items()}
    assert texts["a"] and texts["b"]
    found = tracking_tokens(texts["a"], texts["b"])
    assert not any_tracking(found), found


def test_a_calibration_is_copied_through_a_whitelist(tmp_path, monkeypatch):
    """The calibration dict carries the declination and pier side it was
    walked at; the file gets rates and angles only.

    MUTANT 6 as above."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    r = FlightRecorder()
    r.begin_cycle(measured=False)
    r.dump("cal", calibration=cal_dict(dec_rad=0.7197, pier="west"))
    f = next((tmp_path / "guide" / night_key()).glob("*.json"))
    cal = json.loads(f.read_text(encoding="utf-8"))["calibration"]
    assert set(cal) <= {"x_rate", "y_rate", "x_angle", "y_angle",
                        "y_angle_error", "is_valid"}
    assert "0.7197" not in f.read_text(encoding="utf-8")


# ------------------------------------------------- the scan can see a leak


def test_known_positive_the_comparison_finds_a_number_that_tracks():
    """A scan that finds nothing may be a scan that cannot see. A line that
    DID print the reading differs between the two, and is found; a clock
    reading that is the same on both sides is not.

    (No mutant: this grades the comparison itself.)"""
    a = ["tracking probe: RA 3.2122 h, drift +0.00 at 12:00"]
    b = ["tracking probe: RA 17.8025 h, drift +0.00 at 12:00"]
    found = tracking_tokens(a, b)
    assert any_tracking(found)
    assert "3.2122" in found["a"] and "17.8025" in found["b"]
    assert not any_tracking(tracking_tokens(["drift +0.00 at 12:00"],
                                            ["drift +0.00 at 12:00"]))


# ------------------------------------- round 2: the steps, the Dec, the samples


async def _protocol_outputs(tmp_path, monkeypatch, ra_s: float, dec: float):
    """The whole protocol (every stage, Dec included, the model steps and the
    sampled probe) over a link whose position the pulses really move, from the
    given absolute RA and Dec. Returns the log lines, the result dict as JSON
    and the file text."""
    from test_1071_model_step import PulseModelLink
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(am5, "PROBE_MIN_S", 0.1)
    monkeypatch.setattr(pp, "PULSE_MS", 120)
    monkeypatch.setattr(pp, "TRAIN_PULSES", 2)
    monkeypatch.setattr(pp, "CADENCE_S", 0.2)
    monkeypatch.setattr(pp, "PROBE_SECONDS", 0.3)
    lines: list[str] = []
    monkeypatch.setattr(
        bus, "log", lambda level, message, source="hub", **kw: lines.append(
            message))
    link = PulseModelLink(ra_s=float(ra_s), dec=dec)
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    result = await pp.run_protocol(tel)
    files = sorted((tmp_path / "guide" / night_key()).glob("*-pulse-probe*"))
    assert files and result["completed"] is True
    return lines, json.dumps(result), files[0].read_text(encoding="utf-8")


async def test_the_steps_and_the_dec_stages_do_not_print_either_coordinate(
        tmp_path, monkeypatch, fixed_env):
    """The model steps are differences, the probe is sampled, and Dec is read:
    from two absolute RAs and two declinations, no rendering of either
    appears in any line, the result or the file, while the steps are real
    (RA and Dec both moved).

    MUTANT 8 (``measure_model_step`` returning ``"ra_after_hours": ra1``):
    RED.
    MUTANT 9 (``probe_ra_drift`` returning ``"dec_deg": dec0``): RED."""
    for tag, ra, dec in (("a", RA_A, DEC_A), ("b", RA_B, DEC_B)):
        sub = tmp_path / tag
        sub.mkdir()
        with monkeypatch.context() as mp:
            lines, result, file_text = await _protocol_outputs(sub, mp, ra,
                                                               dec)
        text = "\n".join(lines) + result + file_text
        assert not _find(_forms(ra), text), (tag, _find(_forms(ra), text))
        assert not _find(_dec_forms(dec), text), (tag,
                                                  _find(_dec_forms(dec), text))
        data = json.loads(file_text)
        moved = [s["step"] for s in data["steps"] if s["step"]]
        assert any(abs(s["ra_step_arcsec"]) > 1.0 for s in moved)
        assert any(abs(s["dec_step_arcsec"]) > 1.0 for s in moved)
        assert len(moved) == 8


async def test_every_new_key_is_a_difference_a_rate_a_flag_or_a_count(
        vclock, fixed_env):
    """The probe dict, key by key: nothing in it is a position. A new key has
    to be added here on purpose.

    MUTANT 10 (any ``*_hours`` / ``*_deg`` key added to the result): RED."""
    probe_keys = {
        "ra_drift_arcsec_s", "ra_drift_endpoint_arcsec_s",
        "sidereal_fraction", "samples", "tracking_flag_before",
        "tracking_flag_after", "seconds", "pier_side", "resolution_arcsec_s",
        "dec_drift_arcsec_s", "dec_resolution_arcsec_s", "disturbed",
        "disturbed_by", "cut_short"}
    link = ClockedLink(vclock, RA_A, 1.0027379, dec_start=DEC_A, dec_rate=3.0)
    tel = am5.ZwoAm5Telescope(link)
    await tel.connect()
    link.arm()
    r = await tel.probe_ra_drift(2.0)
    assert set(r) == probe_keys, set(r) ^ probe_keys
    assert not any(k.endswith(("_hours", "_deg", "_rad")) for k in r)
