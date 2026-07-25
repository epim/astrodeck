import pytest
from pydantic import ValidationError

from astrodeck.sequence import Instruction, SequencePlan
from astrodeck.sequence.models import Condition, Predicate


def test_plan_default_has_no_instructions():
    assert SequencePlan().instructions == []


def test_instruction_roundtrip_and_ids():
    i = Instruction(trigger="on_hfr_above", threshold=3.5, action="refocus")
    assert i.enabled and i.id and len(i.id) == 32
    p = SequencePlan(instructions=[i])
    assert SequencePlan(**p.model_dump()).instructions[0].threshold == 3.5


def test_bad_trigger_and_action_rejected():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Instruction(trigger="on_moon_phase", action="refocus")
    with pytest.raises(ValidationError):
        Instruction(trigger="at_time", action="launch_rocket")


def test_at_time_requires_valid_hhmm():
    Instruction(trigger="at_time", at_time="23:30", action="pause")  # ok
    with pytest.raises(ValidationError):
        Instruction(trigger="at_time", at_time="9pm", action="pause")


# --------------------------------------- control-flow expansion validation
_P = Predicate(kind="frame_rejected")


@pytest.mark.parametrize("kwargs,valid", [
    # jump actions REQUIRE a target_arg destination (only_target is a gate).
    (dict(trigger="on_frame_rejected", action="run_target", target_arg="M31"), True),
    (dict(trigger="on_frame_rejected", action="skip_target", target_arg="M31"), True),
    (dict(trigger="on_frame_rejected", action="run_target"), False),
    (dict(trigger="on_frame_rejected", action="skip_target", target_arg="  "), False),
    (dict(trigger="on_frame_rejected", action="run_target",
          only_target="M42", target_arg="M31"), True),
    # compound `when`: 2..8 leaf terms, closed predicate vocabulary.
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when=Condition(op="all", terms=[_P, _P])), True),
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when={"op": "any", "terms": [_P.model_dump()]}), False),
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when={"op": "all", "terms": [_P.model_dump()] * 9}), False),
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when={"op": "xor", "terms": [_P.model_dump()] * 2}), False),
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when={"op": "all", "terms": [{"kind": "moon_up"}, _P.model_dump()]}), False),
    # an at_time PREDICATE needs HH:MM just like the flat trigger does.
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when={"op": "all", "terms": [{"kind": "at_time", "at_time": "23:30"},
                                       _P.model_dump()]}), True),
    (dict(trigger="on_frame_rejected", action="notify", message="x",
          when={"op": "all", "terms": [{"kind": "at_time", "at_time": "9pm"},
                                       _P.model_dump()]}), False),
    # `when` OVERRIDES the flat trigger, so a flat at_time need not be filled in.
    (dict(trigger="at_time", action="notify", message="x",
          when=Condition(op="all", terms=[_P, _P])), True),
])
def test_controlflow_validation(kwargs, valid):
    if valid:
        i = Instruction(**kwargs)
        # round-trips through the plan schema unchanged
        p = SequencePlan(instructions=[i])
        back = SequencePlan(**p.model_dump()).instructions[0]
        assert back.target_arg == i.target_arg
        assert (back.when is None) == (i.when is None)
    else:
        with pytest.raises(ValidationError):
            Instruction(**kwargs)


def test_legacy_instruction_defaults_are_inert():
    """A PRO-3-era rule deserializes with both new fields off => flat path."""
    i = Instruction(trigger="on_hfr_above", threshold=3.0, action="refocus")
    assert i.when is None and i.target_arg is None
