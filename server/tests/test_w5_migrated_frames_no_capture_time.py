# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""migrate_legacy_resume's synthesized frames carry no usable capture time,
so a migrated body step's ADOPT match (H3 orchestrator ruling 7,
``continuation._capture_times``) is unmatched by RULE rather than by luck
(#265).

The legacy ``.sequence_resume.json`` names only ONE instant for the whole
run (its own top-level ``ts``), never one per frame, so stamping each
synthesized frame with the MIGRATION's clock (``now()`` at boot) invented a
capture time nothing recorded. ``_capture_times`` read that fabricated stamp
as when the frame was taken and checked a body step's old pointing against
the ephemeris at the wrong instant: almost always a false non-match (the
body has moved since the real capture), and in principle a false MATCH if
the body happened to sit on the old pointing at the moment the server
rebooted.

Fixed by leaving synthesized frames at ``ts=0.0`` (the model default), which
``continuation._usable`` already reads as "never stamped" for a frame the
engine itself did not get to record -- the same sentinel, now honestly
applied to a frame this store invented rather than captured.

Each mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failure is quoted as observed.
"""
from __future__ import annotations

import json

import pytest

import astrodeck.hub as hub_module
from astrodeck.flows import continuation
from astrodeck.flows.continuation import adopt_matches
from astrodeck.flows.tonight import NameResolution
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import migrate_legacy_resume

#: Where the legacy plan pointed Jupiter: picked so a resolver that always
#: answers this spot would make the step MATCH if asked at any instant.
OLD_RA, OLD_DEC = 5.0, 20.0


@pytest.fixture(autouse=True)
def capture_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _legacy_file(capture_dir, *, count: int = 3) -> None:
    legacy = {"plan": {"name": "L", "targets": [
                  {"name": "Jupiter", "ra_hours": OLD_RA, "dec_deg": OLD_DEC,
                   "steps": [{"exposure_s": 60.0, "count": count}]}]},
              "done": {"0:0": count},
              "ts": 123.0, "report_id": "L-20260701-010101"}
    (capture_dir / ".sequence_resume.json").write_text(
        json.dumps(legacy), encoding="utf-8")


def test_migrated_synthesized_frames_have_no_usable_capture_time(capture_dir):
    """The direct claim: a migrated step's frames give ``_capture_times``
    nothing to sample, so it returns ``[]`` rather than ``[the migration
    instant]``.

    RED under mutant "synthesized frames keep the migration clock"
    (``migrate_legacy_resume``: the synthesized ``SessionFrame(...)`` call
    gets ``ts=now`` put back), observed verbatim:

        ________ test_migrated_synthesized_frames_have_no_usable_capture_time _________
        tests\\test_w5_migrated_frames_no_capture_time.py:80: in test_migrated_synthesized_frames_have_no_usable_capture_time
            assert all(f.ts == 0.0 for f in frames), (
        E   AssertionError: premise: migration stamped a real clock onto a synthesized frame
        E   assert False
    """
    _legacy_file(capture_dir)
    s = migrate_legacy_resume()
    assert s is not None
    step = s.plan.targets[0].steps[0]
    frames = [f for f in s.frames if f.step_id == step.id]
    assert len(frames) == 3, "premise: the step holds synthesized frames"
    assert all(f.ts == 0.0 for f in frames), (
        "premise: migration stamped a real clock onto a synthesized frame")

    times = continuation._capture_times(frames, s.created_ts)

    assert times == [], (
        "a migrated step's frames must give no usable capture time, so "
        "ADOPT's ephemeris check is skipped rather than asked at the wrong "
        "instant")


def test_a_migrated_body_step_is_unmatched_even_on_the_old_pointing(
        capture_dir):
    """The end-to-end claim ADOPT itself makes (H3 orchestrator ruling 7):
    even a catalogue that would place Jupiter EXACTLY on the old pointing,
    at any instant asked, leaves the step unmatched -- because the migrated
    frames give ``_capture_times`` no instant to ask about at all. A
    catalogue this generous would wrongly MATCH the step if the migration
    clock were ever consulted, which is exactly the false-positive half of
    #265's finding ("could in principle pass WRONGLY").

    RED under the same mutant as above, observed verbatim (the step's own id
    elided to a placeholder; the site-privacy scan runs on every published
    finding, not on a test's own transcript, but the id is arbitrary and
    carries nothing worth quoting exactly):

        _______ test_a_migrated_body_step_is_unmatched_even_on_the_old_pointing ________
        tests\\test_w5_migrated_frames_no_capture_time.py:127: in test_a_migrated_body_step_is_unmatched_even_on_the_old_pointing
            assert step.id not in matches.mapping, (
        E   AssertionError: a migrated body step must stay unmatched; ADOPT has no real capture time to check it against
        E   assert '<step id>' not in {'<step id>': ('<target id>', '<step id>')}

    (the step was MATCHED: the fabricated migration-clock instant let the
    generous resolver place Jupiter on the old pointing and the mapping
    credited the migrated frames to the new plan's step -- the false-MATCH
    half of #265's finding, not merely the false-non-match half.)
    """
    _legacy_file(capture_dir)
    s = migrate_legacy_resume()
    assert s is not None
    step = s.plan.targets[0].steps[0]

    def resolve(name, when=None):
        # Answers every instant with the OLD pointing itself: a resolver
        # this generous only fails to match if no instant is ever asked.
        if name != "Jupiter":
            return None
        return NameResolution(ra_hours=OLD_RA, dec_deg=OLD_DEC,
                              identity="Jupiter", moves=True)

    new_plan = SequencePlan(targets=[Target(
        name="Jupiter", ra_hours=6.0, dec_deg=25.0,
        steps=[ExposureStep(exposure_s=60.0, count=5)])])

    matches = adopt_matches(s, new_plan, resolve=resolve)

    assert step.id not in matches.mapping, (
        "a migrated body step must stay unmatched; ADOPT has no real "
        "capture time to check it against")
    assert matches.unmatched, "the step must be reported, not merely dropped"
    reason = matches.unmatched[0]["reason"]
    assert "no capture time is recorded" in reason, reason
