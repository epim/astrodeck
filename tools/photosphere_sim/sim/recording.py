# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A phone's recording of a scan, as a case, and a replay checked against it.

``import-recording`` turns the file ``Recorder.finish`` wrote (SPEC-v2 13.9)
into a case directory the replay can run: the same ``input/`` a simulated case
has (13.1), with the frames decoded to PNG, and no ``truth/``, because nobody
knows the truth of a real garden. ``selfcheck`` then compares what the replay
made of it with what the phone itself reported, which is the one test the
simulator cannot cheat: the report was computed by the scanner on the device,
from the same frames and the same sensor events, so a replay that agrees with
it is the scanner running the same way off the phone (owner check D7, SPEC-v2 7.8).

A recording holds photos of the surroundings. The case directory is as private
as the file, so it belongs under the git-ignored ``cache/`` and is never
committed without the owner's say (O4). Nothing here prints a value from a
recording beyond counts, and the report it embeds carries no coordinates by
construction (13.10).

Times are kept exactly as recorded, in ms since the camera opened. A rebase to
"zero is the first frame" would move the replay's ``begin_ms`` and ``finish_ms``
off the report's ``timeline``, and the selfcheck would have to undo it.

``selfcheck`` reads three things: ``result/diagnostics.json`` (the replay),
``recording/report.json`` (the phone) and ``input/observations.jsonl`` (what the
replay was fed). The last is not decoration. ``Diagnostics`` carries no event
counts, so the event rates are measured on the observations the import wrote;
that is what makes a motion event dropped on import visible, since the report
still counts the ``devicemotion`` events the phone received. It also means
those rows check the import against the phone's counts and say nothing about
the replay, which the printed table notes under it.

The predictor mode is two rows, each compared with its twin in the report:
``predictor_mode`` with ``sensors.modeAtBegin`` and ``mode_changes`` with
``sensors.modeChanges`` (their ``from`` and ``to``, not their times).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import platform
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import PIL
from PIL import Image

from .cases import (_delivery_ms, _hash_concatenated_digests, _sha256_hex,
                    _write_json, _write_jsonl)

__all__ = [
    "Check", "RecordingError", "EVENT_RATE_NOTE", "EVENT_TYPES", "FOCAL_REL_TOL", "LOOP_DEG_TOL",
    "KEYFRAME_REL_TOL", "RATE_REL_TOL", "format_table", "import_recording", "selfcheck",
]

RECORDING_FORMAT = "astrodeck-pano-recording"

#: The selfcheck's limits, SPEC-v2 T23: the focal length within 0.1 %, the
#: closure residual within 0.1 degrees, the keyframe count within 2 % and each
#: event rate within 5 %.
FOCAL_REL_TOL = 0.001
LOOP_DEG_TOL = 0.1
KEYFRAME_REL_TOL = 0.02
RATE_REL_TOL = 0.05

#: The three streams ``SensorFacts.events`` counts, in its order.
EVENT_TYPES = ("deviceorientation", "deviceorientationabsolute", "devicemotion")

_ORIENTATION_EVENTS = ("deviceorientation", "deviceorientationabsolute")

#: A case id becomes a directory name, so it may not carry a separator, a
#: leading dot or anything the file system would read as a path.
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: A frame id becomes a file name (``frames/<id>.png``) under ``input/``. The
#: replay refuses a path that leaves ``input/``; this refuses it earlier, at
#: the one place a recording's own text is turned into a path.
_FRAME_ID = re.compile(r"[A-Za-z0-9_-]+")

_FRAME_NUMBER = re.compile(r"f(\d+)")

#: The mime a frame line carries for each format Pillow reports (13.9).
_MIME = {"PNG": "image/png", "JPEG": "image/jpeg"}


class RecordingError(Exception):
    """The recording or the case cannot be used: the CLI's exit status 2.

    "I could not run" is not "the replay disagrees with the phone" (status 1),
    and the two must not look alike on the way out.
    """


