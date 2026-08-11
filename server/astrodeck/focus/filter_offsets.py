"""Per-filter focuser offsets from measured best-focus positions (pure math).

The offset EDITOR already ships (FilterNamesModal -> ``hub.set_filter_names``)
and the sequence engine applies offsets on every filter change. This is the
arithmetic behind the LEARN loop: run autofocus per slot, then express each
slot's best position RELATIVE to a reference filter (which is pinned to 0).

A slot autofocus FAILED on (a starless narrowband slot is the common case) keeps
its PRIOR offset — writing a bogus 0 there would actively defocus that filter.
"""
from __future__ import annotations


def offsets_from_positions(best_by_slot: dict, ref_slot: int, n_slots: int,
                           prior: list | None = None) -> tuple[list, list]:
    """``(offsets, kept_slots)`` for a wheel of ``n_slots``.

    ``best_by_slot`` maps slot index -> measured best focuser position; slots
    absent from it did not produce a focus. ``prior`` is the current offset list
    (short/missing entries read as 0). Returns the new offset list plus the
    sorted list of slots whose prior offset was KEPT because no focus was
    measured. ``ref_slot`` must be present in ``best_by_slot`` (there is nothing
    to measure against otherwise) -> ``ValueError``."""
    if n_slots <= 0:
        raise ValueError("offsets_from_positions needs at least one slot")
    if ref_slot not in best_by_slot:
        raise ValueError(
            f"reference slot {ref_slot} has no measured focus position — "
            "offsets cannot be computed against it")
    base = int(best_by_slot[ref_slot])
    prev = list(prior or [])
    offsets: list = []
    kept: list = []
    for i in range(n_slots):
        if i in best_by_slot:
            offsets.append(int(best_by_slot[i]) - base)
        else:
            offsets.append(int(prev[i]) if i < len(prev) else 0)
            kept.append(i)
    return offsets, kept


#: How much longer a NARROWBAND slot is swept for than a broadband one.
#:
#: DERIVED, not chosen for looking round. A star is a continuum source, so the
#: light a filter passes scales with its passband: the narrowband slots on a
#: mono wheel are 3-7 nm against a luminance passband of roughly 300 nm
#: (400-700), and the same star therefore arrives 40-100x fainter. Matching
#: that with exposure alone would be 400-1000 s per point — at nine points and
#: three narrowband slots, three to seven hours of a night spent on focus
#: offsets. That is not a default, it is a way of losing a night.
#:
#: The other two levers are already spent. The sweep runs bin 2, which is
#: already 4x the per-pixel signal of bin 1. And gain is past its useful knee:
#: this rig's camera (Player One IMX571) engages high conversion gain at 125,
#: where read noise falls from ~3.96 e- to ~1.36 e- — measured on the camera,
#: docs/hardware/native-cameras-validation.md — and the 2026-08-08 run was
#: already at gain 300, well above it. See ``narrowband_sweep_settings``.
#:
#: So exposure carries it, and its ceiling is the run's own wall clock. That
#: same run measured L/R/G/B at 10 s a point, nine points a slot: with the
#: measurement, about 13 s a point, ~2 minutes a filter, ~8 minutes for the
#: four that worked. x4 puts one narrowband slot at ~7 minutes and all three at
#: ~21 — the same order as the broadband half that succeeded. x10 would be 50
#: minutes of darkness, and x43 (true parity) is the night.
#:
#: What x4 actually buys is not parity, and this is the honest part: it is 1.5
#: magnitudes. A narrowband focus frame is READ-NOISE limited rather than sky
#: limited — the rig's own SII frame at 10 s / gain 300 / bin 2 read a median
#: of 237 ADU and a maximum of ~310, which is the offset pedestal with almost
#: nothing on it — and in that regime per-star SNR grows LINEARLY with exposure
#: (signal ∝ t, noise constant) instead of as √t. So x4 exposure is x4 SNR,
#: which is the one respect in which narrowband focusing is cheaper than it
#: looks. It is a starting point the operator can raise, not a promise.
NARROWBAND_EXPOSURE_MULTIPLE = 4.0


