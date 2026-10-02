# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Tonight brief's settle sentence agrees with a saved override (#560
WP-58's second half, found by WP-58's own verifier; backlog wave 12).

WP-58 added ``GuideConfig.dither_settle_pixels``/``_time_s``/``_timeout_s``
and wired them into every dither a run actually fires
(``SequenceEngine._dither_settle_override``). But the Tonight brief's guide
sentence and ``RigFacts.guide_settle`` (``app._guider_and_settle``) still
printed the hardcoded provider defaults -- ``NATIVE_GUIDE_SETTLE`` for the
native engine, ``guide.phd2.SETTLE`` for PHD2 -- with no look at the
persisted override at all. Once an operator saved a settle override, the
engine dithered with it while the brief kept naming the old default: two
surfaces of one flow disagreed, exactly #506's own class of bug.

``_guider_and_settle`` now looks up each field (pixels, time) independently:
the override when it is SET, the provider's own default when it is not.

THE NATIVE GUIDER IS A DELIBERATE EXCEPTION, not a residual of the same bug.
``guide/native.py::dither``'s own docstring says the Rust engine
self-manages its settle pixel/time criteria and never reads a caller's
``settle["pixels"]``/``settle["time"]`` (only ``settle["timeout"]`` reaches
it) -- restated at ``GuideConfig.dither_settle_pixels``. So a persisted
override never changes what a NATIVE dither's wait criteria actually is;
printing it there would be the exact "two surfaces disagree" defect this
item exists to fix, pointed at the wrong guider. The native guider's brief
clause must stay ``NATIVE_GUIDE_SETTLE`` regardless of the override --
covered below as a control.

