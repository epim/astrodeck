"""Deterministic ids for what a flow compiles to (#189, spec 3.3, D4, D5).

WHY AN ID HAS TO BE A FUNCTION OF THE GRAPH
-------------------------------------------
The session ledger counts frames by STEP ID alone (``Session.accepted_by_step``),
and ``SequencePlan`` mints a fresh uuid4 for every target and step each time it
validates. So a flow compiled again on night two named steps the ledger had
never seen: nothing banked on night one counted, and a campaign could start
over but never continue. Here every id is a uuid5 of what the operator drew -
the flow, the node, the geometry and the exposure recipe - so compiling the
same flow twice names the same targets and the same steps.

WHAT IS IN AN ID, AND WHAT IS LEFT OUT, IS THE DESIGN. Each choice is a rule:

* the GEOMETRY is in (``geometry_key``). A target moved elsewhere is a
  different field, and crediting the old field's frames to it is the flaw D5
  removes. S1 has no anchor, so every move re-keys; S3 keys on the block's
  anchor instead and carries a nudge (``reframe_carry``). ``skip`` is never in
  it: skipping a panel must not re-key its neighbours.
* the exposure RECIPE is in: frame type, filter, exposure, gain, binning. Two
  recipes must never share a count (#77); 60 s frames do not stand in for
  180 s ones.
* the COUNT is out, and so are cycles and per-cycle. "Shoot more of the same"
  keeps what is banked.
* the STAGE node id is in, so two stages drawing the same recipe on one target
  keep two quotas, and the occurrence index ``n`` separates identical slots
  inside one stage ("L 60, L 60").
* a POOL member is keyed on its NAME, never its rank, so reordering the members
  box re-keys nothing.

THE RECIPE IS A STORED CONTRACT. A banked frame is filed under one of these
ids, so changing NS_FLOWS, a precision, a separator or the order of a field
orphans every campaign ever saved. ``tests/test_flows_identity.py`` pins each
of them with golden vectors that S3 builds on.

Pure: no devices, no config, no clock.
"""
from __future__ import annotations

import hashlib
import json
import uuid

#: The namespace of every flow id. FIXED FOREVER, for the reason above.
NS_FLOWS = uuid.UUID("d68016b1-09c2-455d-8573-3286b066e565")

#: A single TARGET is a 1x1 grid, keyed with the values a new TARGET node is
#: CREATED with (spec 3.1): one row, one column, 25% overlap and no camera
#: field recorded. S3 reads the same values from missing keys, so the key of a
#: flow saved before S3 does not move when S3 ships. The overlap is a FRACTION
#: here, as ``framing.compute_mosaic`` takes it: the node's ``overlap`` is a
#: percent, and S3 divides it by 100 before keying.
SINGLE_ROWS = 1
SINGLE_COLS = 1
SINGLE_OVERLAP = 0.25
SINGLE_FOV_X = 0.0
SINGLE_FOV_Y = 0.0

#: Decimal places each geometry field is keyed to (spec 3.3). RA is in HOURS,
#: so 1e-6 h is 0.054 arcsec; every angle is in degrees.
RA_PLACES = 6
DEC_PLACES = 6
OVERLAP_PLACES = 4
ROTATION_PLACES = 3
FOV_PLACES = 5


def fixed(value: float, places: int) -> str:
    """``value`` as a fixed-precision string, with negative zero folded.

    A STRING, not a float, because the key is hashed from text: a float that
    round-trips through JSON, a browser or another parser may print with a
    different number of digits, and a rounded string cannot. The fold matters
    because -0.0 prints as "-0.000000", and so does any negative too small to
    survive the rounding; both are zero, and a key that told them apart would
    re-key a target on the equator for a sign bit."""
    text = f"{float(value):.{places}f}"
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    return text


def _wrapped(value: float, period: float, places: int) -> str:
    """``fixed`` for a quantity that wraps (RA in hours, a position angle).

    Reduced into [0, period) first, and folded again AFTER rounding: 23.9999999 h
    rounds to "24.000000", which is 0 h, and one field must not have two keys
    either side of the seam."""
    text = fixed(float(value) % period, places)
    if float(text) >= period:
        text = fixed(0.0, places)
    return text


