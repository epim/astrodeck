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
  removes. ``skip`` is never in it: skipping a panel must not re-key its
  neighbours.
* ...but the geometry keyed is the block's ANCHOR, not the geometry it is
  drawn at now (#189 Revision 2 ruling 3). The anchor is the geometry the
  counts started at; the save keeps it while ``framing.reframe_carry`` says a
  re-frame moved every panel corner less than half the overlap, so a nudge
  keeps the counts, and replaces it otherwise. S1 had no anchor, so every
  move re-keyed. The anchor is STORED AS THE TEXT THE KEY HASHES
  (``anchor_for``), so the key of a block's first anchor is S1's key of the
  same geometry, byte for byte, and no id moves when S3 ships. One addition
  is not keyed: a single panel anchored with no camera field that is saved
  with one, and nothing else changed, RECORDS the field beside the keyed
  none (``RECORDED_FIELDS``, ``complete_anchor``; #351, S4 orchestrator
  ruling 4), so recording the camera's field restarts no campaign.
* ...unless the TARGET has a NAME and no typed coordinates (``target_key``).
  Its coordinates are then the catalogue's answer at the compile's ``when``,
  and for a planet, the Moon or a comet that answer moves by the hour (a
  deep-sky name resolves to a fixed J2000 row, which does not), so keyed on
  it the ids moved with it and the campaign restarted on every compile. Such
  a TARGET is keyed on the catalogue's CANONICAL IDENTITY for the name
  instead (``name_key``, #229): the row's catalogue id for a fixed row, the
  canonical body name for a moving one. Not on the name as typed, because
  "M 31", "M31" and "m31" are one object and a spelling edit must not
  restart its campaign. The caller resolves the name
  (``tonight.resolve_target``) and passes the identity in, so this module
  stays pure. The angle and the grid are still in the key, and a namespace
  keeps a name key from ever equalling a geometry key.
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
of them with golden vectors that S3 builds on. The anchor is stored as well,
in the node's ``frameAnchor``, so ``anchor_geometry`` must go on reading every
anchor ever written; the key is made from the geometry the text holds, not
from its bytes, so its spelling can change without orphaning anything.

Pure: no devices, no config, no clock.
"""
from __future__ import annotations

import hashlib
import json
import math
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

#: Decimal places a step's exposure (seconds), gain and binning are spelled to
#: in its id: the exposure to the MICROSECOND (#189, H2), and gain and binning
#: share the same six places because all three go through ``_spelled``. See
#: ``_spelled`` for why three places were not enough.
STEP_PLACES = 6

#: What a key made from a TARGET's NAME starts with. A geometry key is 16
#: lowercase hex characters and nothing else, so a key that starts with this
#: can never equal one, whatever the name is: the two kinds are disjoint by
#: construction, as ``engine._focus_group_key`` keeps group names apart from
#: target ids. The name is hashed as well, so a collision without the prefix
#: would take a 64-bit coincidence; with it there is nothing to coincide.
NAME_KEY_PREFIX = "name:"

#: The fields every anchor holds whatever its kind: what the field LOOKS like,
#: as ``_shape`` spells them. A typed block's anchor adds WHERE it is
#: (``ra_hours``, ``dec_deg``); a name-keyed block's adds WHAT it is
#: (``name``, the catalogue's canonical identity) and no position, because
#: that is the catalogue's answer and a planet's moves by the hour.
SHAPE_FIELDS = ("rows", "cols", "overlap", "rotation_deg", "fov_x", "fov_y")
_GEOMETRY_ANCHOR = frozenset(("ra_hours", "dec_deg", *SHAPE_FIELDS))
_NAME_ANCHOR = frozenset(("name", *SHAPE_FIELDS))

#: A camera field an anchor RECORDS without keying it (#351, S4 orchestrator
#: ruling 4). A single panel anchored with no field (S1's shape, and every
#: block framed before MATCH CAMERA) that is then saved with a field, and
#: nothing else changed, keeps its key: its ``fov_x`` and ``fov_y`` stay the
#: none they were keyed with, and the field goes beside them here
#: (``complete_anchor``). ``anchor_key`` never reads these, so the ids stay;
#: ``anchor_geometry`` reads them, and ``framing.reframe_carry`` lays the
#: anchor out with them and takes its threshold from them, so a later move is
#: judged against the field the camera really has. Written only beside a
#: keyed field of none: an anchor that keys a field has no other to record.
RECORDED_FIELDS = ("recorded_fov_x", "recorded_fov_y")


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
    return json.dumps({
        "ra_hours": _wrapped(ra_hours, 24.0, RA_PLACES),
        "dec_deg": fixed(dec_deg, DEC_PLACES),
        **_shape(rotation_deg, rows, cols, overlap, fov_x, fov_y),
    }, sort_keys=True, separators=(",", ":"))


def _shape(rotation_deg, rows, cols, overlap, fov_x, fov_y) -> dict:
    """The fields of a key that say what the field LOOKS like rather than
    where it is: the angle and the grid. One copy, so a geometry key and a
    name key can never spell the same angle two ways."""
    angle = (None if rotation_deg is None or float(rotation_deg) < 0
             else _wrapped(rotation_deg, 360.0, ROTATION_PLACES))
    return {"rows": int(rows), "cols": int(cols),
            "overlap": fixed(overlap, OVERLAP_PLACES),
            "rotation_deg": angle,
            "fov_x": fixed(fov_x, FOV_PLACES),
            "fov_y": fixed(fov_y, FOV_PLACES)}


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


def canonical_name(name: str, rotation_deg: float | None, *,
                   rows: int = SINGLE_ROWS, cols: int = SINGLE_COLS,
                   overlap: float = SINGLE_OVERLAP,
                   fov_x: float = SINGLE_FOV_X,
                   fov_y: float = SINGLE_FOV_Y) -> str:
    """The exact text ``name_key`` hashes: ``canonical_geometry`` with
    ``name`` where ``ra_hours`` and ``dec_deg`` were, and the angle and grid
    spelled by the same ``_shape``. ``json.dumps`` escapes every non-ASCII
    character, so the text is ASCII and a name in any script (or a typed
    position's id, which carries a degree sign) keys the same on every
    machine. ``name`` is taken as given: for a TARGET it is the catalogue's
    canonical identity, which ``target_key`` is handed by its caller, never
    the name the operator typed."""
    return json.dumps({
        "name": str(name),
        **_shape(rotation_deg, rows, cols, overlap, fov_x, fov_y),
    }, sort_keys=True, separators=(",", ":"))


def name_key(name: str, rotation_deg: float | None, *,
             rows: int = SINGLE_ROWS, cols: int = SINGLE_COLS,
             overlap: float = SINGLE_OVERLAP,
             fov_x: float = SINGLE_FOV_X,
             fov_y: float = SINGLE_FOV_Y) -> str:
    """``NAME_KEY_PREFIX`` and the first 16 hex characters of sha256 over
    ``canonical_name``: where a geometry key would go, for a TARGET whose
    field is a name the catalogue resolves (see ``target_key``)."""
    text = canonical_name(name, rotation_deg, rows=rows, cols=cols,
                          overlap=overlap, fov_x=fov_x, fov_y=fov_y)
    digest = hashlib.sha256(text.encode("ascii")).hexdigest()[:16]
    return NAME_KEY_PREFIX + digest


def typed_coordinates(entry: dict) -> bool:
    """True when a compiled TARGET entry carries BOTH an RA and a Dec: the
    operator typed the field. Otherwise ``to_plan._coords`` resolves the
    entry's name against the catalogue.

    One definition, read by ``_coords`` and ``target_key`` alike: an entry the
    one resolves by name and the other keys on its geometry would key a moving
    answer, which is the fault ``target_key`` exists to remove. Truthiness,
    not parseability, is the test on both sides - a typed Dec that does not
    parse drops the entry in ``_coords`` rather than falling back to the
    name, so it is never keyed at all.

    TRIMMED FIRST (#387). A field that holds only whitespace is blank, as an
    empty one is, so the block is placed by its name. Read raw, an RA of
    three spaces was typed: ``_coords`` could not parse it and dropped the
    block, while the Target modal, whose mirror (``framingModel.ts``
    ``typedCoordinates``) trims, showed it placed by its name and previewed
    a placement the run would never make. Both readings are graded against
    ``tests/fixtures/typed_coordinates_cases.json``. A value that is not
    text is read as it always was: a falsy one (an RA of the number 0) is
    blank, and any other is its ``str``."""
    return _typed(entry.get("ra")) and _typed(entry.get("dec"))


def _typed(value) -> bool:
    """One coordinate field holds something typed: text that is not blank
    once trimmed (``str.strip``, Python's whitespace), or a truthy value
    that is not text, which reads as its ``str``."""
    return bool(str(value).strip()) if value else False


def _identity_of(entry: dict, canonical: str | None) -> str | None:
    """The canonical identity a TARGET is keyed on, or None when it is keyed
    on its geometry: the one reading ``target_key`` and ``anchor_for`` share,
    so a block's anchor and its key can never disagree on which kind it is.

    REFUSES TO GUESS. A name-keyed entry with no ``canonical`` raises
    ``ValueError`` rather than falling back to the typed name or to the
    geometry: either fallback mints an id the other side of the seam does not
    (``to_plan`` drops an entry the catalogue cannot resolve before keying it,
    and ``progress._single`` does not ask), so a caller that forgot to resolve
    would name steps no ledger holds."""
    name = str(entry.get("name") or "").strip()
    if not name or typed_coordinates(entry):
        return None
    # Keyed AS GIVEN: the resolver hands back a trimmed catalogue id, and a
    # second trim here would hide a caller that asked the catalogue about an
    # untrimmed name. Blank is no identity at all.
    if canonical is None or not str(canonical).strip():
        raise ValueError(
            f"TARGET {name!r} has no typed coordinates, so it is keyed on "
            f"the catalogue's canonical identity for its name, and none "
            f"was given: resolve the name (tonight.resolve_target) first")
    return str(canonical)


def anchor_for(entry: dict, ra_hours: float, dec_deg: float,
               rotation_deg: float | None, *, canonical: str | None,
               rows: int = SINGLE_ROWS, cols: int = SINGLE_COLS,
               overlap: float = SINGLE_OVERLAP,
               fov_x: float = SINGLE_FOV_X,
               fov_y: float = SINGLE_FOV_Y) -> str:
    """The anchor of a TARGET block as it is drawn NOW: the text the save
    writes as a block's first anchor, and as its new one when a re-frame
    restarts the counts (spec 3.3, ruling 3).

    IT IS THE TEXT THE KEY HASHES. A typed block's anchor is
    ``canonical_geometry``; a name-keyed block's is ``canonical_name`` of the
    catalogue's canonical identity, its grid and its angle, with no
    coordinates. So ``anchor_key`` of a first anchor is the key the block had
    with no anchor at all, which is S1's key: writing it moves no id. Which
    kind is decided by ``_identity_of``, as ``target_key`` decides it, from
    the entry's ``name``, ``ra`` and ``dec`` (a node's params carry the same
    three, so the save can pass those).

    ``rotation_deg`` is the angle the grid is LAID OUT at, None or negative
    for any angle, and the grid is the block's (overlap as a FRACTION, fields
    in bin-1 degrees). For a "camera fixed at" block that is the planned
    angle, which the run never commands but which placed every panel."""
    shape = dict(rows=rows, cols=cols, overlap=overlap, fov_x=fov_x,
                 fov_y=fov_y)
    name = _identity_of(entry, canonical)
    if name is not None:
        return canonical_name(name, rotation_deg, **shape)
    return canonical_geometry(ra_hours, dec_deg, rotation_deg, **shape)


def _anchor_number(held: dict, key: str) -> float:
    """One number of an anchor. ``fixed`` writes each as a string, and a
    plain JSON number is read too; a bool, a non-number or a non-finite
    value is not a geometry."""
    value = held[key]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"anchor field {key!r} is not a number: {value!r}")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(
            f"anchor field {key!r} is not a number: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"anchor field {key!r} is not finite: {value!r}")
    return number


def anchor_geometry(anchor: str | None) -> dict | None:
    """The geometry an anchor holds, read back from its text.

    A typed anchor gives ``{ra_hours, dec_deg, rotation_deg, rows, cols,
    overlap, fov_x, fov_y}``, a named one ``{name, rotation_deg, rows, cols,
    overlap, fov_x, fov_y}``: numbers as floats, the grid as integers, and
    "any angle" as None. That is the shape ``framing.reframe_carry`` takes.
    A COMPLETED anchor (``complete_anchor``, ruling 4) adds
    ``recorded_fov_x`` and ``recorded_fov_y``, the field it records beside
    the keyed field of none; ``fov_x`` and ``fov_y`` stay what is keyed.

    Blank (or None) is NO ANCHOR YET, answered as None: a block saved before
    S3, or never saved, whose current geometry is its anchor (spec 3.3).

    ANYTHING ELSE THAT IS NOT AN ANCHOR IS REFUSED with ``ValueError``,
    naming what is wrong: text that is not JSON, a missing or an unknown
    field (a ``skip`` in an anchor would put skip back into the identity),
    a grid of fewer than one row, a declination off the sphere. The server
    alone writes anchors, so a malformed one is a corrupted file, and a key
    guessed from it would file frames under ids nothing else can find.
    Formatting is not checked: the key is made from the geometry
    (``anchor_key``), so key order and number spelling do not matter."""
    if anchor is None or not str(anchor).strip():
        return None
    try:
        held = json.loads(anchor)
    except (TypeError, ValueError) as e:
        raise ValueError(f"an anchor is canonical JSON, and this is not "
                         f"JSON ({e}): {anchor!r}") from None
    if not isinstance(held, dict):
        raise ValueError(f"an anchor is a JSON object, not {anchor!r}")
    recorded = [k for k in RECORDED_FIELDS if k in held]
    if recorded and len(recorded) != len(RECORDED_FIELDS):
        raise ValueError(f"a recorded field is a pair, {list(RECORDED_FIELDS)}"
                         f", and this anchor holds only {recorded}")
    fields = frozenset(held) - frozenset(RECORDED_FIELDS)
    if fields not in (_GEOMETRY_ANCHOR, _NAME_ANCHOR):
        raise ValueError(
            f"not an anchor: it holds {sorted(fields)}, and an anchor holds "
            f"{sorted(_GEOMETRY_ANCHOR)} or {sorted(_NAME_ANCHOR)}")
    out: dict = {}
    if fields == _NAME_ANCHOR:
        name = held["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"a named anchor names no object: {name!r}")
        out["name"] = name
    else:
        out["ra_hours"] = _anchor_number(held, "ra_hours")
        out["dec_deg"] = _anchor_number(held, "dec_deg")
        if not -90.0 <= out["dec_deg"] <= 90.0:
            raise ValueError(f"anchor declination {out['dec_deg']!r} is off "
                             f"the sphere")
    rotation = held["rotation_deg"]
    out["rotation_deg"] = (None if rotation is None
                           else _anchor_number(held, "rotation_deg"))
    for key in ("rows", "cols"):
        count = held[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"anchor {key} must be a whole number of at "
                             f"least 1, not {count!r}")
        out[key] = count
    out["overlap"] = _anchor_number(held, "overlap")
    if not 0.0 <= out["overlap"] < 1.0:
        raise ValueError(f"anchor overlap is a fraction in [0, 1), not "
                         f"{out['overlap']!r}")
    for key in ("fov_x", "fov_y"):
        out[key] = _anchor_number(held, key)
        if out[key] < 0.0:
            raise ValueError(f"anchor {key} is negative: {out[key]!r}")
    if recorded:
        # Beside a keyed field of none only: the one state ``complete_anchor``
        # writes. Beside a keyed field it would be a second, contradicting
        # field for ``reframe_carry`` to lay the anchor out with.
        if (out["fov_x"], out["fov_y"]) != (0.0, 0.0):
            raise ValueError(
                f"an anchor records a field only beside a keyed field of "
                f"none, and this one keys {out['fov_x']!r} x "
                f"{out['fov_y']!r}")
        for key in RECORDED_FIELDS:
            out[key] = _anchor_number(held, key)
            if out[key] <= 0.0:
                raise ValueError(f"anchor {key} records no field: "
                                 f"{out[key]!r}")
    return out


def _key_of(held: dict) -> str:
    """``anchor_key`` of a geometry in ``anchor_geometry``'s shape: the
    anchor's own grid, angle and KEYED field, never a recorded one."""
    shape = {k: held[k] for k in ("rows", "cols", "overlap", "fov_x",
                                  "fov_y")}
    if "name" in held:
        return name_key(held["name"], held["rotation_deg"], **shape)
    return geometry_key(held["ra_hours"], held["dec_deg"],
                        held["rotation_deg"], **shape)


def anchor_key(anchor: str) -> str:
    """The key a block's ids hang off, made from its anchor: ``geometry_key``
    of a typed anchor's geometry, ``name_key`` of a named one's identity,
    each with the anchor's own grid and angle. Made from the geometry the
    text holds, re-spelt canonically, so it equals the hash of the text
    whenever the text is canonical, which is how the server writes it.

    A RECORDED FIELD IS NOT KEYED (ruling 4): the key of a completed anchor
    is the key it had before the field was recorded, which is what keeps the
    ids of a block whose camera field was recorded and nothing else moved.

    A blank anchor has no key and raises ``ValueError``, as a malformed one
    does (``anchor_geometry``); ``target_key`` is what falls back to the
    current geometry when there is no anchor."""
    held = anchor_geometry(anchor)
    if held is None:
        raise ValueError("a blank anchor has no key: key the block's current "
                         "geometry (target_key does)")
    return _key_of(held)


def completes(held: dict, drawn: dict) -> bool:
    """Whether ``drawn``, a block's geometry now, COMPLETES the anchor
    ``held`` (#351, S4 orchestrator ruling 4): both in ``anchor_geometry``'s
    shape (a frame the caller has placed may carry coordinates beside a
    name, and they are not read).

    True when ALL that changed is a camera field recorded where the anchor
    had none: ``held`` is a single panel that keys no field and records
    none, ``drawn`` has a finite, positive field, and ``drawn`` with that
    field set aside keys exactly as ``held`` does (the same object or
    centre, angle, grid and overlap). The caller then keeps the anchor's key
    and records the field (``complete_anchor``), and later moves are judged
    against it as usual.

    ANYTHING ELSE CHANGED WITH IT, AND IT DOES NOT COMPLETE. A nudge made in
    the same save is judged against the anchor as it was, whose threshold
    is 0: a threshold read from the field the same edit records would let
    that edit set its own allowance (spec 3.3, "a threshold read from the
    new grid would let an edit raise its own allowance").

    A SINGLE PANEL ONLY. Its pointing is its centre whatever the field is.
    A grid's panels are tiled from the field, so recording one moves every
    panel off the centre it would have been shot at; M1 refuses a grid with
    no field, so none was, and it re-anchors as before."""
    if any(k in held or k in drawn for k in RECORDED_FIELDS):
        return False
    if (float(held["fov_x"]), float(held["fov_y"])) != (0.0, 0.0):
        return False
    field = (float(drawn["fov_x"]), float(drawn["fov_y"]))
    if not all(math.isfinite(v) and v > 0.0 for v in field):
        return False
    if (int(held["rows"]), int(held["cols"])) != (SINGLE_ROWS, SINGLE_COLS):
        return False
    return _key_of(held) == _key_of({**drawn, "fov_x": 0.0, "fov_y": 0.0})


def complete_anchor(anchor: str, now: str) -> str | None:
    """``anchor`` completed with the camera field ``now`` records, when
    ``now`` completes it (``completes``), else None. Both are anchor texts:
    the stored one and the one ``anchor_for`` gives the block as drawn.

    THE KEYED TEXT IS KEPT AS IT IS STORED, and the field is written beside
    it as ``RECORDED_FIELDS``, spelt as ``fov_x`` is (``FOV_PLACES``). So
    ``anchor_key`` of the answer is ``anchor_key(anchor)``: rewriting
    ``fov_x`` and ``fov_y`` instead would re-key the block, and restart the
    counts this exists to keep."""
    held, drawn = anchor_geometry(anchor), anchor_geometry(now)
    if held is None or drawn is None or not completes(held, drawn):
        return None
    stored = json.loads(anchor)
    stored["recorded_fov_x"] = fixed(drawn["fov_x"], FOV_PLACES)
    stored["recorded_fov_y"] = fixed(drawn["fov_y"], FOV_PLACES)
    return json.dumps(stored, sort_keys=True, separators=(",", ":"))


def target_key(entry: dict, ra_hours: float, dec_deg: float,
               rotation_deg: float | None, *, canonical: str | None,
               anchor: str | None = None,
               rows: int = SINGLE_ROWS, cols: int = SINGLE_COLS,
               overlap: float = SINGLE_OVERLAP,
               fov_x: float = SINGLE_FOV_X,
               fov_y: float = SINGLE_FOV_Y) -> str:
    """The key a TARGET block's group id is made from, decided from its
    compiled ``entry``. The ONE place this is decided: ``to_plan._identify``
    mints the id through it and ``progress._single`` finds the id again
    through it, and two copies of the rule would let a card read "nothing
    banked" against the ledger of a live campaign.

    THE ANCHOR FIRST (#189 Revision 2 ruling 3). With an ``anchor``, the
    block's stored ``frameAnchor``, the key is ``anchor_key`` of it: the
    geometry the counts started at, which the save keeps while a re-frame
    moves every panel corner less than half the overlap. That is what lets a
    nudge keep the ids and the banked frames. The anchor is used only when it
    is THIS block's: a named block's must name the identity its name resolves
    to now, and a typed block's must hold coordinates. Any other anchor is
    stale (the save re-anchors on both changes), and keying on it would
    credit one object's frames to another, the flaw D5 removes; the block is
    then keyed on what it is drawn as, and CONTINUE's dropped-steps refusal
    guards the ledger.

    With no anchor (blank or None: an unsaved preview, a block saved before
    S3) the CURRENT geometry is the anchor, keyed with the grid passed in:
    typed coordinates on ``geometry_key`` of the field, as S1 keyed every
    TARGET, and ``canonical`` not read; a name and no typed coordinates on
    ``name_key`` of ``canonical``, the catalogue's canonical identity for the
    name (``tonight.resolve_target``: the catalogue id of a fixed row, the
    canonical body name of a moving one), which the caller resolved (#229).
    The coordinates passed in are then the catalogue's answer at the
    compile's ``when``, and that answer moves: keyed on it, every compile
    named new steps and night two banked on nothing night one shot (#189 A5).
    Keyed on the name as typed, "M 31" and "M31" were two campaigns of one
    object, and a spelling edit restarted the counts. A name-keyed entry with
    no ``canonical`` raises ``ValueError`` (``_identity_of``), anchor or not.

    ``rotation_deg`` is keyed either way, as the target carries it (None for
    any angle), so setting an angle re-frames a named field as it re-frames a
    typed one (spec 3.3). The grid defaults to the single-target shape, so a
    caller that passes neither an anchor nor a grid gets S1's key exactly."""
    name = _identity_of(entry, canonical)
    held = anchor_geometry(anchor)
    # `.get("name")` is None for a typed anchor and `name` is None for a typed
    # entry, so one comparison says "the same kind, and the same object".
    if held is not None and held.get("name") == name:
        return anchor_key(anchor)
    shape = dict(rows=rows, cols=cols, overlap=overlap, fov_x=fov_x,
                 fov_y=fov_y)
    if name is not None:
        return name_key(name, rotation_deg, **shape)
    return geometry_key(ra_hours, dec_deg, rotation_deg, **shape)


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


def _spelled(value) -> str:
    """A step's number as keyed: ``fixed`` to ``STEP_PLACES``, then trailing
    zeros and a bare trailing point trimmed, so 180, 180.0 and "180" are one
    exposure and 60.1 is "60.1".

    TO THE MICROSECOND (#189: A6, then H2). This used to be ``{:g}``, which
    keeps six significant digits: every exposure of 1000 s or more lost its
    milliseconds, and 1234.567 s and 1234.568 s were both "1234.57", two
    recipes on one count (#77). A6 spelled it to the millisecond, which did
    the same to the other end of the range: a planetary or lunar sub is a
    fraction of a millisecond, and three places wrote 0.0005 s and 0.001 s
    both as "0.001" and 0.0001 s as "0", so two of those three recipes shared
    a count. Six places keep every one of them apart and still write
    1234.5678 s whole. Gain and binning go through the same spelling and
    share the six places: an integral value is written as the integer
    ("100", never "100.0") and anything else keeps up to six places, so gain
    100.5 and 100.4 stay two recipes. Negative zero, and a negative that
    rounds to it at six places, is folded by ``fixed``.

    NO MIGRATION. Every value S1's golden vectors and the shipped examples
    use spells the same all three ways, and no S1 or H1 id exists in the
    wild: nothing they minted has been deployed, so no stored ledger holds a
    step id this moves.

    A value that is not a number is kept as its text; the plan's own
    validation refuses it later, and this must not raise a different error
    first."""
    try:
        text = fixed(value, STEP_PLACES)
    except (TypeError, ValueError):
        return str(value).strip()
    # Only a finite number has a point: "nan" and "inf" have none, and
    # stripping zeros off a text with no point would turn "1000" into "1".
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def step_signature(*, frame_type, filter, exposure_s, gain, binning) -> str:
    """The recipe part of a step id, and what "identical" means when ``n`` is
    counted. No filter (None) is spelled empty: the engine treats a falsy
    filter as "leave the wheel where it is", so None and "" are one recipe."""
    return "/".join((str(frame_type), "" if filter is None else str(filter),
                     _spelled(exposure_s), _spelled(gain),
                     _spelled(binning)))


def step_id(target_id: str, stage_node_id: str, *, frame_type, filter,
            exposure_s, gain, binning, n: int = 0) -> str:
    """One step of one target. ``n`` is the occurrence index of this recipe
    among identical ones inside the same stage: almost always 0, and 1 for the
    second of two "L 60" slots in one cycle."""
    signature = step_signature(frame_type=frame_type, filter=filter,
                               exposure_s=exposure_s, gain=gain,
                               binning=binning)
    return _id(f"{target_id}/{stage_node_id}/{signature}/{int(n)}")
