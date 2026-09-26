"""Rig facts: what the route knows about the live rig, as one frozen value.

``compile_plan`` and the doctor are pure: no devices, no config, no clock. A
few of the mosaic rules nevertheless turn on something only the running rig
knows (spec 3.3, 1.8):

* M5 compares a block's snapshotted camera field with the live effective
  optics (``hub.effective_optics``), and the wizard needs the same field to
  lay out a mosaic at all;
* M10 and the brief weigh a MEASURED hop cost against a visit;
* M8 asks whether the active profile has a rotator;
* M9 previews the ``quota_unbounded`` refusal, which needs to know whether
  both reject guards are off.

The route reads those off the hub, the way ``to_plan`` is handed ``cool_to``,
and passes them in as a :class:`RigFacts`. The rules take the value; they never
reach for the hub themselves.

KNOWING NOTHING IS A STATE, NOT A PLAUSIBLE READING. Every field is optional,
and its "unknown" is ``None`` (``""`` for the provenance line, 0 samples for
the hop), so ``RigFacts()`` is what a preview, a test or a route with no rig
hands over, and every rule that needs a fact SKIPS when the fact is unknown. A
default of ``has_rotator=False`` would make M8 warn "no rotator" on every rig
nobody asked about; a field of ``(0, 0)`` would make M5 report a camera that
images nothing. So a zero, a negative or a non-finite field is refused here
rather than carried: the route must say "unknown" as ``None``.

This module imports only the standard library, and a test holds it to that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


def _finite_positive(value, what: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number, not {value!r}") from None
    if not math.isfinite(v) or v <= 0:
        raise ValueError(
            f"{what} must be a positive finite number, not {value!r}; an "
            f"unknown value is None")
    return v


def _optional_bool(value, what: str) -> None:
    # `is True / is False`, not truthiness: 1 or "yes" from a sloppy route
    # would otherwise pass as a fact nobody measured.
    if value is not None and value is not True and value is not False:
        raise ValueError(f"{what} must be True, False or None, not {value!r}")


@dataclass(frozen=True)
class RigFacts:
    """What the live rig is known to be. Every field optional; see the module
    docstring for why unknown is never a default reading."""

    #: The imaging camera's field at BIN 1, ``(x_deg, y_deg)``, from the live
    #: effective optics. Bin 1 because a block's `fovX`/`fovY` snapshot is
    #: bin 1 (spec 3.1), and the two are compared.
    fov_deg: Optional[tuple[float, float]] = None
    #: Where the field came from, for display: "profile Refractor, matched
    #: 2026-09-23". Never read by a rule.
    fov_from: str = ""
    #: The measured cost of one panel hop, seconds, and how many hops it was
    #: measured over. Both or neither: a cost with no samples is not a
    #: measurement, and "measured over 0 hops" is not a sentence to print.
    hop_cost_s: Optional[float] = None
    hop_samples: int = 0
    #: Whether the ACTIVE profile has a rotator (M8).
    has_rotator: Optional[bool] = None
    #: Whether both reject guards (per step and per night) are off (M9).
    reject_guards_off: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.fov_deg is not None:
            if (isinstance(self.fov_deg, (str, bytes))
                    or not hasattr(self.fov_deg, "__len__")
                    or len(self.fov_deg) != 2):
                raise ValueError(
                    f"fov_deg is an (x, y) pair in degrees, not "
                    f"{self.fov_deg!r}")
            pair = (_finite_positive(self.fov_deg[0], "fov_deg x"),
                    _finite_positive(self.fov_deg[1], "fov_deg y"))
            # Normalised, so two facts about the same field compare and hash
            # equal whether the route handed a list or a tuple of ints.
            object.__setattr__(self, "fov_deg", pair)
        if not isinstance(self.fov_from, str):
            raise ValueError(f"fov_from is text, not {self.fov_from!r}")
        if (isinstance(self.hop_samples, bool)
                or not isinstance(self.hop_samples, int)
                or self.hop_samples < 0):
            raise ValueError(
                f"hop_samples is a count, not {self.hop_samples!r}")
        if self.hop_cost_s is None:
            if self.hop_samples:
                raise ValueError(
                    f"{self.hop_samples} hop samples with no measured cost")
        else:
            cost = _finite_positive(self.hop_cost_s, "hop_cost_s")
            if self.hop_samples < 1:
                raise ValueError(
                    "a measured hop cost needs the number of hops it was "
                    "measured over")
            object.__setattr__(self, "hop_cost_s", cost)
        _optional_bool(self.has_rotator, "has_rotator")
        _optional_bool(self.reject_guards_off, "reject_guards_off")
