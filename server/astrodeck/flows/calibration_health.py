"""The LIBRARY HEALTH matrix — what the calibration queue is allowed to believe.

Backend work list item 5 of the Flows handoff: rows keyed
``(kind, exposure, gain, temp, filter, rotation)`` carrying have/need counts and
an OK / STALE / MISSING verdict. Two surfaces read it and they must not disagree
— the inspector panel (README §3, "LIBRARY HEALTH matrix when node =
CALIBRATION QUEUE") and the queue itself, which is what the node's "If library
stale" parameter means at 23:40 when the clouds roll in.

**THERE IS ALREADY A MATCH RULE AND THIS IS NOT A SECOND ONE.**
``calibration/matcher.py`` opens by calling itself "the AUTHORITATIVE match
rule". A matrix that decided for itself whether a dark fits a light would be a
second answer to the same question, and the two would part company the first
time either tolerance moved — leaving the panel that tells the operator their
library is fine as the one that was wrong. So every verdict below is computed by
calling ``dark_matches`` / ``flat_matches`` with the matcher's own
``MatchTolerance``, over the matcher's own ``LightNeed`` and ``MasterRecord``
types, off keys minted by ``keys.key_from_header``. What this module adds is the
three things the matcher has no opinion about: how MANY frames a need wants, how
OLD they are, and whether anything has CONTRADICTED them.

**THE ROW KEY IS THE PREDICATE, MADE VISIBLE.** Each kind's row is keyed by
exactly the axes that kind's predicate tests. That is why a DARK row carries no
filter (shutter closed — ``keys`` drops it), a FLAT row carries no exposure
(auto-solved to ADU, so ``flat_matches`` does not test it) and no temperature
(same), and a BIAS row carries no exposure (folded to 0.0 at key time). Keying a
row on an axis its predicate ignores splits one row into two with identical
counts; keying on one axis fewer pools frames the pipeline will then refuse.

**WHAT MAKES A ROW STALE** — three signals, and only one of them is a policy:

1. SHORT — fewer usable frames than the quota. Read off the count.
2. DRIFT — frames at these settings exist, but the CONDITIONS they were shot
   under have moved past what the matcher tolerates: sensor temperature beyond
   ``MatchTolerance.temp_tol_c``, or camera angle beyond ``ROTATION_TOL_DEG``.
   No number is invented here; temperature is decided by the same tolerance the
   pipeline will use when it goes looking for a master, so a row can never say
   OK about a set the pipeline would then refuse to apply. This is the
   prototype's own worked example — "darks STALE (sensor −10→−5°C)".
3. AGE — the newest usable frame is older than a horizon. This one IS a policy
   and is stated as one: what actually invalidates a set is an EVENT (the train
   came apart, the dew heater went on, the camera turned in its ring), and none
   of those leave a trace this function can read. The clock is a proxy for the
   event, per kind, and the number that decided it is written into the row's
   reason so the panel can say "newest flat is 21 days old (horizon 14)" rather
   than a bare STALE.

**COUNTING IS NOT EVIDENCE.** On 2026-08-11 this rig shot 50 bias and 44 darks
at gain 125 / offset 30 / bin 1 / −5 °C to match that night's lights, and they
were later found contaminated. A matrix that graded on counts alone said OK
about them, and would say it again. So two fields ride on every row beside
``have``:

* ``contradicted`` — frames whose own header says the dark check refused them
  (``imaging.darks``' verdict, read through the very
  ``library._rejected_by_dark_check`` the master builder uses, because two
  readings of that card is how "absent means usable" quietly becomes "absent
  means bad" on some upgrade day and deletes a working library). They are NOT
  counted into ``have``: the builder will not stack them, so a row that counted
  them would promise a master that cannot be built. The count and the frame's
  own evidence sentence go into the row's reason.
* ``measured`` — how many of ``have`` were actually judged. ``imaging.darks``
  is explicit that its asymmetry runs one way ("a negative verdict is evidence,
  a positive one is only the absence of it") and it names the leak it cannot
  see: a faint starless one, which is what a contaminated set looks like. So OK
  here means NOTHING CONTRADICTS THESE — never "these are good" — and a row
  reading 44/44 with 0 measured tells the operator exactly what that OK is
  worth. That is the honest thing this module can do about 2026-08-11. Claiming
  a verdict the pixels do not support is the thing it must not do.

Pure: no filesystem, no config, and no clock unless asked (``now``). The route
supplies the frames; ``frame_from_header`` is the header→row bridge and carries
the note about which existing walk to feed it from.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Sequence

from ..calibration.keys import CAL_FRAME_TYPES, CalKey, key_from_header
# PRIVATE IMPORTS, ON PURPOSE, both of them rules rather than helpers.
# ``_rejected_by_dark_check`` is the one reading of the DARKOK card in this
# codebase and ``_temp_ok`` is the one reading of "temperature is not a
# constraint when either side does not know it". Copying either into this file
# would create a second copy that drifts silently — and both of them fail in the
# direction of a library that looks healthier than it is.
from ..calibration.library import DARK_OK_CARD, _rejected_by_dark_check
from ..calibration.matcher import (LightNeed, MasterRecord, MatchTolerance,
                                   _temp_ok, best_master, dark_matches,
                                   flat_matches)
from ..rotation import angle_equals

#: The three kinds, in the order the queue shoots them. NOT a fresh opinion:
#: ``compile.compile_plan`` emits ``"order": ["dark", "bias", "flat"]`` into
#: every plan, so the matrix reads top-to-bottom as the night's work. Pinned
#: against the compiler in tests so the two cannot drift apart.
KIND_ORDER: tuple[str, ...] = ("DARK", "BIAS", "FLAT")

#: The matrix's left-hand column (prototype ``m.k``): plural for the sets you
#: shoot many of, singular-looking for bias because "BIASES" is not a word
#: anyone at a telescope says.
KIND_LABEL: dict[str, str] = {"DARK": "DARKS", "BIAS": "BIAS", "FLAT": "FLATS"}

VERDICT_OK = "OK"
VERDICT_STALE = "STALE"
VERDICT_MISSING = "MISSING"

#: The closed reason vocabulary. A row says WHICH of these made it stale so the
#: panel can explain itself; "STALE" with no reason is a colour, not a fact.
REASON_CONTRADICTED = "contradicted"
REASON_DRIFT = "drift"
REASON_SHORT = "short"
REASON_AGE = "age"

#: The queue node's ``quota`` default ("Sufficient quantity", frames each).
DEFAULT_QUOTA = 20

#: How far the camera may have turned before a flat stops being this flat.
#:
#: Derived, not picked. A flat corrects dust shadows, and a mote sitting r from
#: the optical axis moves r·Δθ across the sensor when the rotator turns. At the
#: edge of an APS-C sensor (r ≈ 14 mm) 1° drags a shadow 0.24 mm — about 65 px
#: at 3.76 µm, which is already the diameter of a mote's own out-of-focus
#: shadow. Past roughly one degree, then, a flat stops dividing out the mote
#: that is there and starts dividing out one that is not: a dark ring beside a
#: bright one, in every frame, permanently. Under a degree the shadow still
#: overlaps itself and the correction degrades smoothly rather than inverting.
#:
#: Not a constraint at all when either side's angle is unknown — a rig with no
#: rotator writes no ``ROTATANG``, and the same "unknown is not a constraint"
#: reading ``matcher._temp_ok`` applies to temperature applies here.
ROTATION_TOL_DEG = 1.0

#: When a set stops being trusted on age alone, per kind, in days.
#:
#: THIS IS A POLICY AND THE ROW SAYS SO. What really invalidates calibration is
#: an event: the imaging train came apart and went back together, a dew heater
#: changed the gradient, the camera turned in its ring, the sensor window was
#: cleaned. None of those write anything this function can read, so the clock
#: stands in for them — and because it is a proxy, the horizon that decided a
#: verdict is written into the reason text rather than buried here, and the
#: whole mapping is a parameter a site with different discipline can replace.
#:
#: Flats get 14 days because they are the ones an event breaks: two weekends is
#: long enough that a rig nobody has touched is not nagged every session, short
#: enough that a set predating a train change gets questioned. Darks and bias
#: get 90 because the things that invalidate THEM — gain, offset, binning,
#: setpoint — are in the row key already, so a change makes a different row
#: rather than an old one, and the clock is left catching only sensor ageing and
#: driver/firmware changes.
STALE_AFTER_DAYS: dict[str, float] = {"DARK": 90.0, "BIAS": 90.0, "FLAT": 14.0}

_SECONDS_PER_DAY = 86400.0
#: U+2212. The prototype writes temperatures as "−5°C"; a hyphen-minus renders
#: shorter than the digits beside it in the mono column and reads as a dash.
_MINUS = "−"


def bias_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool:
    """Does this bias record cover ``need``?

    THE ONE PREDICATE THIS MODULE HAD TO ADD, and it is written here rather than
    guessed at because ``matcher`` has none: its docstring says "Bias is optional
    in v1", and ``best_master`` will raise ``KeyError`` on ``"BIAS"``. The shape
    is ``dark_matches`` with exposure removed — ``key_from_header`` already folds
    a bias exposure to 0.0, so it is not an identity — and it reuses the
    matcher's own ``_temp_ok`` rather than a second reading of the temperature
    rule. Temperature IS tested: a bias is the offset pedestal plus read noise,
    and the pedestal moves with the sensor, so a bias from a different setpoint
    is the same kind of wrong as a dark from one.

    TODO(flows-handoff): this belongs in ``calibration/matcher.py`` beside its
    two siblings once the queue actually shoots bias — it is a match rule, and
    match rules living in two files is the exact drift this module's docstring
    argues against. Left here because the scope fence for this task is two new
    files.
    """
    return (m.frame_type == "BIAS" and m.gain == need.gain
            and m.offset == need.offset and m.binning == need.binning
            and _temp_ok(need.temp_c, m.temp_c, tol.temp_tol_c))


@dataclass(frozen=True)
class CalNeed:
    """One light the queue must be able to calibrate.

    ``light`` is the matcher's own :class:`LightNeed`, unwrapped and handed
    straight to its predicates, so there is exactly one description in this
    server of what a light needs. ``rotation_deg`` is the single axis
    ``LightNeed`` does not carry and the handoff's row key does: the camera
    angle, which flats depend on and darks do not.
    """
    light: LightNeed
    rotation_deg: float | None = None


@dataclass(frozen=True)
class CalFrame:
    """One calibration frame on disk, as this matrix needs to see it.

    ``key`` is a whole ``CalKey`` rather than loose fields so the identity of a
    frame here is byte-for-byte the identity the master builder indexes it
    under. ``dark_ok`` is deliberately THREE-valued: ``False`` is a verdict
    against the frame, ``True`` is a verdict for it, and ``None`` is no verdict
    at all — every frame shot before the check existed. Collapsing the last two
    would let a row claim its frames were measured when nothing ever looked at
    them, which is the difference between an OK that is worth something and one
    that is worth nothing.
    """
    key: CalKey
    ts: float
    rotation_deg: float | None = None
    dark_ok: bool | None = None
    why: str = ""
    path: str = ""


@dataclass(frozen=True)
class Reason:
    """Why a row is not OK, in words the panel can print verbatim."""
    code: str
    text: str

    def to_json(self) -> dict:
        return {"code": self.code, "text": self.text}


@dataclass(frozen=True)
class HealthRow:
    """One row of the matrix — the handoff's six key fields, the counts, and the
    evidence behind the verdict.

    ``offset`` and ``binning`` are carried even though the handoff's key does not
    name them, because both are EXACT components of the matcher's predicates: a
    matrix that showed two bin-1/bin-2 rows as one line would be adding frames
    the pipeline keeps apart. They ride in the row rather than changing the key.
    """
    kind: str                       # DARK | BIAS | FLAT
    exposure_s: float | None        # None where exposure is not an identity
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str                     # "" for DARK/BIAS — shutter closed
    rotation_deg: float | None
    have: int
    need: int
    verdict: str
    reasons: tuple[Reason, ...] = ()
    family: int = 0                 # frames at these settings, drifted or not
    contradicted: int = 0           # …of which the dark check refused
    measured: int = 0               # …of `have`, actually judged clear
    newest_ts: float | None = None
    age_days: float | None = None
    master_id: str | None = None
    from_master: int = 0            # frames of `have` that came from a master

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def quantity(self) -> str:
        """The prototype's ``q`` column, verbatim: "14/20"."""
        return f"{self.have}/{self.need}"

    @property
    def summary(self) -> str:
        """The prototype's ``v`` column: "180s g100 · −5°C", "Ha g100 · PA 23°".

        Identity first, then the condition it was shot under. Gain rides in the
        flat row too, where the prototype's fixture shows only the filter,
        because gain IS part of a flat's identity (``flat_matches`` tests it) and
        two rows differing only in gain would otherwise print the same line
        twice with different counts. Binning appears only when it is not 1 — the
        row for the ordinary case should not carry a word about the unusual one.
        """
        bits: list[str] = []
        head = f"{self.exposure_s:g}s " if self.exposure_s is not None else ""
        head += f"{self.filter} " if self.filter else ""
        bits.append(f"{head}g{self.gain}")
        if self.temp_c is not None:
            bits.append(f"{_signed(self.temp_c)}°C")
        if self.rotation_deg is not None:
            bits.append(f"PA {self.rotation_deg:g}°")
        if self.binning != 1:
            bits.append(f"bin {self.binning}")
        return " · ".join(bits)

    def to_json(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "summary": self.summary,
            "exposure_s": self.exposure_s, "gain": self.gain,
            "offset": self.offset, "temp_c": self.temp_c,
            "binning": self.binning, "filter": self.filter,
            "rotation_deg": self.rotation_deg,
            "have": self.have, "need": self.need, "quantity": self.quantity,
            "verdict": self.verdict,
            "reasons": [r.to_json() for r in self.reasons],
            "family": self.family, "contradicted": self.contradicted,
            "measured": self.measured, "newest_ts": self.newest_ts,
            "age_days": self.age_days, "master_id": self.master_id,
            "from_master": self.from_master,
        }


