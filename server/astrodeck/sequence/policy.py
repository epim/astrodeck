"""Resolving the rig's standards against one night's plan (#239 stage A).

Twelve settings that used to live only on ``SequencePlan`` now live in config as
well, because they were never per-night intent - dither distance is a property
of the guide scope, a star floor is the operator's standard for a usable frame,
and neither changes from one target to the next. The plan can still override any
of them for a single night; ``None`` means "inherit the rig's standard".

WHY ONE MODULE AND ONE FUNCTION. Two layers for one setting is the shape that
had Polar running simulated for weeks: the active profile's providers beat
global config, ``/api/config`` rendered the losing layer, and nothing anywhere
said which was in force. The lesson taken from that is not "avoid layering" -
layering is what lets a flow-built night inherit standards it cannot express -
it is that the resolution must happen ONCE, in a pure function, and must be able
to say which layer won. Hence :attr:`RunPolicy.sources`.

``None`` IS THE ONLY ABSENCE, and that is load-bearing. Every threshold here
uses 0 to mean "gate off", so the obvious spelling ::

    plan.min_stars or cfg.standards.min_stars      # WRONG

silently discards an operator's deliberate 0 and re-imposes the rig standard,
which is precisely the night they were trying to shoot without a star floor. The
same trap applies to the two booleans, where ``False`` is a choice.

This module may import config; ``models.py`` deliberately may not, which is why
``quota_unbounded`` takes a resolved policy rather than reaching for one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # pragma: no cover - typing only
    from ..config import AppConfig
    from .models import SequencePlan

#: The twelve, and where each one's rig-level value lives. Ordered as the design
#: doc lists them so the two can be read side by side.
MOVED_FIELDS: tuple[str, ...] = (
    "dither_pixels",
    "recover_guiding",
    "cool_timeout_s",
    "hfr_reject_factor",
    "meridian_flip_warn_min",
    "apply_filter_offsets",
    "refocus_on_temp_delta_c",
    "min_stars",
    "max_guide_rms",
    "max_eccentricity",
    "max_consecutive_rejects",
    "max_consecutive_rejects_night",
)

#: field -> the config block that holds the rig-level value.
_RIG_BLOCK: dict[str, str] = {
    "dither_pixels": "guide",
    "recover_guiding": "guide",
    "cool_timeout_s": "cooling",
    "hfr_reject_factor": "escalation",
    "meridian_flip_warn_min": "safety",
    "apply_filter_offsets": "standards",
    "refocus_on_temp_delta_c": "standards",
    "min_stars": "standards",
    "max_guide_rms": "standards",
    "max_eccentricity": "standards",
    "max_consecutive_rejects": "standards",
    "max_consecutive_rejects_night": "standards",
}


@dataclass(frozen=True)
class RunPolicy:
    """What one run actually operates under, with the layer each value came from.

    Frozen because it is resolved once at ``engine.start`` and read for the rest
    of the night: a value that could change mid-run would put the frame at 03:00
    under different rules from the frame at 21:00, with nothing recording it.
    """
    dither_pixels: float
    recover_guiding: bool
    cool_timeout_s: int
    hfr_reject_factor: float
    meridian_flip_warn_min: float
    apply_filter_offsets: bool
    refocus_on_temp_delta_c: float
    min_stars: int
    max_guide_rms: float
    max_eccentricity: float
    max_consecutive_rejects: int
    max_consecutive_rejects_night: int
    #: field -> "plan" | "rig". The half that makes the layering legible; the
    #: report records it so a night can answer "which gates were on, and who
    #: said so" months later.
    sources: dict[str, str] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        """``{field: {"value": v, "source": "plan"|"rig"}}`` for the report."""
        return {f: {"value": getattr(self, f), "source": self.sources.get(f, "rig")}
                for f in MOVED_FIELDS}


def resolve_policy(plan: "SequencePlan", cfg: "AppConfig | None") -> RunPolicy:
    """Resolve the twelve against ``cfg``, recording which layer won.

    ``cfg`` may be ``None`` - a few engine paths run without a config snapshot
    (and tests construct engines bare). In that case the rig layer is a default
    :class:`~astrodeck.config.AppConfig`, which carries exactly the values
    ``SequencePlan`` used to hold, so a policy-less run behaves as it always did.
    """
    if cfg is None:
        from ..config import AppConfig
        cfg = AppConfig()

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name in MOVED_FIELDS:
        mine = getattr(plan, name, None)
        if mine is None:
            values[name] = getattr(getattr(cfg, _RIG_BLOCK[name]), name)
            sources[name] = "rig"
        else:
            # NOT `or` - see the module docstring. An explicit 0 or False is a
            # choice about tonight and has to beat the rig standard.
            values[name] = mine
            sources[name] = "plan"
    return RunPolicy(sources=sources, **values)