# --------------------------------------------------------------------------
# Reading a recording


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read_lines(path: Path):
    """Yield ``(line number, parsed JSON)`` for every non-blank line.

    One line at a time: a recording of a full scan is tens of megabytes of
    base64, and only the observation lines (without their pixels) are kept.
    """
    try:
        # utf-8-sig: the phone writes no BOM, but a file re-saved on Windows has one.
        handle = path.open("r", encoding="utf-8-sig")
    except OSError as error:
        raise RecordingError(f"cannot read {path}: {error.strerror or error}") from error
    with handle:
        try:
            for number, text in enumerate(handle, start=1):
                if not text.strip():
                    continue
                try:
                    yield number, json.loads(text)
                except json.JSONDecodeError as error:
                    raise RecordingError(f"line {number}: not JSON ({error.msg})") from error
        except UnicodeDecodeError as error:
            raise RecordingError(f"{path} is not UTF-8 text") from error


def _need(number: int, line: dict, key: str, check, what: str):
    """``line[key]``, or a RecordingError naming the line when it is absent or the wrong kind."""
    if key not in line or not check(line[key]):
        raise RecordingError(f"line {number}: {line.get('kind')!r} line needs {key} as {what}")
    return line[key]


def _number_or_null(number: int, line: dict, key: str):
    """A reading that may be absent or null (Brave's blocked shape): null in both cases."""
    value = line.get(key)
    if value is not None and not _is_number(value):
        raise RecordingError(f"line {number}: {key} must be a number or null")
    return value


def _check_header(number: int, line) -> dict:
    if not isinstance(line, dict) or line.get("kind") != "header":
        raise RecordingError(f"line {number}: a recording starts with its header line")
    if line.get("format") != RECORDING_FORMAT:
        raise RecordingError(f"line {number}: format is not {RECORDING_FORMAT!r}")
    if line.get("version") != 1:
        raise RecordingError(f"line {number}: recording version {line.get('version')!r} is not 1")
    analysis = line.get("analysis")
    if not isinstance(analysis, dict):
        raise RecordingError(f"line {number}: the header has no analysis size")
    for key in ("width", "height"):
        value = analysis.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RecordingError(f"line {number}: the header's analysis {key} is not a positive integer")
    return line


def _decode_frame(number: int, line: dict) -> Image.Image:
    """The frame as an RGB image, from JPEG or PNG bytes, checked against its declared size."""
    data = _need(number, line, "data", lambda v: isinstance(v, str), "base64 text")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RecordingError(f"line {number}: frame data is not base64") from error
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise RecordingError(f"line {number}: frame data is {image.format}, not JPEG or PNG")
            # The mime the recorder wrote is the encoder's own word for what the
            # bytes are; a line that disagrees with its bytes is a damaged one.
            declared_mime = line.get("mime")
            if declared_mime is not None and declared_mime != _MIME[image.format]:
                raise RecordingError(f"line {number}: frame says {declared_mime!r} but the data is {image.format}")
            image.load()
            rgb = image.convert("RGB")
    except RecordingError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        # UnidentifiedImageError is an OSError; a truncated JPEG is an OSError
        # from load(); a damaged PNG chunk can be a SyntaxError; a frame that
        # claims to be enormous is Pillow's DecompressionBombError.
        raise RecordingError(f"line {number}: frame data does not decode ({error})") from error
    declared = (line["width"], line["height"])
    if rgb.size != declared:
        raise RecordingError(
            f"line {number}: frame says {declared[0]} x {declared[1]} but decodes to "
            f"{rgb.size[0]} x {rgb.size[1]}")
    return rgb