def _signed(value: float) -> str:
    """"−5" / "+0" — a temperature that keeps its sign glyph in a mono column."""
    return f"{_MINUS}{abs(value):g}" if value < 0 else f"{value:g}"


def frame_from_header(header: Mapping, *, ts: float, path: str = "") -> CalFrame | None:
    """A FITS header → one supply row, or None when the frame is not calibration.

    The identity comes from ``keys.key_from_header`` unchanged — the same call
    the master builder makes — so a frame lands in the row it will be stacked
    into. Two things are read on top of it, because ``CalKey`` carries neither:

    * ``ROTATANG`` — written by ``imaging.fitsio`` as "Rotator sky PA (deg)". A
      rig with no rotator writes no card and the angle stays unknown, which the
      matching treats as no constraint rather than as a mismatch.
    * the dark check's verdict, through ``library._rejected_by_dark_check``.

    TODO(flows-handoff): the route needs a walk to feed this, and there are
    already two — ``gallery._walk_frames`` (prunes ``SKIP_TOP_DIRS`` by first
    component, hands back stat data, but reads no gain/offset/temp) and
    ``CalibrationLibrary._bucket_raw`` (right exclusions, reads exactly these
    headers, but DROPS contradicted frames into a side list and buckets by
    ``key_index_id``, which is temp-binned and rotation-blind, so it cannot
    produce a per-rotation row or a contradicted count). Neither fits as-is;
    please extend one rather than adding a third rglob over the capture root.
    """
    key = key_from_header(header)
    if key is None or key.frame_type not in CAL_FRAME_TYPES:
        return None
    raw_rot = header.get("ROTATANG", None)
    try:
        rotation = None if raw_rot is None else float(raw_rot)
    except (TypeError, ValueError):
        rotation = None
    if _rejected_by_dark_check(header):
        dark_ok: bool | None = False
    elif header.get(DARK_OK_CARD, None) is None:
        dark_ok = None                      # unjudged — every pre-check frame
    else:
        dark_ok = True
    return CalFrame(key=key, ts=float(ts), rotation_deg=rotation, dark_ok=dark_ok,
                    why=str(header.get("DARKWHY", "") or "").strip(), path=path)


