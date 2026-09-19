"""Render one scored result as a single self-contained ``report.html``.

The report is the artefact a human reads instead of ``scores.json``, so it is
one file with nothing to fetch: the panoramas are inline base64 PNGs, the
plots are inline SVG, there is one style block, the font is whatever the
reader's system offers, and there is no script. A report that needs a network
or a bundler is not evidence anyone can keep beside a night's logs.

What it shows, in the order it shows it:

- the case id, the input hash and the app commit, so a report can be traced
  back to the exact input and build it describes;
- the gates, in the words PASS and FAIL rather than in symbols;
- the headline numbers behind each gate;
- every declared test obstacle scored against its own silhouette;
- every landmark, worst first, so an omission is at the top where it belongs;
- the boundary, truth against measured, with the unresolved azimuths shaded;
- the overlay error through time, against both of its gates;
- the result panorama, the ideal panorama, and the landmark error map.

Two of those are recomputed here rather than read from ``scores.json``,
because the scores carry summaries and a plot needs the series: the boundary
profiles come from ``truth/reference-horizon.json`` and ``result/horizon.json``,
and the overlay series is the angle between each event's ``forward`` and its
frame's truth ``forward``. The recomputation is presentation only; every
number in a table comes from ``scores.json`` exactly as the scorer wrote it.
"""

from __future__ import annotations

import base64
import html as html_module
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import truth as truth_module
from .geometry import angle_between
from .scene import load as load_scene

__all__ = ["render"]

#: The panorama mapping, from CONTRACT.md's "Result directory".
PANORAMA_WIDTH = 1080
PANORAMA_HEIGHT = 300
PANORAMA_ALT_TOP = 90.0
PANORAMA_ALT_SPAN = 100.0

#: What an unpainted raster cell is drawn as. Not black: a scanner that paints
#: black and one that paints nothing are different claims, and the report
#: should not make them look the same.
UNPAINTED = (28, 28, 34)

#: Error buckets for the landmark map, worst last. Each is (label, colour).
BUCKETS = {
    "good": ("error below 0.5 deg", (0, 200, 90)),
    "warn": ("error below 1.0 deg", (245, 180, 0)),
    "bad": ("error at or above 1.0 deg", (230, 60, 40)),
    "duplicate": ("duplicate", (180, 60, 220)),
    "omitted": ("omitted", (255, 255, 255)),
    "other": ("not observable, or a sliver", (130, 130, 130)),
}

#: The overlay gates, in degrees, drawn on the timeline.
GATE_SETTLED = 0.5
GATE_MOVING = 1.0


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _read_json(path: Path, default=None):
    if not Path(path).is_file():
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list:
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line
            in path.read_text(encoding="utf-8").splitlines() if line]


def _escape(value) -> str:
    return html_module.escape("" if value is None else str(value))


