from astrodeck.sequence import Instruction, SequencePlan


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
    import pytest
    from pydantic import ValidationError
    Instruction(trigger="at_time", at_time="23:30", action="pause")  # ok
    with pytest.raises(ValidationError):
        Instruction(trigger="at_time", at_time="9pm", action="pause")