def canonical_geometry(ra_hours: float, dec_deg: float,
                       rotation_deg: float | None, *,
                       rows: int = SINGLE_ROWS, cols: int = SINGLE_COLS,
                       overlap: float = SINGLE_OVERLAP,
                       fov_x: float = SINGLE_FOV_X,
                       fov_y: float = SINGLE_FOV_Y) -> str:
    """The exact text ``geometry_key`` hashes: canonical JSON, sorted keys, no
    whitespace, every float a ``fixed`` string and ``rows``/``cols`` integers.

    ``rotation_deg`` None or NEGATIVE is JSON null, "any angle". That is the
    reading ``to_plan`` gives the field (#150): -1 and -5 are one geometry, and
    0 is north up, a real angle that keys apart from no angle at all. A set
    angle is reduced modulo 360."""
    angle = (None if rotation_deg is None or float(rotation_deg) < 0
             else _wrapped(rotation_deg, 360.0, ROTATION_PLACES))
    return json.dumps({
        "ra_hours": _wrapped(ra_hours, 24.0, RA_PLACES),
        "dec_deg": fixed(dec_deg, DEC_PLACES),
        "rows": int(rows),
        "cols": int(cols),
        "overlap": fixed(overlap, OVERLAP_PLACES),
        "rotation_deg": angle,
        "fov_x": fixed(fov_x, FOV_PLACES),
        "fov_y": fixed(fov_y, FOV_PLACES),
    }, sort_keys=True, separators=(",", ":"))


def geometry_key(ra_hours: float, dec_deg: float,
                 rotation_deg: float | None, *,
                 rows: int = SINGLE_ROWS, cols: int = SINGLE_COLS,
                 overlap: float = SINGLE_OVERLAP,
                 fov_x: float = SINGLE_FOV_X,
                 fov_y: float = SINGLE_FOV_Y) -> str:
    """The first 16 hex characters of sha256 over ``canonical_geometry``."""
    text = canonical_geometry(ra_hours, dec_deg, rotation_deg, rows=rows,
                              cols=cols, overlap=overlap, fov_x=fov_x,
                              fov_y=fov_y)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


def _id(name: str) -> str:
    return uuid.uuid5(NS_FLOWS, name).hex


def group_id(flow_id: str, node_id: str, geometry_key: str) -> str:
    """One TARGET block at one geometry. Every panel's id hangs off this."""
    return _id(f"{flow_id}/{node_id}/{geometry_key}")


def target_id(group_id: str, row: int = 0, col: int = 0) -> str:
    """One panel of a block, 0-based. A single target is its block's r0c0, so
    single targets gain continuity through the same path mosaics use."""
    return _id(f"{group_id}/r{int(row)}c{int(col)}")


def member_key(name: str, occurrence: int = 0) -> str:
    """A pool member's name as keyed: the first copy as is, the second copy on
    with ``#<occurrence>`` (0-based, so the second copy is "#1"). The first
    copy carrying no suffix is what lets a second copy be added later without
    re-keying the one with frames banked."""
    return name if occurrence == 0 else f"{name}#{occurrence}"


def member_id(flow_id: str, node_id: str, name: str,
              occurrence: int = 0) -> str:
    """A pool member's target id: keyed on its name, never its rank."""
    return _id(f"{flow_id}/{node_id}/member/{member_key(name, occurrence)}")


def _g(value) -> str:
    """A number spelled ``{:g}``, so 180, 180.0 and "180" are one exposure. A
    value that is not a number is kept as its text; the plan's own validation
    refuses it later, and this must not raise a different error first."""
    try:
        text = format(float(value), "g")
    except (TypeError, ValueError):
        return str(value).strip()
    return "0" if text == "-0" else text


def step_signature(*, frame_type, filter, exposure_s, gain, binning) -> str:
    """The recipe part of a step id, and what "identical" means when ``n`` is
    counted. No filter (None) is spelled empty: the engine treats a falsy
    filter as "leave the wheel where it is", so None and "" are one recipe."""
    return "/".join((str(frame_type), "" if filter is None else str(filter),
                     _g(exposure_s), _g(gain), _g(binning)))


def step_id(target_id: str, stage_node_id: str, *, frame_type, filter,
            exposure_s, gain, binning, n: int = 0) -> str:
    """One step of one target. ``n`` is the occurrence index of this recipe
    among identical ones inside the same stage: almost always 0, and 1 for the
    second of two "L 60" slots in one cycle."""
    signature = step_signature(frame_type=frame_type, filter=filter,
                               exposure_s=exposure_s, gain=gain,
                               binning=binning)
    return _id(f"{target_id}/{stage_node_id}/{signature}/{int(n)}")
