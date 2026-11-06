# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.3 (#1113 part a; #780, #782): the session stack's memory budget.

#780. A mosaic with more panels than the budget holds used to release the panel
LEAST RECENTLY ADDED, which under a round-robin visit is always the panel the
rotation is about to come back to: every visit missed, every panel restarted
from one frame, and none ever built. The fix is that a released panel comes
back COARSER (sized to its share of the budget, ``budget / panels``) and the
victim is chosen by who was least recently VISITED, preferring the panels that
hold more than their share, so a rotating night converges on a stack per panel
instead of thrashing.

#782. A channel whose first frame was refused (no stars through that filter,
cloud) must not appear in ``status.channels`` with nothing in it: the strip
offers one chip per status channel, and a chip for an empty channel 404s.

Mutant (a byte backup in the scratch directory, restored byte-identically):

``M1-least-recently-added``  ``_enforce_budget`` is the body it had before this
    work: whole panels released oldest-add first until the budget fits, with no
    coarser factor remembered for the panel that comes back.
    ``test_a_rotating_four_panel_night_builds_a_stack_per_panel``:
    ``AssertionError: every visited panel has a stack of its own`` (``assert
    {'C', 'D'} == {'A', 'B', 'C', 'D'}``: A and B are released before the
    round ends and no panel ever builds).
"""
import math

import numpy as np

import astrodeck.imaging.sessionstack as sessionstack_mod
from astrodeck.imaging.sessionstack import SessionStacker

STARS = [(60, 70, 1.0), (180, 120, 0.7), (300, 200, 0.55),
         (420, 90, 0.4), (250, 330, 0.35)]
DITHERS = [(0, 0), (1.5, -2.0), (-2.0, 1.0), (3.0, 1.0), (2.0, -1.0)]
RUN = "run-1"
#: Four panels, each on its own part of the sky.
OFFSETS = {"A": (0.0, 0.0), "B": (60.0, 20.0), "C": (-50.0, -30.0),
           "D": (30.0, -40.0)}
#: The grid floor the tests run with: the frames here are 400x480, which the 2x
#: base binning already puts at 200x240, so 50 allows two further halvings.
MIN_SIDE = 50


def field(dx: float = 0.0, dy: float = 0.0, *, seed: int = 3,
          shape=(400, 480)) -> np.ndarray:
    """A synthetic star field plus an extended glow, shifted by (dx, dy). The
    stars are broad (sigma 6 px) so they survive the coarser grids the budget
    chooses: a real frame at the rig's plate scale is oversampled the same way."""
    rng = np.random.default_rng(seed)
    h, w = shape
    img = rng.normal(400.0, 6.0, shape)
    xs = np.arange(w) - (240 + dx)
    ys = (np.arange(h) - (200 + dy))[:, None]
    img += 3000.0 * np.exp(-(xs ** 2 + ys ** 2) / (2 * 110.0 ** 2))
    for x, y, b in STARS:
        xs = np.arange(w) - (x + dx)
        ys = (np.arange(h) - (y + dy))[:, None]
        img += b * 3_600_000.0 * np.exp(-(xs ** 2 + ys ** 2) / (2 * 6.0 ** 2)) \
            / (2 * math.pi * 36.0)
    return np.clip(img, 0, 65535).astype(np.uint16)


def feed(s: SessionStacker, panel: str, n: int, *, filt: str = "R") -> str:
    """The n-th sub of ``panel``, dithered a little from its neighbours."""
    ox, oy = OFFSETS[panel]
    dx, dy = DITHERS[n % len(DITHERS)]
    got = s.add(field(ox + dx, oy + dy, seed=2000 + 31 * n + ord(panel[0])),
                filt, 60.0, target=panel, session=RUN)
    assert got, f"panel {panel} sub {n} was refused: the test proves nothing"
    return got


def one_panel_bytes() -> int:
    probe = SessionStacker(min_side=MIN_SIDE)
    probe.start()
    feed(probe, "A", 0)
    return probe.held_bytes()


def frames_by_panel(s: SessionStacker) -> dict[str, int]:
    return {p["key"]: p["frames"] for p in s.status()["panels"]}


# ------------------------------------------------------------------- #780
def test_a_rotating_four_panel_night_builds_a_stack_per_panel():
    """THE ACCEPTANCE CASE. Four panels, a budget that holds two and a half of
    them, a round-robin visit order: the shape that released the next panel on
    every visit. Every panel must keep a stack that GROWS from round to round,
    and the accumulators must sit within the budget once the rotation has
    settled."""
    one = one_panel_bytes()
    budget = int(one * 2.5)
    s = SessionStacker(max_bytes=budget, min_side=MIN_SIDE)
    s.start()
    seen: list[dict[str, int]] = []
    for rnd in range(5):
        for panel in "ABCD":
            feed(s, panel, rnd)
        seen.append(frames_by_panel(s))

    last, before = seen[-1], seen[-2]
    assert set(last) == {"A", "B", "C", "D"}, \
        "every visited panel has a stack of its own"
    for panel in "ABCD":
        assert last[panel] > before[panel] >= 1, \
            f"panel {panel} did not grow across rounds: {seen}"
    assert max(last.values()) >= 3, \
        f"no panel ever built a stack under the rotation: {seen}"
    assert s.held_bytes() <= budget
    # Releasing is how a panel is shrunk, not how every visit ends: the record
    # names each panel at most once and the rotation stopped releasing.
    assert len(s.status()["evicted"]) <= 4
    assert seen[-2] != seen[0], seen


