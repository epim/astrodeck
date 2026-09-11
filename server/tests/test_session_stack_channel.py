"""Session stack: rendering ONE channel, not the composite with a tint on it.

The stacker has always kept one accumulator per channel -- ``self._stacks`` is
a ``LiveStacker`` per ``channel_for(filter)`` and has been since the feature
landed. What did not exist was a way to LOOK at one of them: ``rgb_preview``
only ever composited, so "show me just Ha" could only ever be answered in the
browser, by tinting or masking the colour composite. That answer is a lie in
the one case the operator asks the question for -- checking whether the Ha subs
are worth keeping -- because the composite has already mixed Ha with Sii into
red and stretched all of it on the widest channel's scale.

So these tests are mostly about the ways a fake single-channel view passes: it
returns the composite when the channel is empty (indistinguishable while the
UI tints it), it serves a black frame for a channel that has nothing in it, and
it disagrees with the composite about which accumulator an operator's filter
name means. Each has a test whose name says which.
"""
import io

import math
import numpy as np
from PIL import Image

from astrodeck.imaging.sessionstack import CHANNEL_ORDER, SessionStacker

STARS = [(60, 70, 1.0), (180, 120, 0.7), (300, 200, 0.55),
         (420, 90, 0.4), (250, 330, 0.35)]


