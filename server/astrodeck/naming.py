# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""PRO-11 file-naming + folder templates — pure token engine (no I/O).

The persisted per-target counter lives in the Hub (CAPTURE_DIR-backed); this
module is import-light (stdlib only) so config.py can import DEFAULT_TEMPLATE /
validate_template without a cycle."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

CAPTURE_EXT = ".fits"
DEFAULT_TEMPLATE = (
    "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$")

# token name -> sanitize mode. "strict" mirrors the legacy filter sanitizer
# (hub.py:1686); "loose" mirrors the legacy target sanitizer (hub.py:1681).
KNOWN_TOKENS: dict[str, str] = {
    "TARGET": "loose", "FRAMETYPE": "loose", "FILTER": "strict",
    "DATE": "loose", "TIME": "loose", "DATETIME": "loose",
    "NIGHT": "loose", "FRAMENR": "loose",
    # Capture-settings tokens. All opt-in: a template that doesn't mention them
    # is byte-for-byte unchanged, and an unknown/absent value renders empty, so
    # the token simply drops out (existing engine behavior).
    # SENSORTEMP completes the set (UX #48): without it a dark library is
    # undistinguishable by filename — 300s g100 -10C and 600s g0 -20C both land
    # as ``Dark_<target>_0001.fits`` and only the header tells them apart.
    "GAIN": "loose", "EXPOSURE": "loose", "BINNING": "loose",
    "SENSORTEMP": "loose",
    # The mosaic panel's 1-based ``row-col`` label (``panel_label``; #189
    # U-08). Empty on every frame that is not a panel, so it drops out like
    # the capture tokens do. Strict, like FILTER: a label is digits and one
    # hyphen, and nothing else belongs in it.
    "PANEL": "strict",
}

_TOKEN_RE = re.compile(r"\$\$([A-Z0-9_]+)\$\$")


def token_names() -> tuple[str, ...]:
    return tuple(KNOWN_TOKENS)


def sanitize_component(value: str, mode: str = "loose") -> str:
    """One path component, sanitized to match the legacy builders byte-for-byte.
    strict: alnum + -_ , strip underscores (legacy filter). loose: alnum + -_ +
    space, strip whitespace (legacy target)."""
    v = value or ""
    if mode == "strict":
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in v).strip("_")
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in v).strip()


def _sub_piece(piece: str, fields: Mapping[str, str]) -> str:
    """Substitute $$TOKEN$$ in one underscore-delimited piece. Literal runs are
    loose-sanitized (neutralizes `..`/`:` injected as literals); each token value
    is sanitized by its own mode. Unknown tokens render empty."""
    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(piece):
        lit = piece[pos:m.start()]
        if lit:
            out.append(sanitize_component(lit, "loose"))
        mode = KNOWN_TOKENS.get(m.group(1))
        if mode is not None:
            out.append(sanitize_component(fields.get(m.group(1), ""), mode))
        pos = m.end()
    tail = piece[pos:]
    if tail:
        out.append(sanitize_component(tail, "loose"))
    return "".join(out)


def render_relative_path(template: str, fields: Mapping[str, str]) -> Path:
    """Render a template to a RELATIVE path (folders via '/', '.fits' appended).
    Split -> substitute -> drop-empty -> rejoin reproduces the legacy parts-list
    (an empty token drops out, so no double '_')."""
    segments: list[str] = []
    for seg in template.split("/"):
        pieces = [_sub_piece(p, fields) for p in seg.split("_")]
        joined = "_".join(p for p in pieces if p != "")
        if joined:
            segments.append(joined)
    if not segments:                      # validation prevents this; ultra-defensive
        segments = ["capture"]
    segments[-1] = segments[-1] + CAPTURE_EXT
    return Path(*segments)


def format_exposure_token(exposure_s: float | None) -> str:
    """``$$EXPOSURE$$`` value: integer seconds when whole (``300`` -> ``"300"``),
    otherwise a ``g``-format with ``.`` -> ``p`` (``1.5`` -> ``"1p5"``).

    A ``.`` inside a filename segment is legal but invites extension confusion
    (``M42_1.5_0001.fits``), and sub-second exposures are rare enough that the
    ``p`` idiom (borrowed from electronics part numbers) is the safer default.
    ``None``/non-numeric renders empty so the token drops out of the filename."""
    if exposure_s is None:
        return ""
    try:
        v = float(exposure_s)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
        return ""
    if v == int(v):
        return str(int(v))
    return f"{v:g}".replace(".", "p")