def _as_master(frame: CalFrame) -> MasterRecord:
    """A raw frame in the matcher's supply type.

    Legitimate because the matcher's predicates are about KEY COMPATIBILITY and
    nothing in them looks at whether a record is a stack of forty or a single
    exposure. The queue's question is how many RAW frames sit at these settings,
    which is upstream of the master and is what the builder will consume.
    """
    k = frame.key
    return MasterRecord(id=frame.path, frame_type=k.frame_type,
                        exposure_s=k.exposure_s, gain=k.gain, offset=k.offset,
                        temp_c=k.temp_c, binning=k.binning, filter=k.filter,
                        frame_count=1, path=frame.path, built_ts=frame.ts)


def _rotation_ok(need_deg: float | None, frame_deg: float | None,
                 tol_deg: float) -> bool:
    """Wrap-aware angle equality, via ``rotation.angle_equals`` — 359.5° and 0.5°
    are one degree apart, and a naive subtraction says 359."""
    if need_deg is None or frame_deg is None:
        return True         # unknown on either side — see ROTATION_TOL_DEG
    return angle_equals(need_deg, frame_deg, tol_deg)


def _in_family(kind: str, need: LightNeed, rec: MasterRecord,
               tol: MatchTolerance) -> bool:
    """Is this record the same SET as ``need`` asks for, conditions aside?

    Family is the kind's predicate with the two CONDITION axes lifted out —
    sensor temperature and camera angle. That split is what separates the two
    verdicts. Change the exposure of a dark, or its gain, and you have never
    shot the thing you now want (MISSING); change the setpoint you shot it at,
    and you have the set, drifted (STALE). Temperature is lifted by passing
    ``temp_c=None``, which is the matcher's own escape hatch — ``_temp_ok``
    treats an unknown temperature as no constraint — so the family test remains
    literally the matcher's predicate rather than a re-derivation of it.
    """
    loose = replace(need, temp_c=None)
    if kind == "DARK":
        return dark_matches(loose, rec, tol)
    if kind == "BIAS":
        return bias_matches(loose, rec, tol)
    return flat_matches(loose, rec, tol)    # rotation is not in flat_matches