THE HARNESS mirrors test_h4_brief_guides_from_rig.py exactly: the real app
over ASGI (that file's ``api`` fixture, imported), a synthetic site, and the
guide resolver's real inputs set on the app's hub.

MUTATIONS. The named mutant ("the override lookup removed") was written over
a byte backup of ``server/astrodeck/api/app.py`` in this worktree, run alone
against this file, and restored with its SHA-256 compared before and after.
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
from astrodeck.config import GuideConfig, ProvidersConfig
from astrodeck.guide.phd2 import SETTLE as PHD2_SETTLE
from test_flows_progress_route import api  # noqa: F401
from test_h4_brief_guides_from_rig import (_brief, _graph, native_rig,  # noqa: F401
                                           unconnected_rig)


@pytest.fixture
def phd2_rig(unconnected_rig):
    """``unconnected_rig`` pinned to the PHD2 bridge (Rig > Guider's
    provider row): the resolver's last resort is always available, so this
    names PHD2 whether or not a guide camera and mount are connected, exactly
    ``test_control_a_rig_pinned_to_the_bridge_still_names_it``'s setup."""
    unconnected_rig.store.set_providers(ProvidersConfig(guide="backend"))
    return unconnected_rig


class TestThePhd2SettleFollowsTheOverride:
    async def test_a_saved_pixel_override_appears_in_the_brief(
            self, phd2_rig):
        """Only the pixel field is saved; the time field falls back to
        PHD2's own default (8 s).

        RED under mutant "the override lookup removed" (``_guider_and_settle``
        reverted to ``(phd2_settle["pixels"], phd2_settle["time"])``
        verbatim), observed:

            AssertionError: This flow arms M31. For each target it guides
            with PHD2 (settle below 1.5 px for 8 s, dither 3 px every 3
            frames). It captures L 120 s x 12 (gain 100, bin 1).
            assert 'settle below 2.5 px for 8 s' in '...' (as above)
        """
        phd2_rig.store.set_guide(GuideConfig(dither_settle_pixels=2.5))
        told = await _brief(phd2_rig, _graph())
        assert "settle below 2.5 px for 8 s" in told, told
        assert float(PHD2_SETTLE["time"]) == 8, "premise: PHD2's own time"

    async def test_a_saved_time_override_appears_mixed_with_pixel_default(
            self, phd2_rig):
        """Only the time field is saved; the pixel field falls back to
        PHD2's own default (1.5 px) -- the mix the other direction.

        RED under the same mutant, observed:

            AssertionError: This flow arms M31. For each target it guides
            with PHD2 (settle below 1.5 px for 8 s, dither 3 px every 3
            frames). It captures L 120 s x 12 (gain 100, bin 1).
            assert 'settle below 1.5 px for 15 s' in '...' (as above)
        """
        phd2_rig.store.set_guide(GuideConfig(dither_settle_time_s=15.0))
        told = await _brief(phd2_rig, _graph())
        assert "settle below 1.5 px for 15 s" in told, told
        assert float(PHD2_SETTLE["pixels"]) == 1.5, "premise: PHD2's own pixels"

    async def test_both_fields_saved_both_appear(self, phd2_rig):
        phd2_rig.store.set_guide(GuideConfig(dither_settle_pixels=3.0,
                                             dither_settle_time_s=20.0))
        told = await _brief(phd2_rig, _graph())
        assert "settle below 3 px for 20 s" in told, told

    async def test_a_cleared_override_falls_back_to_the_provider_default(
            self, phd2_rig):
        """Saved, read back, then cleared: the SAME rig returns to PHD2's
        own default once the override is unset again -- the override is read
        live from config, not latched from the first brief."""
        phd2_rig.store.set_guide(GuideConfig(dither_settle_pixels=4.0))
        overridden = await _brief(phd2_rig, _graph())
        assert "settle below 4 px" in overridden, overridden
        phd2_rig.store.set_guide(GuideConfig())
        cleared = await _brief(phd2_rig, _graph())
        assert (f"settle below {PHD2_SETTLE['pixels']:g} px for "
                f"{PHD2_SETTLE['time']:g} s") in cleared, cleared
        assert "4 px" not in cleared, cleared

    async def test_a_zero_override_does_not_crash_and_is_not_printed(
            self, phd2_rig):
        """0 px is a real engine choice (WP-58's own docstring: "the
        engine's fast-recenter pulses alone, no extra settle dwell"), but
        ``RigFacts.guide_settle`` refuses a non-positive pixel as not a
        reading at all. Saving 0 must not take the brief route down with it;
        it falls back to the provider default exactly as unset does.

        RED under mutant "the guard removed" (``_settle_field_override``'s
        ``return v if math.isfinite(v) and v > 0 else default`` replaced with
        a bare ``return v``), observed as an uncaught 500 from the route,
        not a failed assertion:

            astrodeck\\flows\\rig.py:147: in __post_init__
                _finite_positive(self.guide_settle[0], "guide_settle pixels"),
            ValueError: guide_settle pixels must be a positive finite
            number, not 0.0; an unknown value is None
        """
        phd2_rig.store.set_guide(GuideConfig(dither_settle_pixels=0.0))
        told = await _brief(phd2_rig, _graph())
        assert f"settle below {PHD2_SETTLE['pixels']:g} px" in told, told


class TestTheNativeGuiderIgnoresTheOverride:
    async def test_a_saved_override_does_not_reach_the_native_brief(
            self, native_rig):
        """CONTROL: the native guider's settle criteria is the Rust engine's
        own and a persisted override never reaches it
        (``guide/native.py::dither``'s docstring) -- so the same override
        that changes a PHD2 night's brief changes nothing here.

        RED under mutant "native also reads the override" (the pixel/time
        override applied unconditionally rather than only in the PHD2
        branch), observed:

            AssertionError: This flow arms M31. For each target it guides
            with AstroDeck native (settle below 9 px for 10 s, dither 4.5 px
            every 3 frames). It captures L 120 s x 12 (gain 100, bin 1).
            assert 'settle below 1.5 px for 10 s' in '...' (as above)
        """
        native_rig.store.set_guide(GuideConfig(dither_pixels=4.5,
                                               dither_settle_pixels=9.0))
        told = await _brief(native_rig, _graph())
        assert "settle below 1.5 px for 10 s" in told, told
        assert "9" not in told, told
