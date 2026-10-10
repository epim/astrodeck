# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Importing a phone's recording as a case, and checking a replay against its report.

SPEC-v2 T23, 13.1, 13.9, 13.10 and owner check D7. The recording below is
written by this file, by hand, per 13.9: the header, then the lines in the order
the page appended them (a frame's line lands a few milliseconds after its
presentation time, so the file is NOT in delivery order, as a real one is not),
the begin and finish actions, and last the report. Half the frames are PNG (as
the replay's lossless encoder writes them, with an alpha channel) and half are
JPEG (as the phone writes them). Everything it is checked against is computed
here from the same lists the file was written from, never by calling the
importer.

Mutants, each of which must turn a test below red (SPEC-v2 7.1):

- (a) drop motion events on import (the task's named mutant):
  ``SelfcheckMatches.test_a_faithful_import_selfchecks_clean`` fails on the
  devicemotion event-rate row, ``SelfcheckRows.test_missing_motion_events_...``
  is the same row, and ``ImportRecording.test_observation_lines_match_13_1``
  errors with a KeyError on ``motion`` (no motion lines to read).
- (b) widen the focal limit from 0.1 % to 1 %:
  ``SelfcheckRows.test_focal_passes_at_0_05_pct_and_fails_at_0_2_pct`` fails.
- (c) have the CLI exit 0 on a mismatch: ``ExitCodes.test_a_mismatch_exits_1`` fails.
- (d) keep the file order instead of sorting by delivery time:
  ``ImportRecording.test_observations_are_sorted_by_delivery_time`` fails.
- (e) leave a half-built case behind on a bad recording:
  ``ImportRefusals.test_a_bad_recording_leaves_nothing_behind`` fails.
- (f) do not catch ``DecompressionBombError`` on a frame:
  ``ImportRefusals.test_frames_that_cannot_be_decoded_are_refused`` errors.
- (g) accept the phone's final mode as well as its mode at Begin:
  ``SelfcheckRows.test_the_mode_at_begin_is_the_report_mode_and_the_final_mode_is_not`` fails.
- (h) estimate fps from the line count instead of the id span:
  ``ImportRecording.test_manifest`` and ``ImportOptions.test_fps_is_the_id_span_over_the_time_span`` fail.
- (i) drop ``abs()`` from the closure comparison:
  ``SelfcheckRows.test_the_closure_residual_is_compared_in_degrees`` fails on the negative side.
- (j) count a legacy orientation line (no ``event``) as ``deviceorientation``:
  ``SelfcheckRows.test_a_legacy_orientation_line_counts_as_absolute`` fails.
- (k) do not compare ``mode_changes`` with ``modeChanges``:
  ``SelfcheckRows.test_the_handoffs_must_be_the_same`` fails.
- (l) do not check a frame's mime against its bytes:
  ``ImportRefusals.test_a_frame_whose_mime_disagrees_with_its_bytes_is_refused`` fails.
- (m) open the recording as plain utf-8, which refuses a BOM:
  ``ImportOptions.test_a_byte_order_mark_is_tolerated`` fails.
"""
import base64
import contextlib
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from unittest import mock

import numpy as np
from PIL import Image

from sim import recording
from sim.__main__ import main as cli_main

ROOT = pathlib.Path(__file__).resolve().parents[1]

W, H = 18, 32
DELIVERED = 120                    # frames delivered; every second one is kept (every = 2)
FRAME_MS = 1000.0 / 30
T0 = 1000.0                        # the camera opened a second before the first frame
T_LAST_FRAME = round(T0 + (DELIVERED - 1) * FRAME_MS, 3)
T_FINISH = round(T_LAST_FRAME + 500, 3)
N_RELATIVE, N_ABSOLUTE, N_MOTION = 60, 120, 240      # 15, 30 and 60 Hz over the 4 s

FRAME_KEYS = ["kind", "frame_id", "t_capture_ms", "t_present_ms", "width", "height", "file"]
ORIENTATION_KEYS = ["kind", "event", "t_event_ms", "t_receive_ms", "alpha", "beta", "gamma", "absolute"]
MOTION_KEYS = ["kind", "t_event_ms", "t_receive_ms", "rate"]
SCREEN_KEYS = ["kind", "t_event_ms", "t_receive_ms", "angle"]


def _line(record: dict) -> str:
    return json.dumps(record, separators=(",", ":")) + "\n"


def _stats(n: int = 0) -> dict:
    return {"n": n, "p50": None, "p95": None, "max": None} if n == 0 else {"n": n, "p50": 1.0, "p95": 2.0, "max": 3.0}


def scan_report(*, f_norm=1.3324, post_deg=0.04, mode="relative", changes=(), keyframes=85,
                counts=None) -> dict:
    """A ScanReport (3.4) with every field, camelCase as the phone writes it."""
    counts = counts or {"deviceorientation": N_RELATIVE, "deviceorientationabsolute": N_ABSOLUTE,
                        "devicemotion": N_MOTION}
    return {
        "format": "astrodeck-pano-report", "version": 2,
        "scanner": {"commit": "0123abc", "sensorOnly": False},
        "browser": {"brands": ["Chromium", "Brave"], "mobile": True, "platform": "Android"},
        "timeline": {"beginMs": T0, "finishMs": T_LAST_FRAME, "endedBy": "user"},
        "camera": {
            "settings": {"width": 1080, "height": 1920, "frameRate": 30, "resizeMode": "none", "zoom": 1,
                         "focusMode": "continuous", "focusDistance": None, "facingMode": "environment",
                         "label": "back camera"},
            "bench": None, "farbled": False, "exposureReadable": True, "analysis": {"w": W, "h": H},
        },
        "frames": {"delivered": DELIVERED, "viaRvfc": True, "captureTimePresent": 51,
                   "slips": {"pairs": 118, "slips": 0},
                   "lag": {"nowMinusCapture": _stats(51), "presentMinusCapture": _stats(51),
                           "expectedMinusNow": _stats()},
                   "intervalMs": _stats(119), "presentedGaps": 0},
        "sensors": {
            "modeAtBegin": mode,
            "modeChanges": [{"tMs": t, "from": a, "to": b} for t, a, b in changes],
            "events": [{"type": name, "total": counts[name], "absoluteTrue": 0, "absoluteFalse": 0,
                        "absoluteMissing": 0, "nullReadings": 0, "duplicates": 0}
                       for name in ("deviceorientation", "deviceorientationabsolute", "devicemotion")],
            "movingHz": {"relative": 14.8, "absolute": 29.9, "motion": 60.0},
            "rateByOmega": [{"omegaFrom": 0, "omegaTo": 2, "relativeHz": None, "absoluteHz": None}],
            "blocked": False,
            "axis": {"perm": [0, 1, 2], "sign": [1, 1, 1], "unit": "deg", "fit": 0.99, "confirmed": True,
                     "samples": 300},
            "gyroZeroTriples": 0, "gyroSeenNonZero": True, "staleRefusals": 0, "staleLimitMs": _stats(),
            "latency": {"priorMs": 60, "tauMs": 62.5, "sigmaMs": 4.0, "pairs": 12, "applied": True},
        },
        "focal": {"source": "default", "state": "locked", "fNorm": f_norm, "sdPct": 0.55, "ratios": 5,
                  "shortFovDeg": 41.1, "closurePct": None, "gyroScale": None, "ultraWideSuspected": False},
        "loop": {"closed": True, "method": "image", "preDeg": [0.1, 0.2, 0.3], "postDeg": post_deg,
                 "match": {"early": 5, "late": 80}, "unwrappedDeg": 382.5},
        "north": None, "declinationApplied": False,
        "keyframes": {"total": keyframes, "aligned": keyframes - 5, "blurred": 3, "sensor": 2, "ms": _stats(keyframes),
                      "readbackMs": _stats(keyframes), "innovationSteadyDeg": _stats(), "innovationAccelDeg": _stats(),
                      "perSliceMs": _stats()},
        "liveFrame": {"drawMs": _stats(), "redraws": 0},
        "horizon": None,
        "recording": {"on": True, "every": 2, "frames": DELIVERED // 2},
        "captureLog": [{"at": 1234.5, "frameId": 7, "outcome": "accepted", "detail": "aligned", "kf": 3}],
        "slices": [], "error": None,
    }


def diagnostics(report: dict) -> dict:
    """The Diagnostics (3.4) a replay that agrees with ``report`` would write."""
    return {
        "version": 1, "scanner": "pano", "sensor_only": False, "begin_ms": T0, "finish_ms": T_LAST_FRAME,
        "predictor_mode": report["sensors"]["modeAtBegin"],
        "mode_changes": [{"t_ms": c["tMs"], "from": c["from"], "to": c["to"]} for c in report["sensors"]["modeChanges"]],
        "axis_mapping": None,
        "tau_ms": 62.5, "tau_sigma_ms": 4.0, "tau_pairs": 12, "tau_applied": True,
        "focal": {"state": "locked", "f_norm": report["focal"]["fNorm"], "sd_pct": 0.55, "ratios": 5,
                  "short_fov_deg": 41.1},
        "loop": {"closed": True, "method": "image", "pre_deg": [0.1, 0.2, 0.3], "post_deg": report["loop"]["postDeg"],
                 "match": {"early_kf": 5, "late_kf": 80}, "unwrapped_deg": 382.5},
        "north": None, "declination_applied": False,
        "keyframes": [{"id": i, "frame_id": f"f{2 * i + 1:06d}", "t_ms": T0 + 10 * i, "q": [1, 0, 0, 0],
                       "cls": "aligned", "sigma_deg": 0.1} for i in range(report["keyframes"]["total"])],
        "keyframe_ms": _stats(), "readback_ms": _stats(), "stale_refusals": 0, "extractor": "tracer",
    }


@dataclass
class Synthetic:
    path: pathlib.Path
    tmp: object = None
    lines: list = field(default_factory=list)        # the recording's lines as dicts, in file order
    frames: dict = field(default_factory=dict)       # frame_id -> {"format", "rgb"}
    report: dict = None


def _frame_rgb(k: int) -> np.ndarray:
    """A smooth picture, so a JPEG of it is close to it."""
    ys, xs = np.mgrid[0:H, 0:W]
    r = 40 + xs * 8 + (k % 40)
    g = 30 + ys * 5
    b = 120 + (xs + ys) * 2
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def build_recording(path: pathlib.Path) -> Synthetic:
    """Write the synthetic recording of 13.9 and keep what it was written from."""
    syn = Synthetic(path=path)
    arrivals = []          # (arrival ms, line)

    kept = 0
    for k in range(1, DELIVERED + 1, 2):             # every = 2: delivery counts 1, 3, 5, ...
        frame_id = f"f{k:06d}"
        t_present = round(T0 + (k - 1) * FRAME_MS, 3)
        rgb = _frame_rgb(k)
        buffer = io.BytesIO()
        if kept % 2 == 0:
            rgba = np.concatenate([rgb, np.full((H, W, 1), 255, np.uint8)], axis=-1)
            Image.fromarray(rgba, "RGBA").save(buffer, format="PNG")
            mime, fmt = "image/png", "PNG"
        else:
            Image.fromarray(rgb, "RGB").save(buffer, format="JPEG", quality=95)
            mime, fmt = "image/jpeg", "JPEG"
        syn.frames[frame_id] = {"format": fmt, "rgb": rgb}
        arrivals.append((t_present + 8, {
            "kind": "frame", "frame_id": frame_id,
            "t_capture_ms": None if kept % 7 == 3 else round(t_present - 40, 3),
            "t_present_ms": t_present, "width": W, "height": H, "mime": mime,
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        }))
        kept += 1

    for i in range(N_RELATIVE):
        t = round(T0 - 50 + i * 1000 / 15, 3)
        arrivals.append((t + 5, {"kind": "orientation", "event": "deviceorientation", "t_event_ms": t,
                                 "t_receive_ms": round(t + 5, 3), "alpha": round((i * 3.1) % 360, 1),
                                 "beta": 67.0, "gamma": -1.2, "absolute": False}))
    for i in range(N_ABSOLUTE):
        t = round(T0 - 40 + i * 1000 / 30, 3)
        blocked = i == 6                              # Brave's blocked shape: the event fires with no angles
        arrivals.append((t + 5, {"kind": "orientation", "event": "deviceorientationabsolute", "t_event_ms": t,
                                 "t_receive_ms": round(t + 5, 3),
                                 "alpha": None if blocked else round(101.4 + i * 0.1, 1),
                                 "beta": None if blocked else 67.0, "gamma": None if blocked else -1.2,
                                 "absolute": None if blocked else True}))
    for i in range(N_MOTION):
        t = round(T0 - 30 + i * 1000 / 60, 3)
        rate = {"alpha": 0.1, "beta": 18.4, "gamma": -7.8}
        if i == 2:
            rate = None
        elif i == 3:
            rate["gamma"] = None
        arrivals.append((t + 5, {"kind": "motion", "t_event_ms": t, "t_receive_ms": round(t + 5, 3), "rate": rate}))
    arrivals.append((3005, {"kind": "screen", "t_event_ms": 3000, "t_receive_ms": 3005, "angle": 90}))
    arrivals.append((T0, {"kind": "action", "t_ms": T0, "action": "begin"}))
    arrivals.append((T_FINISH, {"kind": "action", "t_ms": T_FINISH, "action": "finish"}))

    arrivals.sort(key=lambda item: item[0])           # stable: the order the page appended them
    header = {
        "kind": "header", "format": "astrodeck-pano-recording", "version": 1, "encoder": "image/jpeg",
        "video": {"width": 1080, "height": 1920}, "analysis": {"width": W, "height": H},
        "settings": {"width": 1080, "height": 1920, "frameRate": 30, "resizeMode": None, "zoom": None,
                     "focusMode": None, "focusDistance": None, "facingMode": "environment", "label": None},
        "commit": "0123abc",
    }
    syn.report = scan_report()
    syn.lines = [header] + [line for _, line in arrivals] + [{"kind": "report", "report": syn.report}]
    path.write_text("".join(_line(l) for l in syn.lines), encoding="utf-8", newline="\n")
    return syn


SYN = None


def setUpModule():
    global SYN
    tmp = tempfile.TemporaryDirectory()
    SYN = build_recording(pathlib.Path(tmp.name) / "scan.jsonl")
    SYN.tmp = tmp


def tearDownModule():
    SYN.tmp.cleanup()


def read_jsonl(path: pathlib.Path) -> list:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines() if text]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_cli(*argv):
    """``(exit status, stdout, stderr)`` of ``python -m sim`` with these arguments, in process."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main([str(a) for a in argv])
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue(), err.getvalue()


class ImportRecording(unittest.TestCase):
    """The synthetic recording, imported once and read many ways."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.tmp.name)
        cls.case = recording.import_recording(SYN.path, "scan", cls.root)
        cls.observations = read_jsonl(cls.case / "input" / "observations.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_case_directory_has_the_files_and_no_truth(self):
        for rel in ["manifest.json", "input/observations.jsonl", "input/actions.jsonl", "input/scanner.json",
                    "recording/report.json"]:
            self.assertTrue((self.case / rel).is_file(), rel)
        self.assertFalse((self.case / "truth").exists())
        self.assertEqual((self.case / "input" / "scanner.json").read_text(encoding="utf-8"), "{}")
        # The import is the only thing in the root: no half-built sibling.
        self.assertEqual([p.name for p in self.root.iterdir()], ["scan"])

    def test_every_kept_frame_is_a_png_of_the_right_size_named_by_its_id(self):
        names = sorted(p.name for p in (self.case / "input" / "frames").iterdir())
        self.assertEqual(names, sorted(f"{frame_id}.png" for frame_id in SYN.frames))
        self.assertEqual(len(names), DELIVERED // 2)
        self.assertEqual({SYN.frames[i]["format"] for i in SYN.frames}, {"PNG", "JPEG"})
        for frame_id, original in SYN.frames.items():
            with Image.open(self.case / "input" / "frames" / f"{frame_id}.png") as image:
                self.assertEqual((image.format, image.mode, image.size), ("PNG", "RGB", (W, H)), frame_id)
                pixels = np.asarray(image)
            error = np.abs(pixels.astype(int) - original["rgb"].astype(int))
            if original["format"] == "PNG":
                self.assertEqual(int(error.max()), 0, frame_id)         # lossless in, lossless out
            else:
                self.assertLess(float(error.mean()), 3.0, frame_id)     # JPEG: close, not equal

    def test_observation_lines_match_13_1(self):
        by_kind = {}
        for obs in self.observations:
            by_kind.setdefault(obs["kind"], []).append(obs)
        keys = {"frame": FRAME_KEYS, "orientation": ORIENTATION_KEYS, "motion": MOTION_KEYS, "screen": SCREEN_KEYS}
        for kind, wanted in keys.items():
            for obs in by_kind[kind]:
                self.assertEqual(list(obs), wanted, kind)
        # Every frame and every event of the recording is there, once.
        source = [l for l in SYN.lines if l["kind"] in keys]
        self.assertEqual(len(self.observations), len(source))
        for kind in keys:
            self.assertEqual(len(by_kind[kind]), sum(1 for l in source if l["kind"] == kind), kind)
        self.assertEqual(len(by_kind["motion"]), N_MOTION)
        events = [o["event"] for o in by_kind["orientation"]]
        self.assertEqual(events.count("deviceorientation"), N_RELATIVE)
        self.assertEqual(events.count("deviceorientationabsolute"), N_ABSOLUTE)
        # The lines carry the recording's values, not a re-derivation of them.
        as_written = {json.dumps(l, sort_keys=True) for l in source if l["kind"] != "frame"}
        for obs in self.observations:
            if obs["kind"] != "frame":
                self.assertIn(json.dumps(obs, sort_keys=True), as_written)
        # A frame references its PNG, keeps its null capture time, and drops the inline pixels.
        frames = {o["frame_id"]: o for o in by_kind["frame"]}
        for line in source:
            if line["kind"] == "frame":
                obs = frames[line["frame_id"]]
                self.assertEqual(obs["file"], f"frames/{line['frame_id']}.png")
                self.assertEqual((obs["t_capture_ms"], obs["t_present_ms"], obs["width"], obs["height"]),
                                 (line["t_capture_ms"], line["t_present_ms"], W, H))
        self.assertTrue(any(o["t_capture_ms"] is None for o in frames.values()))
        # Brave's blocked shape and the null rates survive.
        self.assertTrue(any(o["alpha"] is None and o["absolute"] is None for o in by_kind["orientation"]))
        self.assertEqual(sum(1 for o in by_kind["motion"] if o["rate"] is None), 1)
        self.assertEqual(sum(1 for o in by_kind["motion"] if o["rate"] and o["rate"]["gamma"] is None), 1)

    def test_observations_are_sorted_by_delivery_time(self):
        def delivered(l):
            return l["t_present_ms"] if l["kind"] == "frame" else l["t_receive_ms"]
        source = [l for l in SYN.lines if l["kind"] in ("frame", "orientation", "motion", "screen")]
        # The fixture is not already in delivery order, or this test proves nothing.
        self.assertNotEqual([delivered(l) for l in source], sorted(delivered(l) for l in source))
        times = [delivered(o) for o in self.observations]
        self.assertEqual(times, sorted(times))

    def test_actions_are_the_action_lines(self):
        self.assertEqual(read_jsonl(self.case / "input" / "actions.jsonl"),
                         [{"t_ms": T0, "action": "begin"}, {"t_ms": T_FINISH, "action": "finish"}])

    def test_times_are_kept_as_recorded(self):
        # Not rebased to the first frame: the replay's begin_ms must line up with the report's timeline.
        first = next(o for o in self.observations if o["kind"] == "frame")
        self.assertEqual(first["t_present_ms"], T0)

    def test_manifest(self):
        manifest = json.loads((self.case / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["case_id"], "scan")
        self.assertEqual(manifest["seed"], 0)
        self.assertEqual((manifest["scene"], manifest["route"], manifest["profile"]),
                         ("recording", "recording", "recording"))
        self.assertEqual(manifest["camera"], {"width": W, "height": H, "fov_short_deg": None})
        # 60 frames kept of 120 delivered at 30 fps: the ids span the decimation, so the rate is the camera's.
        self.assertAlmostEqual(manifest["fps"], 30.0, delta=0.01)
        self.assertEqual(manifest["versions"]["app_commit"], "0123abc")
        self.assertEqual(set(manifest["versions"]), {"three", "chromium", "webgl_renderer", "python", "numpy",
                                                     "pillow", "app_commit"})

    def test_manifest_hashes_name_the_bytes_on_disk(self):
        """The recipe is CONTRACT.md's, written out again here rather than the importer's helper."""
        manifest = json.loads((self.case / "manifest.json").read_text(encoding="utf-8"))
        frames = sorted((self.case / "input" / "frames").glob("*.png"), key=lambda p: p.name)
        joined = "".join(sha256(p.read_bytes()) for p in frames)
        self.assertEqual(manifest["hashes"]["frames"], sha256(joined.encode("ascii")))
        self.assertEqual(manifest["hashes"]["observations"],
                         sha256((self.case / "input" / "observations.jsonl").read_bytes()))
        self.assertIsNone(manifest["hashes"]["truth"])          # there is no truth/ to hash
        self.assertEqual(set(manifest["hashes"]), {"frames", "observations", "truth"})

    def test_the_embedded_report_is_written_as_it_came(self):
        written = json.loads((self.case / "recording" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(written, SYN.report)

    def test_no_frame_path_leaves_the_input_directory(self):
        for obs in self.observations:
            if obs["kind"] == "frame":
                path = (self.case / "input" / obs["file"]).resolve()
                self.assertTrue(path.is_file())
                self.assertEqual(path.parent, (self.case / "input" / "frames").resolve())


class ImportOptions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def write(self, name, lines):
        path = self.root / name
        path.write_text("".join(_line(l) for l in lines), encoding="utf-8", newline="\n")
        return path

    def minimal(self, extra=()):
        header = SYN.lines[0]
        frame = next(l for l in SYN.lines if l["kind"] == "frame")
        return [header, frame, *extra]

    def test_a_recording_without_a_report_imports_and_has_no_report_file(self):
        path = self.write("r.jsonl", self.minimal())
        case = recording.import_recording(path, "bare", self.root / "cases")
        self.assertFalse((case / "recording" / "report.json").exists())
        self.assertTrue((case / "manifest.json").is_file())

    def test_a_legacy_orientation_line_keeps_the_event_key_absent(self):
        legacy = {"kind": "orientation", "t_event_ms": 1100, "t_receive_ms": 1105, "alpha": 1.0, "beta": 2.0,
                  "gamma": 3.0, "absolute": True}
        path = self.write("r.jsonl", self.minimal([legacy]))
        case = recording.import_recording(path, "legacy", self.root / "cases")
        orientation = [o for o in read_jsonl(case / "input" / "observations.jsonl") if o["kind"] == "orientation"]
        self.assertEqual(orientation, [legacy])             # 13.1: no `event` means deviceorientationabsolute

    def test_a_key_the_format_does_not_declare_is_not_carried_into_the_case(self):
        extra = {"kind": "motion", "t_event_ms": 1100, "t_receive_ms": 1105,
                 "rate": {"alpha": 1.0, "beta": 2.0, "gamma": 3.0, "stray": 9}, "latitude": 1.0}
        path = self.write("r.jsonl", self.minimal([extra]))
        case = recording.import_recording(path, "stray", self.root / "cases")
        text = (case / "input" / "observations.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("stray", text)
        self.assertNotIn("latitude", text)

    def test_fps_is_the_id_span_over_the_time_span(self):
        frame = next(l for l in SYN.lines if l["kind"] == "frame")
        later = dict(frame, frame_id="f000031", t_present_ms=frame["t_present_ms"] + 1000)
        path = self.write("r.jsonl", [SYN.lines[0], frame, later])
        case = recording.import_recording(path, "fps", self.root / "cases")
        self.assertEqual(json.loads((case / "manifest.json").read_text(encoding="utf-8"))["fps"], 30.0)

    def test_a_single_frame_has_no_fps(self):
        path = self.write("r.jsonl", self.minimal())
        case = recording.import_recording(path, "one", self.root / "cases")
        self.assertIsNone(json.loads((case / "manifest.json").read_text(encoding="utf-8"))["fps"])

    def test_a_byte_order_mark_is_tolerated(self):
        # The phone writes none, but a recording re-saved on Windows can start with one.
        path = self.write("r.jsonl", SYN.lines)
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        case = recording.import_recording(path, "bom", self.root / "cases")
        plain = recording.import_recording(SYN.path, "plain", self.root / "cases")
        for rel in ("input/observations.jsonl", "recording/report.json"):
            self.assertEqual((case / rel).read_bytes(), (plain / rel).read_bytes(), rel)


class ImportRefusals(unittest.TestCase):
    """Every way a recording cannot be imported is a RecordingError, never a half case."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.out = self.root / "cases"
        self.frame = next(l for l in SYN.lines if l["kind"] == "frame")

    def refused(self, lines, message, case_id="bad", raw=None):
        path = self.root / "r.jsonl"
        path.write_text(raw if raw is not None else "".join(_line(l) for l in lines), encoding="utf-8", newline="\n")
        with self.assertRaises(recording.RecordingError) as caught:
            recording.import_recording(path, case_id, self.out)
        self.assertIn(message, str(caught.exception))
        return caught.exception

    def test_a_bad_recording_leaves_nothing_behind(self):
        # The last line is the broken one, after valid frames have been decoded and written.
        self.refused([SYN.lines[0], self.frame, {"kind": "mystery"}], "unknown line kind")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_the_header_is_checked(self):
        header = SYN.lines[0]
        self.refused([self.frame], "starts with its header")
        self.refused([dict(header, format="something-else"), self.frame], "format")
        self.refused([dict(header, version=2), self.frame], "version")
        self.refused([dict(header, analysis={"width": 0, "height": 5}), self.frame], "analysis width")
        self.refused([], "empty", raw="")

    def test_a_recording_with_no_frames_is_refused(self):
        self.refused([SYN.lines[0]], "no frames")

    def test_a_line_that_is_not_json_names_its_line(self):
        self.refused(None, "line 2: not JSON", raw=_line(SYN.lines[0]) + "{nope\n")

    def test_frames_that_cannot_be_decoded_are_refused(self):
        header = SYN.lines[0]
        self.refused([header, dict(self.frame, data="!!not base64!!")], "base64")
        self.refused([header, dict(self.frame, data=base64.b64encode(b"not an image").decode())], "does not decode")
        self.refused([header, dict(self.frame, width=W + 1)], "decodes to")
        gif = io.BytesIO()
        Image.new("RGB", (W, H)).save(gif, format="GIF")
        self.refused([header, dict(self.frame, data=base64.b64encode(gif.getvalue()).decode())], "not JPEG or PNG")
        # A frame Pillow will not open for its size is a refusal, not a traceback.
        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            self.refused([header, self.frame], "does not decode")

    def test_a_frame_whose_mime_disagrees_with_its_bytes_is_refused(self):
        header = SYN.lines[0]
        by_format = {}
        for line in SYN.lines:
            if line["kind"] == "frame":
                by_format.setdefault(line["mime"], line)
        self.assertEqual(sorted(by_format), ["image/jpeg", "image/png"])
        jpeg, png = by_format["image/jpeg"], by_format["image/png"]
        self.refused([header, dict(jpeg, mime="image/png")], "'image/png' but the data is JPEG")
        self.refused([header, dict(png, mime="image/jpeg")], "'image/jpeg' but the data is PNG")
        self.refused([header, dict(png, mime="text/plain")], "but the data is PNG")
        self.assertEqual(list(self.out.iterdir()), [])
        # The matching mimes import, and a line with no mime at all is not refused for it.
        for line in (jpeg, png, {k: v for k, v in png.items() if k != "mime"}):
            path = self.root / "ok.jsonl"
            path.write_text("".join(_line(l) for l in (header, line)), encoding="utf-8", newline="\n")
            case = recording.import_recording(path, f"ok{len(list(self.out.iterdir()))}", self.out)
            self.assertTrue((case / "manifest.json").is_file())

    def test_a_frame_id_cannot_name_a_path(self):
        header = SYN.lines[0]
        self.refused([header, dict(self.frame, frame_id="../escape")], "frame_id")
        self.refused([header, dict(self.frame, frame_id="a/b")], "frame_id")
        self.refused([header, self.frame, self.frame], "appears twice")

    def test_unusable_lines_are_refused_by_name(self):
        header = SYN.lines[0]
        self.refused([header, self.frame, {"kind": "motion", "t_event_ms": 1}], "t_receive_ms")
        self.refused([header, self.frame, {"kind": "action", "t_ms": 1, "action": "pause"}], "begin")
        self.refused([header, self.frame, {"kind": "orientation", "event": "deviceorientationx", "t_event_ms": 1,
                                           "t_receive_ms": 1}], "unknown")
        report = {"kind": "report", "report": {}}
        self.refused([header, self.frame, report, report], "second report")

    def test_the_case_id_and_the_file_are_checked(self):
        for case_id in ["", "../up", "a/b", ".hidden", "a b"]:
            with self.assertRaises(recording.RecordingError, msg=case_id):
                recording.import_recording(SYN.path, case_id, self.out)
        with self.assertRaises(recording.RecordingError):
            recording.import_recording(self.root / "absent.jsonl", "x", self.out)

    def test_an_existing_case_is_not_overwritten(self):
        recording.import_recording(SYN.path, "dup", self.out)
        marker = self.out / "dup" / "marker.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(recording.RecordingError) as caught:
            recording.import_recording(SYN.path, "dup", self.out)
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


class SelfcheckBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.case = recording.import_recording(SYN.path, "scan", self.root)
        self.report = json.loads((self.case / "recording" / "report.json").read_text(encoding="utf-8"))
        self.replay(diagnostics(self.report))

    def replay(self, diag, result=None):
        """Write what a replay of the case would have written."""
        out = pathlib.Path(result) if result else self.case / "result"
        out.mkdir(parents=True, exist_ok=True)
        (out / "diagnostics.json").write_text(json.dumps(diag), encoding="utf-8")

    def rewrite_report(self, edit):
        edit(self.report)
        (self.case / "recording" / "report.json").write_text(json.dumps(self.report), encoding="utf-8")

    def rows(self):
        return {c.name: c for c in recording.selfcheck(self.case)}

    def failing(self):
        return sorted(name for name, c in self.rows().items() if not c.ok)

    def selfcheck_cli(self, *extra):
        return run_cli("selfcheck", "scan", "--cases", self.root, *extra)


class SelfcheckMatches(SelfcheckBase):
    def test_a_faithful_import_selfchecks_clean(self):
        rows = self.rows()
        self.assertEqual(self.failing(), [])
        self.assertEqual(list(rows), ["focal f_norm", "loop post_deg", "predictor mode", "predictor mode changes",
                                      "keyframe count", "event rate deviceorientation",
                                      "event rate deviceorientationabsolute", "event rate devicemotion"])
        # The motion row is a real rate of the right size: 240 events over the span of the observations.
        self.assertRegex(rows["event rate devicemotion"].replay, r"^\d+\.\d\d Hz$")
        self.assertEqual(rows["event rate devicemotion"].replay, rows["event rate devicemotion"].report)
        code, out, err = self.selfcheck_cli()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.count("PASS"), 9)            # eight rows and the verdict line
        self.assertNotIn("FAIL", out)
        for name in rows:
            self.assertIn(name, out)
        # The event-rate rows sit under a column headed "replay" and are not the replay's: the table says so.
        self.assertIn(recording.EVENT_RATE_NOTE, out)

    def test_the_note_is_printed_only_with_event_rate_rows(self):
        checks = [c for c in recording.selfcheck(self.case) if not c.name.startswith("event rate")]
        self.assertEqual(len(checks), 5)
        self.assertNotIn(recording.EVENT_RATE_NOTE, recording.format_table(checks))


class SelfcheckRows(SelfcheckBase):
    def with_replay(self, edit):
        diag = diagnostics(self.report)
        edit(diag)
        self.replay(diag)

    def test_focal_passes_at_0_05_pct_and_fails_at_0_2_pct(self):
        f = self.report["focal"]["fNorm"]
        for sign in (1, -1):
            self.with_replay(lambda d: d["focal"].update(f_norm=f * (1 + sign * 0.0005)))
            self.assertEqual(self.failing(), [], f"{sign:+d} 0.05 %")
            self.with_replay(lambda d: d["focal"].update(f_norm=f * (1 + sign * 0.002)))
            self.assertEqual(self.failing(), ["focal f_norm"], f"{sign:+d} 0.2 %")
        code, out, _ = self.selfcheck_cli()
        self.assertEqual(code, 1)
        self.assertIn("selfcheck FAIL: focal f_norm", out)

    def test_the_closure_residual_is_compared_in_degrees(self):
        post = self.report["loop"]["postDeg"]
        self.with_replay(lambda d: d["loop"].update(post_deg=post + 0.09))
        self.assertEqual(self.failing(), [])
        self.with_replay(lambda d: d["loop"].update(post_deg=post + 0.11))
        self.assertEqual(self.failing(), ["loop post_deg"])
        # And below the report: a replay far under the phone's residual is as far from it as one far over.
        self.rewrite_report(lambda r: r["loop"].update(postDeg=0.5))
        for delta, failing in ((-0.09, []), (-0.11, ["loop post_deg"]), (0.09, []), (0.11, ["loop post_deg"]),
                               (-0.45, ["loop post_deg"])):
            self.with_replay(lambda d: d["loop"].update(post_deg=0.5 + delta))
            self.assertEqual(self.failing(), failing, delta)

    def test_no_residual_on_either_side_agrees_and_on_one_side_does_not(self):
        self.rewrite_report(lambda r: r["loop"].update(closed=False, method=None, postDeg=None))
        self.with_replay(lambda d: d["loop"].update(closed=False, post_deg=None))
        self.assertEqual(self.failing(), [])
        self.with_replay(lambda d: d["loop"].update(post_deg=0.0))
        self.assertEqual(self.failing(), ["loop post_deg"])

    def test_the_predictor_mode_must_be_equal(self):
        self.with_replay(lambda d: d.update(predictor_mode="absolute-only"))
        self.assertEqual(self.failing(), ["predictor mode"])
        self.with_replay(lambda d: d.update(predictor_mode=None))
        self.assertEqual(self.failing(), ["predictor mode"])

    def hand_off(self, *changes):
        """The phone latched absolute-gyro at Begin and then made these (from, to) changes."""
        self.rewrite_report(lambda r: r["sensors"].update(
            modeAtBegin="absolute-gyro",
            modeChanges=[{"tMs": 2000 + 100 * i, "from": a, "to": b} for i, (a, b) in enumerate(changes)]))

    def changes_in_replay(self, *changes, mode="absolute-gyro"):
        self.with_replay(lambda d: d.update(
            predictor_mode=mode,
            mode_changes=[{"t_ms": 2500 + 7 * i, "from": a, "to": b} for i, (a, b) in enumerate(changes)]))

    def test_the_mode_at_begin_is_the_report_mode_and_the_final_mode_is_not(self):
        # Diagnostics.predictor_mode pairs with sensors.modeAtBegin (SPEC-v2 3.4), as mode_changes pairs
        # with modeChanges. A replay that latched the mode the phone ENDED in latched another mode.
        self.hand_off(("absolute-gyro", "relative"))
        self.changes_in_replay(("absolute-gyro", "relative"))
        self.assertEqual(self.failing(), [])                    # faithful; the change times differ and do not matter
        self.changes_in_replay(("absolute-gyro", "relative"), mode="relative")
        self.assertEqual(self.failing(), ["predictor mode"])
        # The same replay that also lost the handoff (a final mode with no changes) fails both rows.
        self.changes_in_replay(mode="relative")
        self.assertEqual(self.failing(), ["predictor mode", "predictor mode changes"])
        code, out, _ = self.selfcheck_cli()
        self.assertEqual(code, 1)
        self.assertIn("selfcheck FAIL: predictor mode, predictor mode changes", out)

    def test_the_handoffs_must_be_the_same(self):
        self.hand_off(("absolute-gyro", "relative"))
        self.changes_in_replay()                                # latched right, never handed off
        self.assertEqual(self.failing(), ["predictor mode changes"])
        self.changes_in_replay(("relative", "absolute-gyro"))   # the other way round
        self.assertEqual(self.failing(), ["predictor mode changes"])
        self.changes_in_replay(("absolute-gyro", "absolute-only"))
        self.assertEqual(self.failing(), ["predictor mode changes"])
        self.changes_in_replay(("absolute-gyro", "relative"), ("relative", "absolute-gyro"))      # one too many
        self.assertEqual(self.failing(), ["predictor mode changes"])
        # And a replay that hands off where the phone did not.
        self.hand_off()
        self.changes_in_replay(("absolute-gyro", "relative"))
        self.assertEqual(self.failing(), ["predictor mode changes"])
        # Several changes are compared in order.
        self.hand_off(("absolute-gyro", "relative"), ("relative", "absolute-gyro"))
        self.changes_in_replay(("absolute-gyro", "relative"), ("relative", "absolute-gyro"))
        self.assertEqual(self.failing(), [])
        self.changes_in_replay(("relative", "absolute-gyro"), ("absolute-gyro", "relative"))
        self.assertEqual(self.failing(), ["predictor mode changes"])

    def test_the_rows_show_the_modes_and_the_handoffs(self):
        self.hand_off(("absolute-gyro", "relative"))
        self.changes_in_replay()
        rows = self.rows()
        self.assertEqual((rows["predictor mode"].replay, rows["predictor mode"].report),
                         ("absolute-gyro", "absolute-gyro"))
        self.assertEqual((rows["predictor mode changes"].replay, rows["predictor mode changes"].report),
                         ("none", "absolute-gyro > relative"))

    def test_the_keyframe_count_is_compared_within_2_pct(self):
        self.rewrite_report(lambda r: r["keyframes"].update(total=100))
        for count, failing in ((98, []), (102, []), (97, ["keyframe count"]), (103, ["keyframe count"])):
            self.with_replay(lambda d: d.update(keyframes=[{"id": i} for i in range(count)]))
            self.assertEqual(self.failing(), failing, count)

    def drop_events(self, kind, count):
        path = self.case / "input" / "observations.jsonl"
        kept, dropped = [], 0
        for text in path.read_text(encoding="utf-8").splitlines():
            if json.loads(text)["kind"] == kind and dropped < count:
                dropped += 1
                continue
            kept.append(text)
        self.assertEqual(dropped, count)
        path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")

    def test_event_rates_are_compared_within_5_pct(self):
        self.drop_events("motion", 10)                    # 10 of 240 is 4.2 %
        self.assertEqual(self.failing(), [])
        self.drop_events("motion", 3)                     # 13 of 240 is 5.4 %
        self.assertEqual(self.failing(), ["event rate devicemotion"])

    def test_missing_motion_events_fail_the_event_rate_check_and_only_it(self):
        self.drop_events("motion", N_MOTION)
        self.assertEqual(self.failing(), ["event rate devicemotion"])
        rows = self.rows()
        self.assertEqual(rows["event rate devicemotion"].replay, "0.00 Hz")
        code, out, _ = self.selfcheck_cli()
        self.assertEqual(code, 1)
        self.assertIn("selfcheck FAIL: event rate devicemotion", out)

    def test_extra_events_the_report_never_saw_fail(self):
        self.rewrite_report(lambda r: r["sensors"]["events"][2].update(total=0))
        self.assertEqual(self.failing(), ["event rate devicemotion"])

    def test_a_legacy_orientation_line_counts_as_absolute(self):
        # 13.1: a legacy line has no `event` and means deviceorientationabsolute. Here every absolute line of the
        # recording is a legacy one, so the case holds 120 of them, 60 `deviceorientation` lines, and no
        # absolute line that names itself.
        legacy_lines = [{k: v for k, v in l.items() if k != "event"}
                        if l["kind"] == "orientation" and l["event"] == "deviceorientationabsolute" else l
                        for l in SYN.lines]
        self.assertEqual(sum(1 for l in legacy_lines if l["kind"] == "orientation" and "event" not in l), N_ABSOLUTE)
        path = self.root / "legacy.jsonl"
        path.write_text("".join(_line(l) for l in legacy_lines), encoding="utf-8", newline="\n")
        legacy = recording.import_recording(path, "legacy", self.root)
        self.replay(diagnostics(self.report), result=legacy / "result")
        rows = {c.name: c for c in recording.selfcheck(legacy)}
        self.assertEqual([name for name, c in rows.items() if not c.ok], [])
        # Counted where the report counts them: 120 absolute and 60 relative, in the same Hz as the faithful case.
        faithful = self.rows()
        for name in ("event rate deviceorientation", "event rate deviceorientationabsolute"):
            self.assertEqual(rows[name].replay, faithful[name].replay, name)
        self.assertNotEqual(rows["event rate deviceorientationabsolute"].replay, "0.00 Hz")

    def test_the_result_directory_can_be_named(self):
        elsewhere = self.root / "other-result"
        diag = diagnostics(self.report)
        diag["focal"]["f_norm"] *= 1.01
        self.replay(diag, result=elsewhere)
        self.assertEqual(self.failing(), [])                # the default result/ still agrees
        code, out, _ = self.selfcheck_cli("--result", elsewhere)
        self.assertEqual(code, 1)
        self.assertIn("selfcheck FAIL: focal f_norm", out)


class ExitCodes(SelfcheckBase):
    def test_a_match_exits_0(self):
        self.assertEqual(self.selfcheck_cli()[0], 0)

    def test_a_mismatch_exits_1(self):
        diag = diagnostics(self.report)
        diag["predictor_mode"] = "none"
        self.replay(diag)
        code, out, err = self.selfcheck_cli()
        self.assertEqual((code, err), (1, ""))
        self.assertIn("predictor mode", out)

    def test_it_exits_2_when_it_could_not_run(self):
        # No such case.
        code, _, err = run_cli("selfcheck", "absent", "--cases", self.root)
        self.assertEqual(code, 2)
        self.assertIn("no case directory", err)
        # No replay yet.
        shutil.rmtree(self.case / "result")
        code, _, err = self.selfcheck_cli()
        self.assertEqual(code, 2)
        self.assertIn("replay the case", err)
        # A diagnostics file that is not JSON, or lacks the field the check reads.
        self.replay({})
        code, _, err = self.selfcheck_cli()
        self.assertEqual(code, 2)
        self.assertIn("focal.f_norm", err)
        (self.case / "result" / "diagnostics.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.selfcheck_cli()[0], 2)
        # A recording that carried no report.
        self.replay(diagnostics(self.report))
        (self.case / "recording" / "report.json").unlink()
        code, _, err = self.selfcheck_cli()
        self.assertEqual(code, 2)
        self.assertIn("no report", err)

    def test_diagnostics_without_the_handoffs_could_not_be_checked(self):
        # A replay that does not say what it handed off is not the same as one that handed off nothing.
        diag = diagnostics(self.report)
        del diag["mode_changes"]
        self.replay(diag)
        code, _, err = self.selfcheck_cli()
        self.assertEqual(code, 2)
        self.assertIn("mode_changes", err)
        for broken in ("relative", [{"t_ms": 1, "from": "relative"}], [3]):
            diag["mode_changes"] = broken
            self.replay(diag)
            code, _, err = self.selfcheck_cli()
            self.assertEqual(code, 2, broken)
            self.assertIn("mode_changes", err)
        diag["mode_changes"] = []
        self.rewrite_report(lambda r: r["sensors"].update(modeChanges="none"))
        self.replay(diag)
        code, _, err = self.selfcheck_cli()
        self.assertEqual(code, 2)
        self.assertIn("sensors.modeChanges", err)

    def test_import_exits_0_and_2(self):
        out_root = self.root / "imported"
        code, out, err = run_cli("import-recording", SYN.path, "viacli", "--out", out_root)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"wrote {out_root / 'viacli'}", out)
        self.assertIn("never commit", out)
        code, _, err = run_cli("import-recording", self.root / "absent.jsonl", "viacli2", "--out", out_root)
        self.assertEqual(code, 2)
        self.assertIn("no recording at", err)
        code, _, err = run_cli("import-recording", SYN.path, "viacli", "--out", out_root)     # exists now
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_the_command_line_entry_point_carries_the_status(self):
        """``python -m sim`` itself, so a SystemExit(2) is the process's status and not just a return value."""
        run = subprocess.run([sys.executable, "-m", "sim", "selfcheck", "absent", "--cases", str(self.root)],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("no case directory", run.stderr)

    def test_the_whole_path_from_a_recording_to_a_verdict(self):
        out_root = self.root / "e2e"
        self.assertEqual(run_cli("import-recording", SYN.path, "e2e", "--out", out_root)[0], 0)
        case = out_root / "e2e"
        report = json.loads((case / "recording" / "report.json").read_text(encoding="utf-8"))
        (case / "result").mkdir()
        (case / "result" / "diagnostics.json").write_text(json.dumps(diagnostics(report)), encoding="utf-8")
        self.assertEqual(run_cli("selfcheck", "e2e", "--cases", out_root)[0], 0)


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