def _observation(number: int, line: dict) -> dict:
    """One 13.1 line from one recording line: named fields only, in 13.1's order.

    Built from a list of names, never from the object it was handed, as the
    recorder writes it: a key a newer phone adds does not leak into the case.
    """
    kind = line["kind"]
    if kind == "orientation":
        _need(number, line, "t_event_ms", _is_number, "a number")
        _need(number, line, "t_receive_ms", _is_number, "a number")
        out = {"kind": "orientation"}
        # `event` absent means deviceorientationabsolute (13.1, the legacy
        # files), so an absent one stays absent rather than being filled in.
        if "event" in line:
            if line["event"] not in _ORIENTATION_EVENTS:
                raise RecordingError(f"line {number}: orientation event {line['event']!r} is unknown")
            out["event"] = line["event"]
        out["t_event_ms"] = line["t_event_ms"]
        out["t_receive_ms"] = line["t_receive_ms"]
        for key in ("alpha", "beta", "gamma"):
            out[key] = _number_or_null(number, line, key)
        absolute = line.get("absolute")
        if absolute is not None and not isinstance(absolute, bool):
            raise RecordingError(f"line {number}: absolute must be true, false or null")
        out["absolute"] = absolute
        return out
    if kind == "motion":
        _need(number, line, "t_event_ms", _is_number, "a number")
        _need(number, line, "t_receive_ms", _is_number, "a number")
        rate = line.get("rate")
        if rate is not None:
            if not isinstance(rate, dict):
                raise RecordingError(f"line {number}: rate must be an object or null")
            rate = {key: _number_or_null(number, rate, key) for key in ("alpha", "beta", "gamma")}
        return {"kind": "motion", "t_event_ms": line["t_event_ms"],
                "t_receive_ms": line["t_receive_ms"], "rate": rate}
    # screen
    _need(number, line, "t_event_ms", _is_number, "a number")
    _need(number, line, "t_receive_ms", _is_number, "a number")
    _need(number, line, "angle", _is_number, "a number")
    return {"kind": "screen", "t_event_ms": line["t_event_ms"],
            "t_receive_ms": line["t_receive_ms"], "angle": line["angle"]}


def _estimate_fps(frames: list):
    """Frames per second delivered, from ``(frame number, presentation ms)`` pairs.

    Frame ids count deliveries and are kept when frames are decimated (13.9),
    so the rate is the id span over the time span, not the count of lines in
    the file: a recording that kept every second frame still reads as the
    camera's rate. ``None`` when there is no span to measure.
    """
    if len(frames) < 2:
        return None
    ordered = sorted(frames, key=lambda f: f[1])
    seconds = (ordered[-1][1] - ordered[0][1]) / 1000.0
    numbers = [n for n, _ in ordered]
    steps = numbers[-1] - numbers[0] if all(n is not None for n in numbers) else len(ordered) - 1
    if seconds <= 0 or steps <= 0:
        return None
    return round(steps / seconds, 3)


# --------------------------------------------------------------------------
# import-recording


