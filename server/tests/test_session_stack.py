"""Session stack core: one running mean per filter, composited into colour.

The composite is the only place in the app where several filters are shown as
one picture, so the things worth pinning down are the ones that make it a
PICTURE rather than three greyscales in a trenchcoat: that each filter lands on
the channel its name implies, that the channels stay on one brightness scale
(so a target brighter in red comes out red), that they are registered to each
other and not only to themselves, and that the accumulator is downsampled so a
26 MP sensor cannot eat the box.
"""
import math

import numpy as np
import pytest

from astrodeck.imaging.sessionstack import (
    CHANNEL_ORDER, MAX_STACK_SIDE, SessionStacker, block_mean, channel_for,
    downsample_factor, stretch_channels,
)

STARS = [(60, 70, 1.0), (180, 120, 0.7), (300, 200, 0.55),
         (420, 90, 0.4), (250, 330, 0.35)]


def field(dx: float = 0.0, dy: float = 0.0, *, scale: float = 1.0,
          seed: int = 3, shape=(400, 480), glow: float = 3000.0) -> np.ndarray:
    """A star field plus an extended glow, shifted by (dx, dy).

    The glow is what makes a brightness assertion meaningful: over a bare star
    field the frame mean is background noise, which is identical in every
    channel however bright the stars are.
    """
    rng = np.random.default_rng(seed)
    h, w = shape
    img = rng.normal(400.0, 6.0, shape)
    xs = np.arange(w) - (240 + dx)
    ys = (np.arange(h) - (200 + dy))[:, None]
    img += scale * glow * np.exp(-(xs ** 2 + ys ** 2) / (2 * 110.0 ** 2))
    for x, y, b in STARS:
        xs = np.arange(w) - (x + dx)
        ys = (np.arange(h) - (y + dy))[:, None]
        img += b * 900_000.0 * np.exp(-(xs ** 2 + ys ** 2) / (2 * 3.0 ** 2)) \
            / (2 * math.pi * 9.0)
    return np.clip(img, 0, 65535).astype(np.uint16)


def rgb_session(*, r=1.0, g=0.5, b=0.2) -> SessionStacker:
    """Three filters, several subs each, small dithers between them."""
    s = SessionStacker()
    s.start()
    for i, (dx, dy) in enumerate([(0, 0), (1.5, -2.0), (-2.0, 1.0)]):
        s.add(field(dx, dy, scale=r, seed=10 + i), "R", 60.0, target="M42")
    for i, (dx, dy) in enumerate([(3, 1), (2, -1)]):
        s.add(field(dx, dy, scale=g, seed=20 + i), "Green", 60.0, target="M42")
    s.add(field(0, 0, scale=b, seed=30), "B", 30.0, target="M42")
    return s


# ------------------------------------------------------------ filter mapping
@pytest.mark.parametrize("name,channel", [
    ("R", "R"), ("red", "R"), ("G", "G"), ("Green", "G"), ("B", "B"),
    ("L", "L"), ("Lum", "L"), ("Clear", "L"),
    ("Ha", "Ha"), ("H-alpha", "Ha"), ("OIII", "Oiii"), ("O3", "Oiii"),
    ("SII", "Sii"), ("S2", "Sii"),
    ("OSC", "L"), ("", "L"), (None, "L"),
])
def test_filter_names_fold_onto_channels(name, channel):
    assert channel_for(name) == channel


def test_an_unknown_filter_is_luminance_not_a_crash():
    # A dual-band filter, a CLS, or whatever the operator typed. Contributing
    # brightness is honest; claiming a colour for an unknown bandpass is not.
    assert channel_for("L-eXtreme") == "L"
    assert channel_for("  ha  ") == "Ha"


# ------------------------------------------------------------- memory policy
def test_the_accumulator_is_binned_below_the_preview_ceiling():
    # The rig's actual sensor. Full frame would be 313 MB of float32 planes per
    # filter; this is the line that stops seven filters asking for 2.1 GB.
    f = downsample_factor((4176, 6252))
    assert f == 4
    h, w = 4176 // f, 6252 // f
    assert max(h, w) <= MAX_STACK_SIDE
    mb = 3 * h * w * 4 / 1e6          # sum + sumsq + coverage, float32
    assert mb < 25, f"{mb:.0f} MB per filter is not a preview"


def test_even_a_small_sensor_is_binned_at_least_2x():
    assert downsample_factor((100, 120)) == 2