def format_sensor_temp_token(temp_c: float | None) -> str:
    """``$$SENSORTEMP$$`` value: signed integer Celsius with a ``C`` suffix and
    ``-`` for below zero (``-10.2`` -> ``"-10C"``, ``0.4`` -> ``"0C"``).

    Rounded to whole degrees on purpose: a cooled camera dithers by tenths, and
    ``-10p1C`` / ``-9p9C`` in the same dark set would split one library into two.
    ``-`` is legal in both sanitize modes, so the sign survives. ``None``/
    non-numeric renders empty so the token drops out of the filename."""
    if temp_c is None:
        return ""
    try:
        v = float(temp_c)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):    # NaN / inf
        return ""
    return f"{int(round(v))}C"


def capture_tokens(gain: int | None = None, exposure_s: float | None = None,
                   binning: int | None = None,
                   sensor_temp_c: float | None = None) -> dict[str, str]:
    """The capture-settings token values, formatted per the design (D4).
    ``None`` -> ``""`` so the token drops out and old templates are unaffected.

    Kept here (not at the capture seam) so the formatting is unit-testable
    without a Hub and every future caller renders these tokens identically."""
    return {
        "GAIN": "" if gain is None else str(int(gain)),
        "EXPOSURE": format_exposure_token(exposure_s),
        "BINNING": "" if binning is None else str(int(binning)),
        "SENSORTEMP": format_sensor_temp_token(sensor_temp_c),
    }


def panel_label(row: int | None, col: int | None) -> str:
    """A mosaic panel's label, ``"r-c"``, 1-based (mosaic spec 2.3), from the
    server's 0-based row and column.

    Row 1 is the north edge and col 1 the west edge at angle 0: server panel
    (0, 0) is the north-west corner (``framing.compute_mosaic``). The same
    label as the Plan's ``<name> r-c`` and the Target name ``to_plan`` builds
    (3.3: ``<row+1>-<col+1>``), so the file, the name and the chart agree.
    ``""`` when either index is ``None`` (not a panel), which drops the
    ``$$PANEL$$`` token and omits the PANEL card. A negative index is a
    caller's bug and raises ``ValueError``, rather than naming a panel
    ``0-1`` that no chart shows."""
    if row is None or col is None:
        return ""
    r, c = int(row), int(col)
    if r < 0 or c < 0:
        raise ValueError(f"panel row and col are 0-based, got {row!r}, {col!r}")
    return f"{r + 1}-{c + 1}"


def mosaic_label(name: str | None, group_id: str | None) -> str:
    """What a panel frame's MOSAIC card says: the group's name, or its id when
    the name is empty (#189 U-08). ``""`` for neither, which omits the card.

    A name with no printable ASCII in it at all counts as empty. A FITS header
    value is printable ASCII, so the writer folds everything else out
    (``fitsio.save_fits``), and such a name would fold to an empty card that
    groups nothing; the id is the one value every group has. A name with
    some ASCII in it is kept, and the writer folds the rest."""
    n = (name or "").strip()
    if not any(" " < ch <= "~" for ch in n):
        n = ""
    return n or (group_id or "").strip()


#: sample fields used by validate_template's dry render.
_SAMPLE = {"TARGET": "M42", "FRAMETYPE": "Light", "FILTER": "Ha",
           "DATE": "2026-07-23", "TIME": "213045",
           "DATETIME": "2026-07-23_213045", "NIGHT": "2026-07-23",
           "FRAMENR": "0001",
           **capture_tokens(gain=100, exposure_s=300.0, binning=1,
                            sensor_temp_c=-10.0)}


def validate_template(template: str) -> None:
    """Raise ValueError (route -> 422) on an unusable template."""
    t = (template or "").strip()
    if not t:
        raise ValueError("naming template must not be empty")
    if t.endswith("/"):
        # A literal trailing '/' means the raw last segment is empty by
        # construction (independent of any token substitution) — the engine's
        # drop-empty-segment step would silently repurpose the PRIOR segment as
        # the filename, changing the folder/filename split the user wrote.
        # Reject at save time rather than let that surprise reach _capture_path.
        raise ValueError("template must not end with '/' — the last segment is the filename")
    if "\\" in t or ":" in t:
        raise ValueError("use / for folders; backslash and ':' are not allowed")
    unknown = sorted({m for m in _TOKEN_RE.findall(t) if m not in KNOWN_TOKENS})
    if unknown:
        raise ValueError(
            "unknown token(s): " + ", ".join(f"$${u}$$" for u in unknown) +
            " — valid: " + ", ".join(f"$${k}$$" for k in KNOWN_TOKENS))
    rel = render_relative_path(t, _SAMPLE)
    if rel.is_absolute() or any(part in ("..", ".") for part in rel.parts):
        raise ValueError("template must not contain '..' or absolute segments")
    if not rel.name or rel.name == CAPTURE_EXT:
        raise ValueError("template must render a non-empty filename")