def _num(value, places: int = 3) -> str:
    """A number for a table cell. ``None`` prints as a dash, never as zero."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.{places}f}"


def _verdict(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _png_data_uri(image: np.ndarray) -> str:
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(image)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _load_rgba(path: Path):
    path = Path(path)
    if not path.is_file():
        return None
    with Image.open(path) as image:
        if "A" not in image.getbands():
            # An alpha-less panorama is scored as empty; show it as empty too,
            # rather than inventing the alpha the scorer refused to invent.
            return None
        return np.array(image.convert("RGBA"))


def _flatten(rgba) -> np.ndarray:
    """An RGBA raster over the unpainted colour, as RGB for drawing on."""
    if rgba is None:
        return np.tile(np.asarray(UNPAINTED, dtype=np.uint8),
                       (PANORAMA_HEIGHT, PANORAMA_WIDTH, 1))
    painted = (rgba[:, :, 3] == 255)[:, :, None]
    return np.where(painted, rgba[:, :, :3],
                    np.asarray(UNPAINTED, dtype=np.uint8)).astype(np.uint8)


def _raster_xy(az: float, alt: float, width: int, height: int):
    x = float(az) / 360.0 * width - 0.5
    y = (PANORAMA_ALT_TOP - float(alt)) / PANORAMA_ALT_SPAN * max(height - 1, 1)
    return x, y


# --------------------------------------------------------------------------
# The three rasters
# --------------------------------------------------------------------------


def _ideal_raster(case_dir: Path, supplied) -> np.ndarray:
    """The ideal panorama: the supplied one, or rendered from the truth.

    Rendering it costs a couple of seconds, so a caller that already has one
    (the tests, and anything that has run ``sim ideal``) passes the path.
    """
    if supplied is not None:
        if isinstance(supplied, (str, Path)):
            return _flatten(_load_rgba(Path(supplied)))
        return _flatten(np.asarray(supplied))
    truth_dir = Path(case_dir) / "truth"
    scene = load_scene(truth_dir / "scene.json")
    c_ref = np.asarray(_read_json(truth_dir / "reference.json")["c_ref"], dtype=np.float64)
    return _flatten(truth_module.ideal_panorama(scene, c_ref))


def _bucket(entry: dict) -> str:
    status = entry.get("status")
    if status == "duplicate":
        return "duplicate"
    if status == "omitted":
        return "omitted"
    if status != "found":
        return "other"
    error = entry.get("error_deg")
    if error is None:
        return "other"
    if error < 0.5:
        return "good"
    if error < 1.0:
        return "warn"
    return "bad"


def _error_map(result_rgb: np.ndarray, scores: dict) -> np.ndarray:
    """The result panorama with a ring at each landmark's TRUTH direction.

    At the truth direction, not the measured one: the question the map answers
    is "what is at the place this landmark should be", and a ring drawn where
    the blob actually landed would follow the error instead of showing it.
    """
    image = Image.fromarray(result_rgb.copy())
    draw = ImageDraw.Draw(image)
    width, height = image.size
    radius = 6
    for entry in scores.get("landmarks", {}).get("per_landmark", []):
        colour = BUCKETS[_bucket(entry)][1]
        x, y = _raster_xy(entry["truth"]["az"], entry["truth"]["alt"], width, height)
        for shift in (-width, 0, width):
            draw.ellipse([x + shift - radius, y - radius, x + shift + radius, y + radius],
                         outline=colour, width=2)
    return np.asarray(image)


# --------------------------------------------------------------------------
# The two plots
# --------------------------------------------------------------------------


def _runs(flags) -> list:
    """Contiguous runs of true in a boolean sequence, as (start, stop) pairs."""
    runs = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(flags)))
    return runs


def _polyline(points, colour: str, width: float = 1.0, dash: str = "") -> str:
    body = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<polyline fill="none" stroke="{colour}" stroke-width="{width}"'
            f'{extra} points="{body}" />')


def _horizon_svg(case_dir: Path, result_dir: Path) -> str:
    """Truth against measured boundary, azimuth 0..360, altitude -10..90."""
    left, right, top, bottom = 46.0, 12.0, 12.0, 28.0
    plot_width, plot_height = 1080.0 - left - right, 250.0 - top - bottom
    total_height = 250.0

    def x_of(az):
        return left + float(az) / 360.0 * plot_width

    def y_of(alt):
        return top + (90.0 - float(alt)) / 100.0 * plot_height

    parts = [f'<svg viewBox="0 0 1080 {total_height:.0f}" width="100%" '
             f'role="img" class="plot">']
    parts.append(f'<rect x="{left}" y="{top}" width="{plot_width}" '
                 f'height="{plot_height}" class="panel" />')

    reference = _read_json(Path(case_dir) / "truth" / "reference-horizon.json")
    measured = _read_json(Path(result_dir) / "horizon.json")

    if measured and measured.get("points"):
        bins = len(measured["points"])
        uncertain = set(int(i) for i in measured.get("uncertain_bins") or [])
        flags = [index in uncertain for index in range(bins)]
        for start, stop in _runs(flags):
            x0, x1 = x_of(start * 360.0 / bins), x_of(stop * 360.0 / bins)
            parts.append(f'<rect x="{x0:.1f}" y="{top}" width="{max(x1 - x0, 0.6):.1f}" '
                         f'height="{plot_height}" class="unresolved" />')

    for alt in (-10, 0, 30, 60, 90):
        y = y_of(alt)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
                     f'y2="{y:.1f}" class="grid" />')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="tick end">{alt}</text>')
    for az in (0, 90, 180, 270, 360):
        x = x_of(az)
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
                     f'y2="{top + plot_height}" class="grid" />')
        parts.append(f'<text x="{x:.1f}" y="{total_height - 8}" '
                     f'class="tick mid">{az}</text>')

    if reference:
        truth = np.clip(np.asarray(reference["alt_max"], dtype=np.float64), 0.0, 90.0)
        step = 360.0 / truth.size
        parts.append(_polyline(
            [(x_of((index + 0.5) * step), y_of(value)) for index, value in enumerate(truth)],
            "#7aa2ff", 1.2))
    if measured and measured.get("points"):
        bins = len(measured["points"])
        step = 360.0 / bins
        parts.append(_polyline(
            [(x_of((index + 0.5) * step), y_of(point["alt"]))
             for index, point in enumerate(measured["points"])],
            "#ff8a3d", 1.2))

    parts.append(f'<text x="{left}" y="{top - 2}" class="tick">'
                 'altitude (deg) against azimuth (deg): truth in blue, '
                 'measured in orange, unresolved azimuths shaded</text>')
    parts.append("</svg>")
    return "".join(parts)


def _overlay_series(case_dir: Path, result_dir: Path):
    """(t_ms, error_deg, moving) per event line that can be scored."""
    frames = {frame["frame_id"]: frame for frame
              in _read_jsonl(Path(case_dir) / "truth" / "trajectory.jsonl")}
    series = []
    for event in _read_jsonl(Path(result_dir) / "events.jsonl"):
        basis = event.get("basis")
        frame = frames.get(event.get("frame_id"))
        if basis is None or frame is None:
            continue
        series.append((float(event.get("t_ms", 0)),
                       angle_between(basis["forward"], frame["forward"]),
                       float(frame["angular_rate_deg_s"]) > 2.0))
    series.sort(key=lambda row: row[0])
    return series


def _overlay_svg(case_dir: Path, result_dir: Path) -> str:
    left, right, top, bottom = 46.0, 12.0, 12.0, 28.0
    plot_width, plot_height = 1080.0 - left - right, 200.0 - top - bottom
    total_height = 200.0
    series = _overlay_series(case_dir, result_dir)

    parts = [f'<svg viewBox="0 0 1080 {total_height:.0f}" width="100%" '
             f'role="img" class="plot">']
    parts.append(f'<rect x="{left}" y="{top}" width="{plot_width}" '
                 f'height="{plot_height}" class="panel" />')
    if not series:
        parts.append(f'<text x="{left + 8}" y="{top + 24}" class="tick">'
                     'no overlay samples in this result</text></svg>')
        return "".join(parts)

    t_max = max(row[0] for row in series) or 1.0
    error_max = max(max(row[1] for row in series), GATE_MOVING * 1.2)

    def x_of(t):
        return left + float(t) / t_max * plot_width

    def y_of(error):
        return top + plot_height - min(float(error) / error_max, 1.0) * plot_height

    for start, stop in _runs([row[2] for row in series]):
        x0 = x_of(series[start][0])
        x1 = x_of(series[min(stop, len(series) - 1)][0])
        parts.append(f'<rect x="{x0:.1f}" y="{top}" width="{max(x1 - x0, 0.6):.1f}" '
                     f'height="{plot_height}" class="moving" />')
    for gate, label in ((GATE_SETTLED, "settled gate 0.5"), (GATE_MOVING, "moving gate 1.0")):
        y = y_of(gate)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
                     f'y2="{y:.1f}" class="gate" />')
        parts.append(f'<text x="{left + plot_width - 4:.1f}" y="{y - 4:.1f}" '
                     f'class="tick end">{label}</text>')
    for fraction in (0.0, 0.5, 1.0):
        value = error_max * fraction
        y = y_of(value)
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="tick end">'
                     f'{value:.2f}</text>')

    parts.append(_polyline([(x_of(t), y_of(error)) for t, error, _ in series], "#ff8a3d", 1.0))
    parts.append(f'<text x="{left}" y="{top - 2}" class="tick">'
                 f'overlay error (deg, full scale {error_max:.2f}) against time '
                 f'(ms, full scale {t_max:.0f}); moving frames shaded</text>')
    parts.append(f'<text x="{left + plot_width / 2:.1f}" y="{total_height - 8}" '
                 f'class="tick mid">time (ms)</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# The tables
# --------------------------------------------------------------------------


def _table(headers, rows, classes="") -> str:
    head = "".join(f"<th>{_escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join(f'<td class="{cls}">{value}</td>' if cls else f"<td>{value}</td>"
                        for value, cls in row)
        body.append(f"<tr>{cells}</tr>")
    attribute = f' class="{classes}"' if classes else ""
    return (f"<table{attribute}><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _gates_table(gates: dict) -> str:
    rows = []
    for name, value in gates.items():
        if name == "pass":
            continue
        rows.append([(_escape(name), ""), (_verdict(value), _verdict(value).lower())])
    rows.append([("<strong>pass (every gate)</strong>", ""),
                 (f"<strong>{_verdict(gates['pass'])}</strong>",
                  _verdict(gates["pass"]).lower())])
    return _table(["gate", "verdict"], rows, "gates")


def _obstacles_table(horizon: dict) -> str:
    rows = []
    for obstacle in horizon.get("obstacles", []):
        verdict = "MISSED" if obstacle["missed"] else "found"
        rows.append([
            (_escape(obstacle["id"]), ""),
            (_num(obstacle["truth_alt_peak"], 2), ""),
            (_num(obstacle["deficit_median"], 2), ""),
            (_num(obstacle["deficit_p95"], 2), ""),
            (_num(obstacle["width_missed_deg"], 2), ""),
            (_num(obstacle["min_width_deg"], 2), ""),
            (verdict, "fail" if obstacle["missed"] else "pass"),
        ])
    return _table(["obstacle", "truth peak alt", "deficit median", "deficit p95",
                   "width missed (deg)", "min width (deg)", "verdict"], rows)


def _landmarks_table(landmarks: dict) -> str:
    def key(entry):
        # Worst first, and an entry with no error at all (an omission) is
        # worse than any measured error, not better than all of them.
        error = entry.get("error_deg")
        return (0 if error is None else 1, -(error or 0.0), entry["id"])

    rows = []
    for entry in sorted(landmarks.get("per_landmark", []), key=key):
        measured = entry.get("measured")
        rows.append([
            (_escape(entry["id"]), ""),
            (_escape(entry["status"]), "fail" if entry["status"] in ("omitted", "duplicate")
             else ""),
            ("yes" if entry["observable"] else "no", ""),
            (f'{_num(entry["truth"]["az"], 2)} / {_num(entry["truth"]["alt"], 2)}', ""),
            ("-" if measured is None
             else f'{_num(measured["az"], 2)} / {_num(measured["alt"], 2)}', ""),
            (_num(entry.get("error_deg"), 3), ""),
            (_num(entry.get("expected_px"), 1), ""),
        ])
    return _table(["landmark", "status", "observable", "truth az / alt",
                   "measured az / alt", "error (deg)", "expected cells"], rows)


def _summary_table(scores: dict) -> str:
    landmarks = scores["landmarks"]
    horizon = scores["horizon"]
    overlay = scores["overlay"]
    capture = scores["capture"]
    coverage = scores["coverage"]
    rows = [
        ("landmarks", f'expected {landmarks["expected"]}, found {landmarks["found"]}, '
                      f'omitted {len(landmarks["omitted"])}, '
                      f'duplicated {len(landmarks["duplicated"])}, '
                      f'slivers {landmarks["slivers"]}, spurious {landmarks["spurious"]}'),
        ("landmark error (deg)",
         f'median {_num(landmarks["errors_deg"]["median"])}, '
         f'p95 {_num(landmarks["errors_deg"]["p95"])}, '
         f'p99 {_num(landmarks["errors_deg"]["p99"])}, '
         f'max {_num(landmarks["errors_deg"]["max"])}'),
        ("boundary error (deg)",
         f'signed median {_num(horizon["signed_error_deg"]["median"])}, '
         f'p95 {_num(horizon["signed_error_deg"]["p95"])}, '
         f'max {_num(horizon["signed_error_deg"]["max"])}, '
         f'north offset {_num(horizon["north_offset_deg"], 1)}'),
        ("boundary area (sr)",
         f'false open {_num(horizon["false_open_sr"], 4)}, '
         f'false blocked {_num(horizon["false_blocked_sr"], 4)}, '
         f'unresolved {_num(horizon["unresolved_sr"], 4)} '
         f'(the sky above the horizontal is {2 * math.pi:.3f})'),
        ("missed obstructions",
         ", ".join(horizon["missed_obstructions"]) or "none"),
        ("overlay (deg)",
         f'samples {overlay["samples"]}, '
         f'missing fraction {_num(overlay["missing_fraction"], 4)}, '
         f'over gate {overlay["frames_over_gate"]}, '
         f'settled p95 {_num(overlay["settled"]["p95_deg"])} '
         f'max {_num(overlay["settled"]["max_deg"])}, '
         f'moving p95 {_num(overlay["moving"]["p95_deg"])} '
         f'max {_num(overlay["moving"]["max_deg"])}'),
        # Its own row rather than a word at the end of the overlay one: it is
        # what `no_duplicate_frames` fails on, and a gate whose evidence is
        # not on the page is a verdict the reader has to take on trust. A
        # dash is a `scores.json` written before the field existed, which is
        # not the same claim as a zero.
        ("duplicate frames",
         f'{_num(overlay.get("duplicate_frame_ids"), 0)} frame ids arrived '
         f'more than once (each scored on its first line only)'),
        ("capture",
         f'holds {capture["holds"]}, with capture {capture["holds_with_capture"]}, '
         f'latency p95 {_num(capture["latency_ms"]["p95"], 0)} ms, '
         f'max {_num(capture["latency_ms"]["max"], 0)} ms, '
         f'accepted frames {capture["accepted_frames"]}'),
        ("coverage",
         f'painted {_num(coverage["panorama_alpha_fraction"], 4)}, '
         f'of observable {_num(coverage["observable_fraction_covered"], 4)}, '
         f'cells {_num(coverage["cells_covered_fraction"], 4)}'),
        ("panorama", _escape(json.dumps(scores["panorama"]))),
    ]
    return _table(["measurement", "value"],
                  [[(_escape(name), ""), (value, "")] for name, value in rows])


def _legend() -> str:
    items = []
    for key in ("good", "warn", "bad", "duplicate", "omitted", "other"):
        label, (r, g, b) = BUCKETS[key]
        items.append(f'<li><span class="swatch" style="background:rgb({r},{g},{b})">'
                     f'</span>{_escape(label)}</li>')
    return f'<ul class="legend">{"".join(items)}</ul>'


STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
       margin: 0 auto; max-width: 1140px; padding: 24px 16px 64px;
       background: #14151a; color: #e8e8ea; line-height: 1.45; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 8px; text-transform: uppercase;
     letter-spacing: 0.08em; color: #9aa0ad; }
p.meta { margin: 0 0 4px; color: #9aa0ad; font-size: 13px; word-break: break-all; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 4px 0 8px; }
th, td { border-bottom: 1px solid #2b2d36; padding: 4px 8px; text-align: left;
         vertical-align: top; }
th { color: #9aa0ad; font-weight: 600; white-space: nowrap; }
td.pass { color: #3ec98a; font-weight: 600; }
td.fail { color: #ff6b57; font-weight: 600; }
table.gates { max-width: 420px; }
.verdict { font-size: 18px; font-weight: 700; }
.verdict.pass { color: #3ec98a; }
.verdict.fail { color: #ff6b57; }
.scroll { max-height: 420px; overflow: auto; border: 1px solid #2b2d36; }
img { width: 100%; image-rendering: pixelated; border: 1px solid #2b2d36;
      display: block; }
figure { margin: 0 0 16px; }
figcaption { color: #9aa0ad; font-size: 13px; padding: 4px 0; }
svg.plot { background: #1a1c22; border: 1px solid #2b2d36; display: block; }
svg .panel { fill: none; stroke: #2b2d36; }
svg .grid { stroke: #2b2d36; stroke-width: 1; }
svg .gate { stroke: #6b7080; stroke-width: 1; stroke-dasharray: 4 4; }
svg .unresolved { fill: #46324a; }
svg .moving { fill: #22242c; }
svg .tick { fill: #9aa0ad; font-size: 11px;
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
svg .end { text-anchor: end; }
svg .mid { text-anchor: middle; }
ul.legend { list-style: none; display: flex; flex-wrap: wrap; gap: 14px;
            padding: 0; margin: 4px 0 8px; font-size: 13px; color: #9aa0ad; }
ul.legend .swatch { display: inline-block; width: 11px; height: 11px;
                    margin-right: 6px; border-radius: 50%; }
"""


