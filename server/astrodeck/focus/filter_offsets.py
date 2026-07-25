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


def default_ref_slot(names: list | None, current_position: int = 0) -> int:
    """The reference slot to pre-select: a luminance-class slot when the wheel
    has one (case-insensitive ``L`` / ``Lum`` / ``Luminance`` / ``Clear``), else
    the wheel's current position. Always user-overridable in the picker."""
    lum = {"l", "lum", "luminance", "clear", "lp", "uv/ir cut", "uvir"}
    for i, n in enumerate(names or []):
        if str(n).strip().lower() in lum:
            return i
    return int(current_position)