def _is_usable(kind: str, need: CalNeed, rec: MasterRecord, frame_rot: float | None,
               tol: MatchTolerance, rot_tol_deg: float) -> bool:
    """Would the pipeline actually apply this record tonight? The matcher's
    predicate, unmodified, plus the rotation axis it does not carry."""
    if kind == "DARK":
        return dark_matches(need.light, rec, tol)
    if kind == "BIAS":
        return bias_matches(need.light, rec, tol)
    return (flat_matches(need.light, rec, tol)
            and _rotation_ok(need.rotation_deg, frame_rot, rot_tol_deg))


def _row_key(kind: str, need: CalNeed) -> tuple:
    """The dedup key: exactly the axes ``kind``'s predicate tests.

    FLAT deliberately omits offset. ``flat_matches`` does not test it (a flat is
    solved to an ADU target, so the pedestal is not part of its identity), and
    keying on an axis the predicate ignores would put two rows on screen with
    identical counts. The row still REPORTS an offset — it is what tonight's
    frames will be shot at — it just does not split on one.
    """
    lt = need.light
    if kind == "DARK":
        return (kind, round(lt.exposure_s, 3), lt.gain, lt.offset, lt.temp_c,
                lt.binning, "", None)
    if kind == "BIAS":
        return (kind, None, lt.gain, lt.offset, lt.temp_c, lt.binning, "", None)
    rot = None if need.rotation_deg is None else round(need.rotation_deg, 3)
    return (kind, None, lt.gain, None, None, lt.binning, lt.filter, rot)