def _write_case(recording: Path, case_id: str, work: Path) -> None:
    frames_dir = work / "input" / "frames"
    frames_dir.mkdir(parents=True)
    (work / "recording").mkdir()

    header = None
    observations: list = []
    actions: list = []
    report = None
    frame_times: list = []
    seen_ids: set = set()

    for number, line in _read_lines(recording):
        if header is None:
            header = _check_header(number, line)
            continue
        if not isinstance(line, dict):
            raise RecordingError(f"line {number}: expected a JSON object")
        kind = line.get("kind")
        if kind == "frame":
            frame_id = _need(number, line, "frame_id", lambda v: isinstance(v, str) and bool(_FRAME_ID.fullmatch(v)),
                             "a plain name (letters, digits, - and _)")
            if frame_id in seen_ids:
                raise RecordingError(f"line {number}: frame {frame_id} appears twice")
            seen_ids.add(frame_id)
            _need(number, line, "t_present_ms", _is_number, "a number")
            for key in ("width", "height"):
                _need(number, line, key, lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
                      "a positive integer")
            capture = _number_or_null(number, line, "t_capture_ms")
            _decode_frame(number, line).save(frames_dir / f"{frame_id}.png", format="PNG")
            match = _FRAME_NUMBER.fullmatch(frame_id)
            frame_times.append((int(match.group(1)) if match else None, line["t_present_ms"]))
            observations.append({
                "kind": "frame", "frame_id": frame_id, "t_capture_ms": capture,
                "t_present_ms": line["t_present_ms"], "width": line["width"], "height": line["height"],
                "file": f"frames/{frame_id}.png",
            })
        elif kind in ("orientation", "motion", "screen"):
            observations.append(_observation(number, line))
        elif kind == "action":
            _need(number, line, "t_ms", _is_number, "a number")
            _need(number, line, "action", lambda v: v in ("begin", "finish"), "'begin' or 'finish'")
            actions.append({"t_ms": line["t_ms"], "action": line["action"]})
        elif kind == "report":
            if report is not None:
                raise RecordingError(f"line {number}: a second report")
            report = _need(number, line, "report", lambda v: isinstance(v, dict), "an object")
        else:
            # Dropping a line of a kind this importer has never heard of would
            # replay a different night than the phone had.
            raise RecordingError(f"line {number}: unknown line kind {kind!r}")

    if header is None:
        raise RecordingError(f"{recording} is empty")
    if not frame_times:
        raise RecordingError(f"{recording} holds no frames: there is nothing to replay")

    # By delivery time, equal times in file order (CONTRACT.md). The recording
    # is in the order the page appended lines, and a frame's presentation time
    # can be a few milliseconds before the callback that recorded it, so the
    # file order is not quite the delivery order.
    observations.sort(key=_delivery_ms)
    observations_path = work / "input" / "observations.jsonl"
    _write_jsonl(observations_path, observations)
    _write_jsonl(work / "input" / "actions.jsonl", actions)
    _write_json(work / "input" / "scanner.json", {})
    if report is not None:
        _write_json(work / "recording" / "report.json", report)

    frame_names = sorted(p.name for p in frames_dir.glob("*.png"))
    hashes = {
        "frames": _hash_concatenated_digests([frames_dir / name for name in frame_names]),
        "observations": _sha256_hex(observations_path.read_bytes()),
        # There is no truth/ to hash: null says so, where the hash of nothing
        # would claim a truth that was empty.
        "truth": None,
    }
    analysis = header["analysis"]
    manifest = {
        "schema": 1,
        "case_id": case_id,
        "seed": 0,
        "scene": "recording",
        "route": "recording",
        "camera": {"width": analysis["width"], "height": analysis["height"], "fov_short_deg": None},
        "fps": _estimate_fps(frame_times),
        "expected": None,
        "profile": "recording",
        "hashes": hashes,
        "versions": {"three": None, "chromium": None, "webgl_renderer": None,
                     "python": platform.python_version(),
                     "numpy": np.__version__, "pillow": PIL.__version__,
                     "app_commit": header.get("commit")},
    }
    _write_json(work / "manifest.json", manifest)


def import_recording(recording, case_id: str, out_root) -> Path:
    """Write ``<out_root>/<case_id>`` from a recording file and return its path.

    Nothing is left behind on failure: the case is built in a sibling
    directory and renamed into place only when every line has been read, so a
    recording that breaks on its last line leaves no half case for a replay to
    pick up. An existing ``<case_id>`` is refused rather than merged into: a
    stale frame or a stale ``result/`` beside new inputs is a replay of one
    night scored against another.
    """
    recording, out_root = Path(recording), Path(out_root)
    if not _CASE_ID.fullmatch(case_id):
        raise RecordingError(f"{case_id!r} is not a usable case id (letters, digits, '.', '_' and '-')")
    if not recording.is_file():
        raise RecordingError(f"no recording at {recording}")
    final = out_root / case_id
    if final.exists():
        raise RecordingError(f"{final} already exists: remove it or pick another case id")
    out_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{case_id}.", suffix=".importing", dir=out_root))
    try:
        _write_case(recording, case_id, work)
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return final


# --------------------------------------------------------------------------
# selfcheck


@dataclass(frozen=True)
class Check:
    """One row of the selfcheck table."""

    name: str
    replay: str
    report: str
    diff: str
    limit: str
    ok: bool