def test_block_mean_averages_and_crops_the_remainder():
    a = np.arange(4 * 6, dtype=np.uint16).reshape(4, 6)
    out = block_mean(a, 2)
    assert out.shape == (2, 3)
    assert out[0, 0] == round((0 + 1 + 6 + 7) / 4)
    # 5 rows at factor 2 -> the odd row is dropped, not folded in half-weighted
    assert block_mean(np.ones((5, 5), dtype=np.uint16), 2).shape == (2, 2)


# ------------------------------------------------------------- accumulation
def test_each_filter_keeps_its_own_count_and_integration():
    s = rgb_session()
    st = s.status()
    counts = {c["channel"]: c["frames"] for c in st["channels"]}
    secs = {c["channel"]: c["integrated_s"] for c in st["channels"]}
    assert counts == {"R": 3, "G": 2, "B": 1}
    assert secs == {"R": 180.0, "G": 120.0, "B": 30.0}
    assert st["frames"] == 6 and st["integrated_s"] == 330.0
    assert st["mode"] == "rgb" and st["has_image"] is True
    assert [c["channel"] for c in st["channels"]] == ["R", "G", "B"]
    assert all(c in CHANNEL_ORDER for c in counts)


def test_a_disabled_stacker_accumulates_nothing():
    s = SessionStacker()
    assert s.add(field(), "R", 60.0, target="M42") is None
    assert s.status()["frames"] == 0 and s.status()["enabled"] is False


def test_stop_frees_the_planes_as_well_as_flipping_the_switch():
    s = rgb_session()
    assert s.status()["frames"] == 6
    st = s.stop()
    assert st["enabled"] is False and st["frames"] == 0
    assert s.rgb_preview() is None


def test_a_new_target_resets_the_stack():
    s = rgb_session()
    s.add(field(scale=1.0, seed=99), "R", 60.0, target="NGC 6946")
    st = s.status()
    assert st["target"] == "NGC 6946"
    assert st["frames"] == 1, "the previous target's frames are still in the picture"


def test_a_second_run_on_the_same_target_starts_a_new_picture():
    # Same object, different night: the mount was re-centred and the rotator may
    # have moved, so last run's pixels are not this run's.
    s = SessionStacker()
    s.start()
    s.add(field(seed=1), "R", 60.0, target="M42", session="report-a")
    s.add(field(seed=2), "R", 60.0, target="M42", session="report-a")
    assert s.status()["frames"] == 2
    s.add(field(seed=3), "R", 60.0, target="M42", session="report-b")
    assert s.status()["frames"] == 1


def test_a_starless_frame_is_counted_rejected_not_stacked():
    s = SessionStacker()
    s.start()
    s.add(field(seed=1), "R", 60.0, target="M42")
    seq = s.seq
    flat = np.full((400, 480), 400, dtype=np.uint16)
    assert s.add(flat, "R", 60.0, target="M42") is None
    st = s.status()
    assert st["frames"] == 1 and st["rejected"] == 1
    assert s.seq == seq, "a rejected frame bumped the change counter"


# --------------------------------------------------------------- composite
def test_the_composite_channels_follow_the_input_brightness():
    # The whole point of a shared brightness scale. With per-channel white
    # points these three would come out equal and the picture would be grey.
    s = rgb_session(r=1.0, g=0.5, b=0.2)
    rgb = s.compose()
    assert rgb is not None and rgb.shape[2] == 3
    means = [float(rgb[:, :, i].mean()) for i in range(3)]
    assert means[0] > means[1] > means[2], means
    core = rgb[90:110, 110:130]
    core_means = [float(core[:, :, i].mean()) for i in range(3)]
    assert core_means[0] > core_means[1] > core_means[2], core_means
    assert 0.0 <= rgb.min() and rgb.max() <= 1.0


def test_a_luminance_only_session_is_grey_not_coloured():
    s = SessionStacker()
    s.start()
    s.add(field(seed=1), "L", 60.0, target="M42")
    rgb = s.compose()
    assert s.mode() == "mono"
    assert np.allclose(rgb[:, :, 0], rgb[:, :, 1])
    assert np.allclose(rgb[:, :, 1], rgb[:, :, 2])
    assert float(rgb.max()) > 0.5, "a grey composite that is also black"


def test_an_osc_camera_with_no_wheel_lands_on_luminance():
    s = SessionStacker()
    s.start()
    assert s.add(field(seed=1), "", 60.0, target="M42") == "L"
    assert s.mode() == "mono"


def test_oiii_alone_is_teal_and_ha_is_red():
    s = SessionStacker()
    s.start()
    s.add(field(scale=1.0, seed=1), "Ha", 300.0, target="M42")
    s.add(field(scale=0.6, seed=2), "OIII", 300.0, target="M42")
    assert s.mode() == "narrowband"
    rgb = s.compose()
    # Oiii feeds green and blue identically, so those two planes are equal...
    assert np.allclose(rgb[:, :, 1], rgb[:, :, 2])
    # ...and Ha, the brighter channel, dominates red.
    assert float(rgb[:, :, 0].mean()) > float(rgb[:, :, 1].mean())


