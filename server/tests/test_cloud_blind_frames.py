"""A frame with no light in it is not a clear sky.

MEASURED, 2026-08-13. A cloud hold parked the wheel on the blackout slot to
shoot its darks and nothing put it back, so the hold's own probe frames were
taken through a slot carrying no glass. They are black - median 241 against a
240 dark floor - and `cloud_score` judged them:

    04:05  probe 0517  ->  "clear (12 bright stars, 9x noise)"
    04:09  probe 0518  ->  "clear (5 bright stars, 9x noise)"
    04:09  sky cleared after 8 min - re-acquiring the target

That verdict released the hold. The detector found twelve bright stars in a
frame with no photons in it.

TWO INDEPENDENT CAUSES, fixed separately, and this file covers both.

1. The references were calibrated for a smaller sensor. `clear_bright_density`
   was 0.5 bright stars per megapixel, so a 26 MP frame reached "fully clear" on
   thirteen detections - and a black frame yields twenty-nine, its hot-pixel
   floor. Both sub-signals saturated to clear on an empty frame.

2. Nothing checked whether the light path was OPEN. That one is knowable
   exactly rather than by heuristic: the wheel's position is a fact, and a frame
   taken through a slot flagged as carrying no glass says nothing whatever about
   the sky. `hub._publish_preview` now omits the verdict entirely for such a
   frame, and `verdict_from_info` returns None for an absent key, so the
   evaluator treats it as indeterminate - fires nothing, re-arms nothing. A hold
   whose probes are blind keeps holding.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.imaging.clouds import cloud_score
from astrodeck.sequence.cloudstate import verdict_from_info

FIXTURES = Path(__file__).parent / "fixtures" / "star_noise"


def blind_frame(shape=(2048, 2048), level=248.0, sigma=5.0, blobs=8,
                seed=7) -> np.ndarray:
    """A frame the shutter never let light into: a bias pedestal, read noise,
    and a few hot-pixel CLUSTERS.

    Clusters rather than single pixels because `detect_stars` already rejects a
    lone hot pixel (its neighbours carry no flux) - what survives on a real
    sensor, and what produced the twenty-nine detections on the rig, is the
    two-by-two blobs. A synthetic single-pixel frame would pass this test
    without the fix and prove nothing."""
    rng = np.random.default_rng(seed)
    img = rng.normal(level, sigma, shape)
    for _ in range(blobs):
        y = int(rng.integers(20, shape[0] - 20))
        x = int(rng.integers(20, shape[1] - 20))
        img[y:y + 2, x:x + 2] += 4000.0
    return np.clip(img, 0, 65535).astype(np.uint16)


class TestABlackFrameIsNotClearSky:
    def test_a_frame_with_no_light_does_not_read_clear(self):
        """The regression. At the old 0.5/MP reference this scored fully clear
        on both sub-signals, because a handful of hot-pixel clusters on a large
        frame clears a bar set at half a star per megapixel."""
        r = cloud_score(blind_frame())
        assert r.cloudy, (
            f"a frame with no light in it read as clear sky: {r.reason} "
            f"(density {r.bright_density:.2f}/MP, contrast {r.contrast:.0f}x)")

    def test_the_hot_pixel_floor_sits_under_the_clear_reference(self):
        """The margin, stated as a number rather than trusted. A blind frame's
        density has to land BELOW `clear_bright_density` or the verdict is a
        coin toss on how many hot pixels this sensor happens to have."""
        from astrodeck.imaging.clouds import cloud_score as cs
        r = cs(blind_frame())
        assert r.bright_density < 4.0, (
            f"the blind frame's {r.bright_density:.2f}/MP is at or above the "
            "clear reference - the calibration has no margin left")

    def test_a_real_star_field_still_reads_clear(self):
        """The guard against overcorrecting. Raising the bars must not start
        calling real sky cloudy - that would hold every night forever, which is
        a worse failure than the one being fixed."""
        data = np.load(FIXTURES / "star_field.npz")["data"]
        r = cloud_score(data)
        assert not r.cloudy, f"a real star field read as cloud: {r.reason}"

    def test_a_real_overcast_frame_still_reads_cloudy(self):
        """The other end. The committed real cloudy frame must stay cloudy."""
        data = np.load(FIXTURES / "blank_overcast.npz")["data"]
        r = cloud_score(data)
        assert r.cloudy, f"a real overcast frame read as clear: {r.reason}"


class TestABlockedBeamPublishesNoVerdictAtAll:
    """The exact fix, as opposed to the calibration one.

    The pixels do not have to be interrogated: the wheel's position already says
    the light path is shut. `SimFilterWheel` carries a blackout slot at 7 for
    precisely this reason, so the sim exercises the real branch.
    """

    @pytest.fixture
    async def sim_hub(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
        monkeypatch.setattr(hub_module.config_store.cfg().safety,
                            "solar_avoidance", False)
        h = Hub()
        await h.connect_sim()
        yield h
        await h.disconnect_all()

    async def test_a_frame_through_the_blackout_slot_carries_no_sky_verdict(
            self, sim_hub):
        fw = sim_hub.devices["filterwheel"]
        assert fw.is_opaque(7), "the sim wheel must have a blackout slot to test"
        await fw.set_position(7)
        info = await sim_hub.capture(0.05, 100, 30, 1, save=False)
        assert "cloud" not in info, (
            "a frame taken through a slot with no glass in it published a sky "
            f"verdict: {info.get('cloud')}")
        assert verdict_from_info(info) is None, (
            "and the sequencer must read that as 'cannot say', not as clear")

    async def test_a_frame_through_a_REAL_filter_still_carries_one(self, sim_hub):
        """The guard: the verdict must not vanish for ordinary frames, or the
        cloud feature quietly stops existing."""
        fw = sim_hub.devices["filterwheel"]
        await fw.set_position(0)
        info = await sim_hub.capture(0.05, 100, 30, 1, save=False)
        assert "cloud" in info, "an ordinary frame lost its sky verdict"