def narrowband_sweep_settings(exposure_s: float, gain: int, *,
                              hcg_threshold_gain: int | None = None
                              ) -> tuple[float, int]:
    """``(exposure_s, gain)`` for a NARROWBAND slot, from the broadband pair.

    Exposure: ``NARROWBAND_EXPOSURE_MULTIPLE`` times the broadband one — see
    the constant for where the number comes from.

    Gain: unchanged, EXCEPT that it is never left below the camera's high
    conversion gain threshold when the camera reports one. That is not a
    preference either: crossing it is a measured drop in read noise (3.96 e- to
    1.36 e- on the IMX571 at gain 125), and on a read-noise-limited frame —
    which is exactly what a narrowband focus frame is — read noise is the whole
    noise budget. A rig already above the knee gets no change at all, which is
    the case on the wheel this was written for.

    Pure, and separate from the run, so the numbers the dialog shows and the
    numbers the sweep uses are the same numbers.
    """
    nb_exposure = float(exposure_s) * NARROWBAND_EXPOSURE_MULTIPLE
    nb_gain = int(gain)
    if hcg_threshold_gain is not None:
        nb_gain = max(nb_gain, int(hcg_threshold_gain))
    return nb_exposure, nb_gain


#: The slot names that pass a broad visual passband, lower-cased.
LUMINANCE_NAMES = frozenset(
    {"l", "lum", "luminance", "clear", "lp", "uv/ir cut", "uvir"})


def luminance_slot(names: list | None) -> int | None:
    """The first luminance-class slot on the wheel, or None if it has none."""
    for i, n in enumerate(names or []):
        if str(n).strip().lower() in LUMINANCE_NAMES:
            return i
    return None


def default_ref_slot(names: list | None, current_position: int = 0) -> int:
    """The reference slot to pre-select: a luminance-class slot when the wheel
    has one (case-insensitive ``L`` / ``Lum`` / ``Luminance`` / ``Clear``), else
    the wheel's current position. Always user-overridable in the picker."""
    slot = luminance_slot(names)
    return int(current_position) if slot is None else slot


def solve_filter_slot(names: list | None, *,
                      narrowband: list | None = None,
                      opaque: list | None = None,
                      current_slot: int = 0,
                      configured: str | None = None) -> int | None:
    """Which slot a PLATE SOLVE should shoot through — ``None`` to stay put.

    A plate solve needs stars, and a 3-7 nm passband delivers them 40-100x
    fainter than luminance does (see ``NARROWBAND_EXPOSURE_MULTIPLE``). An
    OPAQUE slot delivers none at all. So a solve that inherits whatever the
    wheel happens to be on is a solve that fails whenever the run is on
    narrowband — which is exactly the unattended path, because the meridian
    flip fires wherever the cycle reached.

    MEASURED, 2026-08-10: the NGC 6946 flip landed on an Ha step, the centring
    solve shot 0.3 s through 3 nm, and the frame came back with a maximum of
    1218 ADU out of 65535. ASTAP returned no solution, centring fell back to a
    raw GoTo, and the pointing landed 66' off — a third of the frame away from
    the target, unattended, with the next four filters' frames shot there.

    Three rules, in order:

    1. An operator who NAMED a filter for the solve scope gets it, always. The
       explicit setting is a decision, not a hint, and this function must never
       overrule one — that is the invisible-wrong-config shape.
    2. A current slot that already passes broad light is left alone. Most
       solves happen mid-L/R/G/B and must stay free.
    3. Otherwise prefer a luminance-class slot, if the wheel has one.

    Returns ``None`` for "do not move the wheel", which is also the answer when
    the wheel has nothing better than the slot it is already on — a wheel of
    nothing but narrowband has no move that helps, and inventing one would
    trade a bad solve for a bad solve plus two wheel moves. The caller says so
    rather than pretending it did something.

    Pure, so the rule can be tested without a wheel, and shared so the centring
    path and the polar path cannot drift apart. They already did once: polar
    honoured the configured filter from 2026-08-07 and ``solve_and_sync`` never
    did, which is what made the 66' miss possible.
    """
    names = list(names or [])
    if not names:
        return None
    if configured:
        want = str(configured).strip().lower()
        for i, n in enumerate(names):
            if str(n).strip().lower() == want:
                return i
        # A named filter that is not on the wheel is a stale setting, not an
        # instruction. Fall through to the automatic rules rather than failing.

    def flagged(flags, i):
        flags = flags or []
        return 0 <= i < len(flags) and bool(flags[i])

    unusable = (flagged(narrowband, current_slot)
                or flagged(opaque, current_slot))
    if not unusable:
        return None
    lum = luminance_slot(names)
    if lum is None or lum == current_slot:
        return None
    return lum
