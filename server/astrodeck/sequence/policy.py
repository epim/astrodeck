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

#: THE ECCENTRICITY GATE'S SECOND STATISTIC, and why one dial drives two rules.
#:
#: Measured over every full frame of 2026-09-06 still on disk (the triage spec
#: docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md, table
#: "Whole-frame eccentricity"): NO single median ceiling has margin on both
#: sides. The clean frames top out at median 0.56; the mildest staircase --
#: G_0003, a frame whose guider walked the field away under a flipped
#: calibration -- sits at 0.61, because a staircase's box-truncated fainter
#: stars read as one round lobe and drag the median back down.
#:
#: The DISTRIBUTION does separate them. At the shipped 0.65 default:
#:
#:     rule       clean frames        limit     mildest rejected frame
#:     median     <= 0.56             0.65      0.61  (staircase G_0003)
#:     fraction   <= 0.10 above 0.80  0.25      0.26  (staircase G_0031)
#:
#: so a frame is rejected when its median exceeds `max_eccentricity` OR when
#: more than ECC_ELONGATED_FRACTION of its trusted mid-bright marks exceed
#: `max_eccentricity + ECC_ELONGATED_MARGIN`. Both move with the operator's one
#: number, so raising the ceiling loosens both rules together and a deliberate
#: 0 disarms both -- a companion rule with its own hidden constant would be a
#: setting that does not do what it says.
#:
#: The pair knowingly passes the jump-then-settle class (R_0002, S_0017 at
#: median 0.50) and the faint tail (Ha_0018, 0.62): a single guide jump is not
#: an eccentricity signature, and GN-02 (pulse cap) and GN-03 (re-lock
#: surfacing) are what stop that class at the source.
ECC_ELONGATED_FRACTION = 0.25
ECC_ELONGATED_MARGIN = 0.15

#: Below this many marks carrying an ecc, "the fraction above" is noise -- one
#: star of six is 17% -- so only the median rule runs. The whole-frame
#: measurements behind the numbers above all had hundreds of marks; a 512px
#: crop can have eight.
ECC_MIN_MARKS_FOR_FRACTION = 8

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

    def eccentricity_reject_reason(self, info: Any) -> str | None:
        """Why this frame fails the eccentricity gate, or ``None`` to keep it.

        Reads the grader's ``ecc`` (the median over the trusted mid-bright
        marks) and the ``star_list`` those marks came from, and applies the two
        rules documented at ECC_ELONGATED_FRACTION. The returned sentence names
        WHICH rule fired and both numbers, because "frame rejected" with no
        figures is a night of missing subs nobody can explain in the morning.

        Lives on the policy rather than in the engine so the rule sits beside
        the dial it interprets and the measured margins that chose it. Abstains
        (``None``) whenever the frame carries nothing to judge -- no ``ecc`` and
        too few marks -- rather than guessing.

        THE CAUSE WORDS NAME DEFOCUS FIRST, and that is a field correction
        (2026-09-07 00:38-00:44). Every sub of that stretch was a DONUT -- the
        focuser sat 75 steps off its true position -- and the gate rejected all
        of them, correctly, while the sentence said "trailing/tilt". A
        defocused star measures eccentricity 0.7-0.8 just as a trailed one
        does; the statistic cannot tell them apart, so the sentence must not
        pretend it can. It said "the mount moved" over a night whose mount was
        fine, and the morning went looking for a guiding fault that did not
        exist. "defocus or trailing" is the honest reading of the number: the
        stars are not round, and both a bad focus and a bad guide do that.
        """
        if self.max_eccentricity <= 0 or not isinstance(info, dict):
            return None
        ceiling = float(self.max_eccentricity)
        elongated = ceiling + ECC_ELONGATED_MARGIN
        marks = info.get("star_list")
        eccs = ([float(m["ecc"]) for m in marks
                 if isinstance(m, dict) and m.get("ecc") is not None]
                if isinstance(marks, list) else [])
        n = len(eccs)
        frac = (sum(1 for e in eccs if e > elongated) / n) if n else 0.0
        # The companion measure is only quoted when it was actually judged, so
        # a sentence never implies a rule that did not run.
        tail = (f", {frac:.0%} of {n} stars above {elongated:.2f}"
                if n >= ECC_MIN_MARKS_FOR_FRACTION else "")

        ecc = info.get("ecc")
        if ecc is not None and float(ecc) > ceiling:
            return (f"frame eccentricity median {float(ecc):.2f} above ceiling "
                    f"{ceiling:.2f}{tail} - defocus or trailing")
        if n >= ECC_MIN_MARKS_FOR_FRACTION and frac > ECC_ELONGATED_FRACTION:
            med = f"median {float(ecc):.2f} is under the {ceiling:.2f} ceiling" \
                if ecc is not None else f"median under the {ceiling:.2f} ceiling"
            return (f"frame eccentricity {frac:.0%} of {n} stars above "
                    f"{elongated:.2f}, over the {ECC_ELONGATED_FRACTION:.0%} "
                    f"limit ({med}) - defocus or trailing")
        return None


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