def _load(path: Path, what: str, hint: str):
    if not path.is_file():
        raise RecordingError(f"no {what} at {path}: {hint}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecordingError(f"{what} at {path} cannot be read ({error})") from error


def _dig(value, path: tuple, source: str):
    """``value[a][b]...`` for a path of keys, or a RecordingError naming the missing key."""
    here = value
    for key in path:
        if not isinstance(here, dict) or key not in here:
            raise RecordingError(f"{source} has no {'.'.join(path)}")
        here = here[key]
    return here


def _count(value, name: str, source: str) -> float:
    if not _is_number(value):
        raise RecordingError(f"{source}: {name} is not a number")
    return float(value)


def _observed_events(path: Path):
    """Events per stream and the span of the observations in seconds.

    The span runs from the first to the last delivery time (CONTRACT.md), the
    same for every stream, so a rate is a count over one common denominator.
    """
    if not path.is_file():
        raise RecordingError(f"no observations at {path}")
    counts = {name: 0 for name in EVENT_TYPES}
    first = last = None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RecordingError(f"observations at {path} cannot be read ({error})") from error
    for number, text_line in enumerate(text.splitlines(), start=1):
        if not text_line.strip():
            continue
        try:
            line = json.loads(text_line)
            at = _delivery_ms(line)
            kind = line["kind"]
        except (ValueError, KeyError, TypeError) as error:
            raise RecordingError(f"observations line {number} cannot be read ({error!r})") from error
        first = at if first is None else min(first, at)
        last = at if last is None else max(last, at)
        if kind == "orientation":
            counts[line.get("event", "deviceorientationabsolute")] += 1
        elif kind == "motion":
            counts["devicemotion"] += 1
    if first is None or last is None or last <= first:
        raise RecordingError(f"the observations at {path} span no time: there is no rate to compare")
    return counts, (last - first) / 1000.0


def _deg(value) -> str:
    return "null" if value is None else f"{value:.4f}"


def _mode(value) -> str:
    return "null" if value is None else str(value)


def _transitions(changes, name: str, source: str) -> list:
    """``[(from, to), ...]`` of a list of mode changes; their times are not compared."""
    if not isinstance(changes, list):
        raise RecordingError(f"{source}: {name} is not a list")
    pairs = []
    for entry in changes:
        if not isinstance(entry, dict) or "from" not in entry or "to" not in entry:
            raise RecordingError(f"{source}: every entry of {name} needs from and to")
        pairs.append((entry["from"], entry["to"]))
    return pairs


def _chain(pairs: list) -> str:
    return ", ".join(f"{a} > {b}" for a, b in pairs) if pairs else "none"


def _pct(replay: float, report: float) -> str:
    return f"{(replay - report) / report * 100:+.3f} %" if report else "n/a"


def selfcheck(case_dir, result_dir=None) -> list:
    """The replay's diagnostics against the phone's report, one ``Check`` per row.

    ``case_dir`` is an imported recording; ``result_dir`` defaults to its
    ``result/``. Raises ``RecordingError`` when it could not run: a missing
    replay, a recording that carried no report, a file missing a field.
    """
    case_dir = Path(case_dir)
    result_dir = Path(result_dir) if result_dir is not None else case_dir / "result"
    report = _load(case_dir / "recording" / "report.json", "recording report",
                   "the recording carried no report line")
    diagnostics = _load(result_dir / "diagnostics.json", "replay diagnostics",
                        "replay the case with --scanner pano first")
    if not isinstance(report, dict) or not isinstance(diagnostics, dict):
        raise RecordingError("the report and the diagnostics must both be JSON objects")
    rep, dia = "recording report", "replay diagnostics"
    checks: list = []

    # Focal length, relative.
    f_replay = _count(_dig(diagnostics, ("focal", "f_norm"), dia), "focal.f_norm", dia)
    f_report = _count(_dig(report, ("focal", "fNorm"), rep), "focal.fNorm", rep)
    checks.append(Check("focal f_norm", f"{f_replay:.6g}", f"{f_report:.6g}", _pct(f_replay, f_report),
                        f"{FOCAL_REL_TOL * 100:g} %", abs(f_replay - f_report) <= FOCAL_REL_TOL * abs(f_report)))

    # Closure residual, absolute degrees. Null on both sides is agreement:
    # neither closed the ring, so neither has a residual to differ about.
    p_replay = _dig(diagnostics, ("loop", "post_deg"), dia)
    p_report = _dig(report, ("loop", "postDeg"), rep)
    for value, source in ((p_replay, dia), (p_report, rep)):
        if value is not None and not _is_number(value):
            raise RecordingError(f"{source}: the loop residual is neither a number nor null")
    if p_replay is None or p_report is None:
        ok, diff = p_replay is None and p_report is None, "n/a"
    else:
        ok, diff = abs(p_replay - p_report) <= LOOP_DEG_TOL, f"{p_replay - p_report:+.4f} deg"
    checks.append(Check("loop post_deg", _deg(p_replay), _deg(p_report), diff, f"{LOOP_DEG_TOL:g} deg", ok))

    # Predictor mode, equal. Diagnostics keeps the mode at Begin as
    # `predictor_mode` and the handoffs after it as `mode_changes`, the same
    # pairing SensorFacts has as `modeAtBegin` and `modeChanges` (SPEC-v2 3.4),
    # so each is compared with its twin and with nothing else. A replay that
    # latched another mode from the one the phone latched fails, even if it
    # ended where the phone did; a replay that never handed off fails the
    # second row, not the first.
    m_replay = _dig(diagnostics, ("predictor_mode",), dia)
    at_begin = _dig(report, ("sensors", "modeAtBegin"), rep)
    checks.append(Check("predictor mode", _mode(m_replay), _mode(at_begin), "n/a", "equal", m_replay == at_begin))
    t_replay = _transitions(_dig(diagnostics, ("mode_changes",), dia), "mode_changes", dia)
    t_report = _transitions(_dig(report, ("sensors", "modeChanges"), rep), "sensors.modeChanges", rep)
    checks.append(Check("predictor mode changes", _chain(t_replay), _chain(t_report), "n/a", "equal",
                        t_replay == t_report))

    # Keyframe count, relative.
    k_replay = _dig(diagnostics, ("keyframes",), dia)
    if not isinstance(k_replay, list):
        raise RecordingError(f"{dia}: keyframes is not a list")
    k_report = _count(_dig(report, ("keyframes", "total"), rep), "keyframes.total", rep)
    checks.append(Check("keyframe count", str(len(k_replay)), f"{k_report:g}", _pct(len(k_replay), k_report),
                        f"{KEYFRAME_REL_TOL * 100:g} %", abs(len(k_replay) - k_report) <= KEYFRAME_REL_TOL * abs(k_report)))

    # Event rates, relative, one row per stream.
    observed, span_s = _observed_events(case_dir / "input" / "observations.jsonl")
    events = _dig(report, ("sensors", "events"), rep)
    if not isinstance(events, list):
        raise RecordingError(f"{rep}: sensors.events is not a list")
    reported = {}
    for entry in events:
        if isinstance(entry, dict) and entry.get("type") in EVENT_TYPES:
            reported[entry["type"]] = _count(entry.get("total"), f"sensors.events {entry['type']} total", rep)
    for name in EVENT_TYPES:
        hz_replay = observed[name] / span_s
        hz_report = reported.get(name, 0.0) / span_s
        checks.append(Check(f"event rate {name}", f"{hz_replay:.2f} Hz", f"{hz_report:.2f} Hz",
                            _pct(hz_replay, hz_report), f"{RATE_REL_TOL * 100:g} %",
                            abs(hz_replay - hz_report) <= RATE_REL_TOL * abs(hz_report)))
    return checks


#: Printed under the table when it has event-rate rows, because the column
#: those rows fill is headed "replay" and is not the replay's.
EVENT_RATE_NOTE = ("note: the event rate rows count the events in the imported case against the phone's own "
                   "counts, over one span; Diagnostics holds no event counts, so they check the import, "
                   "not the replay")


def format_table(checks) -> str:
    """The rows as an aligned text table, one verdict per row, and the event-rate note when it applies."""
    header = ("check", "replay", "report", "diff", "limit", "verdict")
    rows = [header] + [(c.name, c.replay, c.report, c.diff, c.limit, "PASS" if c.ok else "FAIL") for c in checks]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    if any(c.name.startswith("event rate") for c in checks):
        lines.append(EVENT_RATE_NOTE)
    return "\n".join(lines)