def test_sii_blends_into_red_beside_ha():
    only_ha = SessionStacker()
    only_ha.start()
    only_ha.add(field(scale=1.0, seed=1), "Ha", 300.0, target="M42")
    red_alone = float(only_ha.compose()[:, :, 0].mean())

    both = SessionStacker()
    both.start()
    both.add(field(scale=1.0, seed=1), "Ha", 300.0, target="M42")
    both.add(field(scale=0.2, seed=2), "SII", 300.0, target="M42")
    red_blended = float(both.compose()[:, :, 0].mean())
    assert red_blended < red_alone, "Sii did not blend into red, it was ignored"


def test_luminance_takes_over_brightness_and_leaves_the_colour_alone():
    # LRGB: adding L must not grey the image out. The chroma differences
    # (r - b) are what a viewer reads as colour. The substitution itself
    # preserves them exactly (every channel gets the same L - y added); what
    # moves them a few percent is the shared range widening to take L in, which
    # is the same curve change every channel sees. "A few percent" is the
    # assertion; "unchanged" would be measuring the wrong thing.
    base = rgb_session()
    plain = base.compose()
    chroma_before = float((plain[:, :, 0] - plain[:, :, 2]).mean())

    base.add(field(scale=1.4, seed=77), "L", 120.0, target="M42")
    lrgb = base.compose()
    chroma_after = float((lrgb[:, :, 0] - lrgb[:, :, 2]).mean())
    assert chroma_after > 0.8 * chroma_before, "L greyed the picture out"
    assert float(lrgb.mean()) > float(plain.mean()), "L added no brightness"
    means = [float(lrgb[:, :, i].mean()) for i in range(3)]
    assert means[0] > means[1] > means[2], means


def test_channels_are_registered_to_each_other_not_only_to_themselves():
    # G's own first frame sits 24px off R's, which is an ordinary dither plus a
    # filter change. Unregistered, every star in the composite wears a coloured
    # shadow; this is the assertion that says the shift was measured and undone.
    s = SessionStacker()
    s.start()
    for i in range(3):
        s.add(field(0, 0, scale=1.0, seed=40 + i), "R", 60.0, target="M42")
    for i in range(2):
        s.add(field(24, -16, scale=1.0, seed=50 + i), "G", 60.0, target="M42")
    planes, h, w = s._aligned_planes()
    r_peak = np.unravel_index(int(np.argmax(planes["R"])), (h, w))
    g_peak = np.unravel_index(int(np.argmax(planes["G"])), (h, w))
    assert abs(int(r_peak[0]) - int(g_peak[0])) <= 1
    assert abs(int(r_peak[1]) - int(g_peak[1])) <= 1


def test_stretch_shares_one_range_but_neutralises_each_background():
    # Two channels with the SAME signal above very different sky levels: the
    # backgrounds must come out equal (no colour cast) and so must the signal.
    a = np.full((64, 64), 400, dtype=np.uint16)
    b = np.full((64, 64), 4000, dtype=np.uint16)
    a[30:34, 30:34] = 400 + 2000
    b[30:34, 30:34] = 4000 + 2000
    out = stretch_channels({"R": a, "G": b})
    assert abs(float(out["R"][0, 0]) - float(out["G"][0, 0])) < 1e-6
    assert abs(float(out["R"][31, 31]) - float(out["G"][31, 31])) < 1e-6


# ------------------------------------------------------------------- render
def test_rgb_preview_is_a_jpeg_sized_to_the_request():
    s = rgb_session()
    got = s.rgb_preview(120)
    assert got is not None
    jpeg, meta = got
    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
    assert meta["width"] == 120 and meta["height"] > 0
    assert meta["frames"] == 6 and meta["mode"] == "rgb"


def test_nothing_stacked_renders_nothing():
    s = SessionStacker()
    s.start()
    assert s.rgb_preview() is None
    assert s.status()["has_image"] is False


def test_a_repeat_request_reuses_the_render():
    s = rgb_session()
    first = s.rgb_preview(200)
    second = s.rgb_preview(200)
    assert first is second, "the composite was re-rendered with nothing new to show"
    assert s.status()["render_age_s"] is not None


def test_a_different_size_is_not_served_from_the_cache():
    s = rgb_session()
    small = s.rgb_preview(120)
    large = s.rgb_preview(240)
    assert large is not None and large[1]["width"] == 240
    assert small[1]["width"] == 120
