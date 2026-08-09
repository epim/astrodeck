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


def default_ref_slot(names: list | None, current_position: int = 0) -> int:
    """The reference slot to pre-select: a luminance-class slot when the wheel
    has one (case-insensitive ``L`` / ``Lum`` / ``Luminance`` / ``Clear``), else
    the wheel's current position. Always user-overridable in the picker."""
    lum = {"l", "lum", "luminance", "clear", "lp", "uv/ir cut", "uvir"}
    for i, n in enumerate(names or []):
        if str(n).strip().lower() in lum:
            return i
    return int(current_position)