def test_a_released_panel_comes_back_coarser_and_the_rest_do_not_change():
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 2.5), min_side=MIN_SIDE)
    s.start()
    for panel in "ABCD":
        feed(s, panel, 0)
    feed(s, "A", 1)
    feed(s, "B", 1)
    grids = {p: s.status(panel=p)["downsample"] for p in "ABCD"}
    base = SessionStacker(min_side=MIN_SIDE)
    base.start()
    feed(base, "A", 0)
    base_factor = base.status()["downsample"]
    assert any(f > base_factor for f in grids.values()), \
        f"nothing was shrunk to make the panels fit: {grids}"
    assert grids["C"] == grids["D"] == base_factor, \
        "the panels the budget did not touch keep their grid"


def test_a_panel_is_never_coarsened_past_the_floor():
    """A grid whose long edge is already at the floor cannot be halved again:
    the panel is released and returns at the same grid, which is what the old
    budget did and what a budget smaller than the floor can only do."""
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 1.5), min_side=240)
    s.start()
    feed(s, "A", 0)
    base = s.status()["downsample"]
    feed(s, "B", 0)             # A is released
    assert s.status()["evicted"] == ["A"]
    feed(s, "A", 1)             # and returns, one frame, on the same grid
    st = s.status(panel="A")
    assert st["frames"] == 1 and st["downsample"] == base


def test_the_coarser_grid_is_sized_to_the_panels_share_of_the_budget():
    """The released panel restarts on the finest grid whose cost fits
    ``budget / panels``: not the next halving by habit, and not a coarser one
    than the share needs."""
    one = one_panel_bytes()
    budget = int(one * 2.5)
    s = SessionStacker(max_bytes=budget, min_side=MIN_SIDE)
    s.start()
    for panel in "ABC":
        feed(s, panel, 0)
    assert s.status()["evicted"] == ["A"]
    feed(s, "A", 1)
    base = s.status(panel="C")["downsample"]
    ratio = s.status(panel="A")["downsample"] / base
    share = budget // 3
    assert ratio > 1 and one / ratio ** 2 <= share,         "the restarted panel does not fit its share"
    assert ratio == 2 or one / (ratio / 2) ** 2 > share,         "a finer grid than needed would have fit the share"


def test_the_panel_the_operator_is_looking_at_is_not_the_victim():
    """LEAST RECENTLY VISITED. A read of a panel is a visit: the picture the
    operator has pinned is not the one the budget releases just because the
    last frame fed to it is old."""
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 2.5), min_side=MIN_SIDE)
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    assert s.status(panel="A")["frames"] == 1       # the operator looks at A
    feed(s, "C", 0)
    assert s.status()["evicted"] == ["B"], \
        "A was added first but visited last: B is the victim"


def test_a_panel_over_its_share_goes_before_one_within_it():
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 3.5), min_side=MIN_SIDE)
    s.start()
    feed(s, "B", 0)             # one channel: within its share
    feed(s, "A", 0, filt="R")
    feed(s, "A", 1, filt="G")   # two channels: over its share
    feed(s, "C", 0)
    assert s.status()["evicted"] == ["A"], \
        "B is the least recently visited, but A holds more than its share"
    assert s.status(panel="B")["frames"] == 1


def test_a_reset_forgets_the_coarser_grids():
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 2.5), min_side=MIN_SIDE)
    s.start()
    for panel in "ABC":
        feed(s, panel, 0)
    s.reset(session=RUN)
    feed(s, "A", 1)
    probe = SessionStacker(min_side=MIN_SIDE)
    probe.start()
    feed(probe, "A", 0)
    assert s.status(panel="A")["downsample"] == probe.status()["downsample"]


def test_the_release_is_said_once_and_says_the_panel_comes_back_coarser(monkeypatch):
    lines: list[str] = []

    class Spy:
        def log(self, level, message, source="hub", **kw):
            lines.append(message)

    monkeypatch.setattr(sessionstack_mod, "bus", Spy())
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 2.5), min_side=MIN_SIDE)
    s.start()
    for panel in "ABC":
        feed(s, panel, 0)
    assert len(lines) == 1 and "the A panel" in lines[0], lines
    assert "coarser" in lines[0], lines[0]


# ------------------------------------------------------------------- #782
def blank() -> np.ndarray:
    """A frame with nothing to register on: cloud, or a filter with no stars."""
    return np.full((400, 480), 400, dtype=np.uint16)


def test_a_refused_first_frame_in_a_new_channel_leaves_no_channel():
    s = SessionStacker(min_side=MIN_SIDE)
    s.start()
    feed(s, "A", 0, filt="R")
    assert s.add(blank(), "G", 60.0, target="A", session=RUN) is None
    st = s.status()
    assert [c["channel"] for c in st["channels"]] == ["R"], \
        "a channel with nothing stacked in it is offered as a chip that 404s"
    assert st["mode"] == "rgb" and st["frames"] == 1


def test_the_refusal_is_still_counted_and_follows_the_channel_when_it_lands():
    s = SessionStacker(min_side=MIN_SIDE)
    s.start()
    feed(s, "A", 0, filt="R")
    assert s.add(blank(), "G", 60.0, target="A", session=RUN) is None
    assert s.status()["rejected"] == 1, \
        "the stack can still say a channel is being refused"
    feed(s, "A", 1, filt="G")
    st = s.status()
    by = {c["channel"]: c for c in st["channels"]}
    assert by["G"]["frames"] == 1 and by["G"]["rejected"] == 1
    assert st["rejected"] == 1, "counted once, not once per place it is shown"


def test_a_refused_frame_in_an_existing_channel_is_counted_as_before():
    s = SessionStacker(min_side=MIN_SIDE)
    s.start()
    feed(s, "A", 0, filt="R")
    assert s.add(blank(), "R", 60.0, target="A", session=RUN) is None
    st = s.status()
    assert st["channels"][0]["rejected"] == 1 and st["rejected"] == 1