def _pick_master(kind: str, need: CalNeed, masters: Sequence[MasterRecord],
                 tol: MatchTolerance) -> MasterRecord | None:
    """The master the row credits — which must be the master the PIPELINE would
    apply, so ``best_master`` picks it (closest exposure, then closest temp,
    then deepest stack). Bias has no branch there, so it is picked here by depth
    among the ones ``bias_matches`` accepts.

    A master that does not match is not counted at all, not even toward the
    family. A ``MasterRecord`` carries no evidence beyond its key, so crediting
    a stack the pipeline would refuse to apply is precisely the "library looks
    healthy" failure this matrix exists to prevent.
    """
    if kind == "BIAS":
        cands = [m for m in masters if bias_matches(need.light, m, tol)]
        return max(cands, key=lambda m: m.frame_count, default=None)
    return best_master(need.light, list(masters), tol, kind)


def _drift_reason(kind: str, need: CalNeed, drifted: list[CalFrame],
                  tol: MatchTolerance, rot_tol_deg: float) -> Reason:
    """The sentence for frames that ARE this set but were shot under conditions
    the matcher will not accept. It names the measured value, tonight's value
    and the tolerance that separates them, because "STALE" alone leaves the
    operator to guess whether to reshoot or to change the setpoint back."""
    n = len(drifted)
    if kind == "FLAT":
        def _apart(f: CalFrame) -> float:
            # Wrapped, like the test that made the frame drift in the first
            # place: 359° and 1° are two degrees apart, and picking the
            # "closest" with a plain subtraction would name the wrong frame in
            # the sentence the operator reads.
            d = abs((f.rotation_deg or 0.0) - (need.rotation_deg or 0.0)) % 360.0
            return min(d, 360.0 - d)

        closest = min(drifted, key=_apart)
        return Reason(REASON_DRIFT,
                      f"{n} flat(s) at these settings but at PA "
                      f"{(closest.rotation_deg or 0.0):g}° — tonight is PA "
                      f"{(need.rotation_deg or 0.0):g}°, past the "
                      f"±{rot_tol_deg:g}° a dust shadow survives")
    want = need.light.temp_c

    def _delta(f: CalFrame) -> float:
        if want is None or f.key.temp_c is None:
            return float("inf")             # unreachable: an unknown temperature
        return abs(want - f.key.temp_c)     # cannot drift (see _temp_ok)

    closest = min(drifted, key=_delta)
    return Reason(REASON_DRIFT,
                  f"{n} frame(s) at these settings but at "
                  f"{_signed(closest.key.temp_c or 0.0)}°C — tonight is "
                  f"{_signed(want or 0.0)}°C, past the ±{tol.temp_tol_c:g}°C "
                  f"the matcher allows")