def field(dx: float = 0.0, dy: float = 0.0, *, scale: float = 1.0,
          seed: int = 3, shape=(400, 480), glow: float = 3000.0) -> np.ndarray:
    """A star field plus an extended glow, shifted by (dx, dy).

    Same synthetic frame the composite's own tests use: the glow is what makes
    a brightness assertion mean anything, because over a bare star field the
    frame mean is background noise whatever the stars are doing.
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


def narrowband(**kw) -> SessionStacker:
    """Two Ha subs and one Oiii, all of the same field. No Sii, deliberately.

    Every ``add`` is asserted: a stacker that quietly refused all three would
    make every assertion below pass on an empty stack.
    """
    s = SessionStacker(**kw)
    s.start()
    assert s.add(field(seed=11), "Ha", 300.0, target="NGC 7000") == "Ha"
    assert s.add(field(1.5, -2.0, seed=12), "Ha", 300.0, target="NGC 7000") == "Ha"
    assert s.add(field(-1.0, 1.0, scale=0.4, seed=13), "Oiii", 300.0,
                 target="NGC 7000") == "Oiii"
    return s


def decode(payload) -> Image.Image:
    assert payload is not None
    jpeg, _meta = payload
    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
    return Image.open(io.BytesIO(jpeg))


# ----------------------------------------------------------------- the render
def test_a_stacked_channel_renders_as_a_single_band():
    # One channel is one measurement of one bandpass. Delivering it as RGB
    # would be three copies of the same plane and an invitation for the client
    # to tint it, which is the thing this route exists to replace.
    s = narrowband()
    got = s.channel_preview("Ha", 200)
    img = decode(got)
    assert img.getbands() == ("L",), img.getbands()
    assert img.width == 200 and img.height > 0
    assert not np.allclose(np.asarray(img), 0), "a black frame is not a render"


def test_the_meta_reports_that_channels_counts_not_the_nights():
    # The caption under a single-channel view says how much went into THAT
    # channel. The stack as a whole has three frames and 900 s; Ha has two.
    s = narrowband()
    _jpeg, meta = s.channel_preview("Ha", 200)
    assert meta["channel"] == "Ha"
    assert meta["frames"] == 2
    assert meta["integrated_s"] == 600.0
    assert meta["rejected"] == 0
    # ...while the whole-stack numbers are still reachable, on `channels`.
    assert {c["channel"] for c in meta["channels"]} == {"Ha", "Oiii"}


def test_an_unstacked_channel_is_a_refusal_not_the_composite():
    # THE vacuity guard for this whole file. A `channel_preview` that fell back
    # to the composite would pass every other test here -- the bytes would be
    # bytes, the meta would be meta -- and would put the composite on screen
    # under an "Sii" label. A black frame is the other wrong answer: at 3am it
    # reads as a dead sensor. Nothing stacked in that channel means 404.
    s = narrowband()
    assert "Sii" not in {c.channel for c in s.channels()}
    assert s.channel_preview("Sii", 200) is None
    assert s.channel_preview("L", 200) is None


def test_an_empty_channel_is_a_refusal_not_the_L_stack():
    # `channel_for("")` is "L" -- that fold is right for a frame from a mono
    # camera with no wheel, and wrong here: an empty `?channel=` means the
    # caller asked for the composite, and answering it with one filter's pixels
    # is a different picture under the same URL.
    s = SessionStacker()
    s.start()
    assert s.add(field(seed=21), "L", 120.0, target="M31") == "L"
    assert s.channel_preview("", 200) is None
    assert s.channel_preview("   ", 200) is None
    assert s.channel_preview(None, 200) is None
    assert s.channel_preview("L", 200) is not None


# ------------------------------------------------------------- the alias fold
def test_the_operators_filter_name_finds_the_same_accumulator():
    # Accumulators are keyed by CHANNEL, not by the string in the wheel. "Ha",
    # "H-alpha" and "HALPHA" are one stack, so they have to be one picture --
    # if the query string resolved differently from `add`, a rig whose wheel
    # says "H-alpha" would 404 on its own data.
    s = narrowband()
    canonical, meta = s.channel_preview("Ha", 200)
    for spelling in ("H-alpha", "HALPHA", "  ha  ", "H_alpha"):
        got = s.channel_preview(spelling, 200)
        assert got is not None, spelling
        assert got[0] == canonical, spelling
        assert got[1]["channel"] == "Ha" == meta["channel"], spelling


def test_an_unknown_filter_name_folds_the_same_way_add_does():
    # A dual-band filter, a CLS, whatever was typed: `add` put those frames in
    # L, so asking for them by that name has to reach L rather than 404.
    s = SessionStacker()
    s.start()
    assert s.add(field(seed=31), "L-eXtreme", 120.0, target="M31") == "L"
    got = s.channel_preview("L-eXtreme", 200)
    assert got is not None
    assert got[1]["channel"] == "L"
    assert got[0] == s.channel_preview("L", 200)[0]


# ------------------------------------------------------- not the composite
def test_the_channel_render_is_not_the_composite():
    # The composite has already mixed Ha into red with whatever else feeds it
    # and stretched every channel on the widest one's range. A single-channel
    # view that came back byte-identical to it would be the CSS-tint answer
    # wearing a server route's clothes.
    s = narrowband()
    composite = s.rgb_preview(200)
    channel = s.channel_preview("Ha", 200)
    assert composite is not None and channel is not None
    assert channel[0] != composite[0]
    assert decode(composite).getbands() == ("R", "G", "B")
    assert decode(channel).getbands() == ("L",)


def test_two_channels_of_the_same_session_are_different_pictures():
    # Ha and Oiii here are the same field at different brightness. Identical
    # bytes would mean the key was being ignored somewhere between the query
    # string and the accumulator.
    s = narrowband()
    ha = s.channel_preview("Ha", 200)
    oiii = s.channel_preview("Oiii", 200)
    assert ha is not None and oiii is not None
    assert ha[0] != oiii[0]
    assert oiii[1]["channel"] == "Oiii" and oiii[1]["frames"] == 1


# -------------------------------------------------------------------- cache
def test_a_repeat_request_reuses_the_channel_render():
    s = narrowband()
    first = s.channel_preview("Ha", 200)
    second = s.channel_preview("Ha", 200)
    assert first is second, "the channel was re-rendered with nothing new to show"


def test_the_composite_and_the_channel_do_not_evict_each_other():
    # One slot would make a client on the composite and a client on Ha
    # re-render each other's picture on every poll, which is the whole reason
    # the cache is keyed by channel.
    s = narrowband()
    composite = s.rgb_preview(200)
    ha = s.channel_preview("Ha", 200)
    assert s.rgb_preview(200) is composite
    assert s.channel_preview("Ha", 200) is ha


def test_a_different_size_is_not_served_from_the_channel_cache():
    s = narrowband()
    small = s.channel_preview("Ha", 120)
    large = s.channel_preview("Ha", 240)
    assert small[1]["width"] == 120
    assert large[1]["width"] == 240


def test_a_new_frame_makes_a_new_channel_render():
    # `min_render_interval_s=0` so the test measures the seq rule rather than
    # the rate limit; on the rig the two together are what stop a polling phone
    # from re-rendering a picture that has not changed.
    s = narrowband(min_render_interval_s=0.0)
    before = s.channel_preview("Ha", 200)
    seq_before = s.seq
    assert s.add(field(0.5, 0.5, seed=14), "Ha", 300.0, target="NGC 7000") == "Ha"
    assert s.seq > seq_before
    after = s.channel_preview("Ha", 200)
    assert after is not None and after[0] != before[0]
    assert after[1]["frames"] == 3


def test_reset_empties_the_per_channel_cache():
    # Reset throws the pixels away. A cache entry that outlived it would keep
    # serving last target's Ha under the new target's name.
    s = narrowband()
    assert s.channel_preview("Ha", 200) is not None
    s.reset()
    assert s.channel_preview("Ha", 200) is None
    assert s._cache == {} and s._cache_stamp == {}


def test_the_cache_cannot_outgrow_one_entry_per_channel():
    # Keys are None plus the output of `channel_for`, which is always one of
    # CHANNEL_ORDER, so this is a bound the code cannot cross rather than one
    # it is trusted not to. Asserting it here means a future alias that folded
    # onto an eighth channel would be caught by the bound, not by a box that
    # ran out of memory in the small hours.
    s = SessionStacker(min_render_interval_s=0.0)
    s.start()
    for i, ch in enumerate(CHANNEL_ORDER):
        assert s.add(field(seed=40 + i), ch, 60.0, target="M31") == ch
    s.rgb_preview(120)
    for ch in CHANNEL_ORDER:
        assert s.channel_preview(ch, 120) is not None, ch
    assert len(s._cache) == len(CHANNEL_ORDER) + 1
    assert set(s._cache) == {None, *CHANNEL_ORDER}