def render(case_dir, scores: dict, out_path, result_dir=None, ideal_panorama=None) -> Path:
    """Write ``out_path`` and return it.

    ``result_dir`` defaults to the directory ``out_path`` is written into,
    which is where ``sim score`` puts the report: beside ``scores.json``, in
    the result directory it describes. ``ideal_panorama`` is an array or a
    path for callers that already have one; without it the ideal panorama is
    rendered from the case's truth, which takes a few seconds.
    """
    case_dir = Path(case_dir)
    out_path = Path(out_path)
    result_dir = Path(result_dir) if result_dir is not None else out_path.parent

    result_rgb = _flatten(_load_rgba(result_dir / "panorama.png"))
    ideal_rgb = _ideal_raster(case_dir, ideal_panorama)
    error_map = _error_map(result_rgb, scores)

    gates = scores["gates"]
    title = f'{scores["case_id"]} photosphere report'
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_escape(title)}</title>",
        f"<style>{STYLE}</style>",
        "</head><body>",
        f"<h1>{_escape(scores['case_id'])}</h1>",
        f'<p class="verdict {_verdict(gates["pass"]).lower()}">'
        f'{_verdict(gates["pass"])}</p>',
        f'<p class="meta">input hash {_escape(scores["input_hash"])}</p>',
        f'<p class="meta">app commit {_escape(scores["app_commit"] or "unknown")} '
        f'&middot; profile {_escape(scores["profile"] or "unknown")} '
        f'&middot; schema {_escape(scores["schema"])}</p>',
        "<h2>Gates</h2>",
        _gates_table(gates),
        "<h2>Measurements</h2>",
        _summary_table(scores),
        "<h2>Test obstacles</h2>",
        _obstacles_table(scores["horizon"]),
        "<h2>Boundary</h2>",
        _horizon_svg(case_dir, result_dir),
        "<h2>Overlay error through time</h2>",
        _overlay_svg(case_dir, result_dir),
        "<h2>Landmarks, worst first</h2>",
        f'<div class="scroll">{_landmarks_table(scores["landmarks"])}</div>',
        "<h2>Rasters</h2>",
        _legend(),
        f'<figure><img src="{_png_data_uri(error_map)}" '
        f'alt="landmark error map"><figcaption>Landmark error map: the result '
        f'panorama with a ring at each landmark truth direction.'
        f'</figcaption></figure>',
        f'<figure><img src="{_png_data_uri(result_rgb)}" '
        f'alt="result panorama"><figcaption>Result panorama. Unpainted cells '
        f'are dark grey.</figcaption></figure>',
        f'<figure><img src="{_png_data_uri(ideal_rgb)}" '
        f'alt="ideal panorama"><figcaption>Ideal panorama, ray cast from the '
        f'reference position.</figcaption></figure>',
        "</body></html>",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8", newline="\n")
    return out_path