def _build_row(kind: str, need: CalNeed, frames: Sequence[CalFrame],
               masters: Sequence[MasterRecord], *, quota: int,
               tol: MatchTolerance, rot_tol_deg: float, horizon_days: float,
               now: float) -> HealthRow:
    lt = need.light
    have = family = contradicted = measured = 0
    newest: float | None = None
    evidence = ""
    drifted: list[CalFrame] = []

    for f in frames:
        rec = _as_master(f)
        if not _in_family(kind, lt, rec, tol):
            continue
        family += 1
        if f.dark_ok is False:
            # Excluded from `have` because `library.build` excludes it from the
            # stack. A row that counted a frame the builder skips would promise
            # a master that cannot be built out of it.
            contradicted += 1
            evidence = evidence or f.why
            continue
        if _is_usable(kind, need, rec, f.rotation_deg, tol, rot_tol_deg):
            have += 1
            measured += 1 if f.dark_ok is True else 0
            newest = f.ts if newest is None else max(newest, f.ts)
        else:
            drifted.append(f)

    master = _pick_master(kind, need, masters, tol)
    from_master = 0
    if master is not None:
        # A built master IS n frames, banked — `frame_count` is the builder's own
        # record of the stack's depth, written precisely so it survives the raws
        # being pruned. Without this a library that has been built and tidied
        # reads MISSING and the queue reshoots a night's calibration it already
        # owns.
        from_master = master.frame_count
        have += from_master
        family += from_master
        newest = (master.built_ts if newest is None
                  else max(newest, master.built_ts))

    age_days = None if newest is None else (now - newest) / _SECONDS_PER_DAY

    reasons: list[Reason] = []
    if contradicted:
        reasons.append(Reason(
            REASON_CONTRADICTED,
            f"{contradicted} frame(s) at these settings were contradicted by "
            f"the dark check — {evidence or 'the frame is not a dark'}. They "
            f"are not counted; the master builder will not stack them"))
    if drifted and have < quota:
        # Only when the row is short. A covered set is not made worse by older
        # frames sitting beside it, and saying so anyway is how a panel trains
        # people to ignore it.
        reasons.append(_drift_reason(kind, need, drifted, tol, rot_tol_deg))
    if have < quota:
        reasons.append(Reason(
            REASON_SHORT,
            f"{have} of {quota} frames — {quota - have} short of the quota"))
    if have > 0 and age_days is not None and age_days > horizon_days:
        reasons.append(Reason(
            REASON_AGE,
            f"newest is {age_days:.0f} days old, past the {horizon_days:g}-day "
            f"horizon for {KIND_LABEL.get(kind, kind).lower()} — nothing here "
            f"knows whether the train has been touched since"))

    if family == 0:
        # Nothing at these settings has EVER been shot. An empty library is
        # therefore MISSING in every row, which is the whole point: a matrix
        # that returned no rows, or OK ones, for a library it could not find
        # would be the most expensive kind of wrong.
        verdict = VERDICT_MISSING
    elif reasons:
        verdict = VERDICT_STALE
    else:
        verdict = VERDICT_OK

    return HealthRow(
        kind=kind,
        exposure_s=round(lt.exposure_s, 3) if kind == "DARK" else None,
        gain=lt.gain, offset=lt.offset,
        temp_c=lt.temp_c if kind in ("DARK", "BIAS") else None,
        binning=lt.binning, filter=lt.filter if kind == "FLAT" else "",
        rotation_deg=need.rotation_deg if kind == "FLAT" else None,
        have=have, need=quota, verdict=verdict, reasons=tuple(reasons),
        family=family, contradicted=contradicted, measured=measured,
        newest_ts=newest, age_days=age_days,
        master_id=None if master is None else master.id, from_master=from_master)


