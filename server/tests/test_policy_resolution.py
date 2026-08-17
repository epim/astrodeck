"""Stage A (#239): one setting across two layers, resolved in one place.

This is the shape that had Polar running simulated for weeks - the active
profile's providers beat global config while /api/config showed the LOSING
layer, and nothing on any screen said which one was in force. So the resolver
is a pure function of both layers, it records WHICH layer won for every field,
and the precedence is asserted here by name rather than inferred from a
running engine.
"""
from astrodeck.config import AppConfig
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.policy import MOVED_FIELDS, resolve_policy


def test_none_inherits_the_rig_standard():
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    p = resolve_policy(SequencePlan(), cfg)
    assert p.min_stars == 40
    assert p.sources["min_stars"] == "rig"


def test_an_explicit_plan_value_wins():
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    p = resolve_policy(SequencePlan(min_stars=5), cfg)
    assert p.min_stars == 5
    assert p.sources["min_stars"] == "plan"


def test_an_explicit_zero_is_a_choice_not_an_absence():
    """THE test the whole scheme rests on.

    0 means "this gate is off" for every threshold here. If a stored 0 were
    treated as absence, an operator could not turn a gate off for one night
    against a rig standard that has it on - and the natural buggy spelling
    (``plan.min_stars or cfg.standards.min_stars``) does exactly that.
    ``None`` is the only absence.
    """
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    cfg.standards.max_consecutive_rejects = 10
    p = resolve_policy(SequencePlan(min_stars=0, max_consecutive_rejects=0), cfg)
    assert p.min_stars == 0
    assert p.sources["min_stars"] == "plan"
    assert p.max_consecutive_rejects == 0
    assert p.sources["max_consecutive_rejects"] == "plan"


def test_false_is_a_choice_too():
    """The bool twin of the zero case: ``apply_filter_offsets=False`` on a plan
    must beat a rig standard of True."""
    cfg = AppConfig()
    cfg.standards.apply_filter_offsets = True
    cfg.guide.recover_guiding = True
    p = resolve_policy(
        SequencePlan(apply_filter_offsets=False, recover_guiding=False), cfg)
    assert p.apply_filter_offsets is False
    assert p.sources["apply_filter_offsets"] == "plan"
    assert p.recover_guiding is False
    assert p.sources["recover_guiding"] == "plan"


def test_every_moved_field_resolves_and_names_its_layer():
    cfg = AppConfig()
    p = resolve_policy(SequencePlan(), cfg)
    assert len(MOVED_FIELDS) == 12
    for f in MOVED_FIELDS:
        assert hasattr(p, f), f
        assert p.sources[f] == "rig", f


def test_a_bare_plan_resolves_to_the_old_model_defaults():
    """The no-change guarantee, stated as a test: a plan nobody edited and a
    config nobody opened resolve to exactly what SequencePlan used to carry."""
    p = resolve_policy(SequencePlan(), AppConfig())
    assert p.dither_pixels == 3.0
    assert p.recover_guiding is True
    assert p.cool_timeout_s == 600
    assert p.hfr_reject_factor == 0.0
    assert p.meridian_flip_warn_min == 15.0
    assert p.apply_filter_offsets is True
    assert p.refocus_on_temp_delta_c == 0.0
    assert p.min_stars == 0
    assert p.max_guide_rms == 0.0
    assert p.max_eccentricity == 0.0
    assert p.max_consecutive_rejects == 10
    assert p.max_consecutive_rejects_night == 20


def test_a_flow_compiled_plan_picks_up_the_rigs_standards():
    """THE HOLE THIS STAGE EXISTS TO CLOSE.

    `flows/to_plan.py` sets eight plan fields and leaves the rest at the model
    default, so before this change a graph-built night ran with every quality
    gate off and no temp-drift refocus, with nowhere to say otherwise.
    """
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    cfg.standards.max_guide_rms = 1.5
    cfg.standards.refocus_on_temp_delta_c = 2.0
    # what to_sequence_plan produces: name/targets/instructions/park/warm only
    flow_plan = SequencePlan(name="Flow", park_when_done=True,
                             warm_cooler_when_done=True)
    p = resolve_policy(flow_plan, cfg)
    assert p.min_stars == 40
    assert p.max_guide_rms == 1.5
    assert p.refocus_on_temp_delta_c == 2.0
    assert p.sources["min_stars"] == "rig"