def health_matrix(
    needs: Sequence[CalNeed],
    frames: Iterable[CalFrame] | Callable[[], Iterable[CalFrame]] = (),
    *,
    masters: Sequence[MasterRecord] = (),
    quota: int | Mapping[str, int] = DEFAULT_QUOTA,
    kinds: Sequence[str] = KIND_ORDER,
    tol: MatchTolerance = MatchTolerance(),
    rotation_tol_deg: float = ROTATION_TOL_DEG,
    stale_after_days: Mapping[str, float] = STALE_AFTER_DAYS,
    now: float | None = None,
) -> list[HealthRow]:
    """The matrix: one row per (kind × distinct need), in the queue's own order.

    THE ROWS ARE THE DEMAND, not the disk. Each row starts from something the
    night needs — the lights in the compiled plan — and then asks what the
    library has for it. Building rows from the frames instead would produce a
    library browser: a matrix full of green for last season's darks, and no row
    at all for the one exposure tonight actually needs, because a set you have
    never shot leaves nothing on disk to make a row out of. Silence is the
    failure mode this ordering removes.

    ``frames`` may be a list (tests, and anything that already has them) or a
    zero-argument scanner (the route, so the capture-root walk happens once,
    inside, on the worker thread). ``masters`` is
    ``CalibrationLibrary.list_masters()``.

    ``quota`` is the queue node's "Sufficient quantity", either one number or a
    per-kind mapping — the prototype's own matrix shows bias banked 40 deep
    against 20 for darks, because a zero-second frame is nearly free. A kind the
    queue is set to "Skip" is left OUT of ``kinds`` rather than given a quota of
    zero: a row that needs nothing can never be short and would read OK forever,
    which is the same lie by a different route.

    An empty ``needs`` returns NO rows — there is nothing to calibrate for. That
    is honest, but it is not green: the caller must not render an empty matrix
    as a healthy one, it must say there are no lights planned yet.
    """
    supply = list(frames() if callable(frames) else frames)
    master_list = list(masters)
    at = time.time() if now is None else now
    wanted = [k for k in KIND_ORDER if k in set(kinds)]

    rows: list[HealthRow] = []
    seen: set[tuple] = set()
    for kind in wanted:
        horizon = float(stale_after_days.get(kind, STALE_AFTER_DAYS[kind]))
        # max(1, …): see the docstring — a quota of zero is expressed by
        # dropping the kind, never by asking for no frames.
        want = quota.get(kind, DEFAULT_QUOTA) if isinstance(quota, Mapping) else quota
        want = max(1, int(want))
        for need in needs:
            key = _row_key(kind, need)
            if key in seen:
                continue
            seen.add(key)
            rows.append(_build_row(kind, need, supply, master_list, quota=want,
                                   tol=tol, rot_tol_deg=rotation_tol_deg,
                                   horizon_days=horizon, now=at))
    return rows
