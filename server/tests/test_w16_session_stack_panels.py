# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""One live stack per mosaic panel, kept across visits (#172 part A, WP-121).

``SessionStacker`` used to hold ONE picture whose identity was (target, run), so
a rotating mosaic restarted the Monitor picture at every panel hop and the live
stack never built: panel A's pixels were thrown away the moment the engine
moved to panel B, and thrown away again when it came back. Panels are now
slots. The identity that remains is the RUN (a second run is a new picture for
every panel), and the picture shown by default is the FOREGROUND panel, the one
the most recent accepted frame fed, so a one-target night answers as it always
did.

Mutants. Each is a one-line change to the named source file, run from a byte
backup inside the worktree, restored byte-identically (sha256 compared) and
grepped gone. The failing assertion is quoted verbatim, from the test named.
The runner that applied them is not part of the tree.

``M1-reseed-on-target``  restore the old reseed: in ``add``, drop every slot
    when the panel key differs from the foreground's (sessionstack.py).
    ``test_returning_to_a_panel_keeps_its_stack``:  ``assert 1 == 2``
    (panel A came back at one frame, not two; 21 of 30 tests here go red).
``M2-key-by-name``  ``_panel_key`` returns the NAME even when an id is given.
    ``test_two_targets_with_one_name_are_two_panels``:
    ``AssertionError: assert ['A'] == ['t-1', 't-2']``
``M3-keep-slots-on-new-run``  a run change adopts the run id and clears nothing.
    ``test_a_new_run_id_drops_every_panel``:
    ``AssertionError: last run's panels survived into the new run``
    (``assert ['A', 'B'] == ['B']``)
``M4-no-eviction``  ``_enforce_budget``'s loop never runs.
    ``test_the_budget_evicts_the_least_recently_added_panel``:
    ``assert [] == ['B']`` on ``evicted``.
``M5-evict-oldest-created``  the victim is ``victims[0]`` (creation order), not
    the smallest ``seq``.
    ``test_the_budget_evicts_the_least_recently_added_panel``:
    ``assert ['A'] == ['B']`` (A was re-fed after B, so A is not the victim).
``M6-evict-the-fed-panel``  the victim search includes the panel just fed.
    ``test_the_panel_being_fed_is_never_evicted``:
    ``AssertionError: assert ([] == ['A']`` (the only panel was released under
    its own add).
``M7-dedup-foreground-only``  ``_holds`` looks at the foreground slot only.
    ``test_a_sub_offered_to_two_panels_is_stacked_once``:
    ``AssertionError: assert 'R' is None`` (the same file landed in two panels).
``M8-backfill-target-guard``  restore ``(stacker.target, stacker.session) !=
    wanted`` in ``run_backfill`` (stackbackfill.py).
    ``test_a_backfill_over_two_panels_does_not_abort_on_the_hop``:
    ``assert 'the stack mo...o another run' == ''`` (aborted at the first hop).
``M9-backfill-one-target``  ``plan_backfill`` restricts to the last frame's
    target again.
    ``test_the_plan_covers_every_target_of_the_run_in_capture_order``:
    ``assert ['B', 'B'] == ['A', 'B', 'A', 'B']``
``M10-global-seq-cache``  ``_cached`` compares against the stacker's ``_seq``
    instead of the slot's.
    ``test_each_panel_keeps_its_own_render_cache``: the
    ``rgb_preview(200, panel="A") is first_a`` assertion fails (a frame landing
    on B rebuilt A's render).
``M11-hub-no-target-id``  ``Hub.session_stack_add`` passes ``target_id=None``.
    ``test_the_hub_keys_the_panel_by_the_targets_id``:
    ``assert a == "R" and b == "R"`` fails with ``R and None == 'R'`` (the
    second same-named target was folded into the first's panel and refused).
``M13-ghost-is-the-foreground``  ``_resolve`` answers an unknown panel with the
    foreground instead of None.
    ``test_an_unknown_panel_reads_empty_and_renders_nothing``:
    ``AssertionError: assert (True and not True)`` (``has_panel('ghost')``).
``M17-empty-panel-on-refusal``  a refused first frame inserts its empty slot.
    ``test_a_refused_first_frame_makes_no_panel_and_moves_nothing``:
    ``AssertionError: a panel with nothing stacked is not a panel``
    (``assert ['A', 'B'] == ['A']``)
``M18-evicted-logged-every-time``  the once-per-name guard on the eviction log
    is ``if True``.
    ``test_an_eviction_is_logged_once_per_panel``:  ``assert 4 == 2``
``M19-backfill-drops-target-id``  ``run_backfill`` passes ``target_id=None``
    (stackbackfill.py).
    ``test_a_backfill_over_two_panels_does_not_abort_on_the_hop``:
    ``assert ['A', 'B'] == ['cc6154d83ad...']`` (keyed by name, not id); and in
    test_session_stack_backfill.py
    ``test_the_live_path_and_a_backfill_do_not_both_take_a_frame``:
    ``AssertionError: the backfill re-stacked a sub the live path had already
    folded in`` (``assert 2 == 3``).

Four more, found by the independent verifier as behaviours with no mutant (the
mutant ran green before the test beside it was written):

``M27-evicted-survives-run``  ``_drop_all`` no longer clears ``_evicted``.
    ``test_a_new_run_and_a_reset_forget_what_the_last_one_released``:
    ``AssertionError: last run's release was carried into the new run``
    (``assert ['A'] == []``).
``M28-status-seq-global``  ``_status_locked`` reports the stacker-wide ``_seq``
    instead of the panel's own.
    ``test_a_panels_seq_moves_only_when_that_panel_does``:  ``assert (4 != 4)``
    (panel A and panel B read the same number).
``M29-foreground-add-by-name``  ``add`` with no ``target`` re-keys the
    foreground panel by its NAME.
    ``test_an_add_naming_no_panel_feeds_the_foreground_panel``:
    ``AssertionError: a frame that named no panel made a panel of its own``
    (``assert ['t-1', 't-2', 'B'] == ['t-1', 't-2']``).
``M30-name-lookup-first-not-latest``  ``_resolve`` answers a shared name with
    the first panel of that name instead of the most recently added.
    ``test_a_name_shared_by_two_panels_answers_with_the_latest_of_them``:
    ``AssertionError: the name answered with the older panel, not the one being
    fed`` (``assert 2 == 3``).
"""
import io
import math
import threading
import time

import numpy as np
from PIL import Image

import astrodeck.imaging.sessionstack as sessionstack_mod
from astrodeck.imaging.sessionstack import SessionStacker
from astrodeck.imaging.stackbackfill import plan_backfill, run_backfill
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame

STARS = [(60, 70, 1.0), (180, 120, 0.7), (300, 200, 0.55),
         (420, 90, 0.4), (250, 330, 0.35)]
DITHERS = [(0, 0), (1.5, -2.0), (-2.0, 1.0), (3.0, 1.0), (2.0, -1.0)]
RUN = "run-1"


def field(dx: float = 0.0, dy: float = 0.0, *, scale: float = 1.0,
          seed: int = 3, shape=(400, 480), glow: float = 3000.0) -> np.ndarray:
    """A star field plus an extended glow, shifted by (dx, dy). The same
    synthetic frame every session-stack test file uses."""
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


#: Where each panel's field sits on the sky, as a pixel offset. Different
#: panels look at different places, so the pictures they make differ for a
#: reason a viewer could see (a co-added pair would show both).
PANEL_OFFSET = {"A": (0.0, 0.0), "B": (60.0, 20.0), "C": (-50.0, -30.0)}


def feed(s: SessionStacker, panel: str, n: int, *, run: str = RUN,
         filt: str = "R", key: str | None = None, ident: str | None = None):
    """The n-th sub of ``panel``: dithered a little from its neighbours, so
    registration has something to measure, and on its own part of the sky."""
    ox, oy = PANEL_OFFSET.get(panel, (0.0, 0.0))
    dx, dy = DITHERS[n % len(DITHERS)]
    got = s.add(field(ox + dx, oy + dy, seed=1000 + 17 * n + ord(panel[0])),
                filt, 60.0, target=panel, target_id=ident, session=run,
                key=key)
    assert got, f"panel {panel} sub {n} was refused: the test proves nothing"
    return got


def decode_gray(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("L"),
                      dtype=np.float32)


# ------------------------------------------------------------ one per panel
def test_returning_to_a_panel_keeps_its_stack():
    """THE headline: A, B, A. Before, the hop to B dropped A's pixels and the
    return to A started a one-frame picture."""
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    feed(s, "A", 1)
    assert s.status(panel="A")["frames"] == 2
    assert s.status(panel="B")["frames"] == 1
    feed(s, "B", 1)
    assert s.status(panel="A")["frames"] == 2
    assert s.status(panel="B")["frames"] == 2


def test_panels_lists_every_panel_in_first_seen_order_with_its_own_numbers():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    feed(s, "A", 1)
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["A", "B"], \
        "first-seen order, so a chip row does not reshuffle at every hop"
    by = {p["key"]: p for p in st["panels"]}
    assert by["A"]["target"] == "A" and by["A"]["frames"] == 2
    assert by["A"]["integrated_s"] == 120.0
    assert by["B"]["frames"] == 1 and by["B"]["integrated_s"] == 60.0
    # The most recent add is the foreground, and its seq is the highest: that
    # is how a client works out which chip is "the latest".
    assert by["A"]["seq"] > by["B"]["seq"]
    assert set(by["A"]) == {"key", "target", "frames", "integrated_s", "seq"}


def test_the_two_previews_differ_and_each_counts_its_own_panel():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    feed(s, "A", 1)
    ja, ma = s.rgb_preview(240, panel="A")
    jb, mb = s.rgb_preview(240, panel="B")
    assert ma["frames"] == 2 and mb["frames"] == 1
    assert ma["target"] == "A" and mb["target"] == "B"
    diff = float(np.abs(decode_gray(ja) - decode_gray(jb)).mean())
    assert diff > 2.0, \
        f"panel A and panel B rendered the same picture (mean diff {diff:.2f})"


def test_the_default_view_is_the_foreground_panel():
    """No panel asked = the panel the latest accepted frame fed, so a client
    that has never heard of panels sees what it always saw."""
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    assert s.status()["target"] == "B" and s.target == "B"
    assert s.status()["frames"] == s.status(panel="B")["frames"] == 1
    assert s.rgb_preview(200)[0] == s.rgb_preview(200, panel="B")[0]
    feed(s, "A", 1)
    assert s.status()["target"] == "A" and s.status()["frames"] == 2
    assert s.channel_preview("R", 200)[1]["frames"] == 2


def test_a_one_target_night_answers_with_the_old_shape_plus_two_keys():
    s = SessionStacker()
    s.start()
    feed(s, "M42", 0)
    feed(s, "M42", 1)
    st = s.status()
    old = {"enabled", "target", "session", "seq", "channels", "frames",
           "integrated_s", "rejected", "mode", "downsample", "has_image",
           "render_age_s", "backfill"}
    assert old <= set(st), old - set(st)
    assert set(st) - old == {"panels", "evicted"}
    assert len(st["panels"]) == 1 and st["evicted"] == []
    assert st["seq"] == s.seq and st["frames"] == 2 and st["target"] == "M42"
    assert st["session"] == RUN


def test_two_targets_with_one_name_are_two_panels():
    """Ids are unique where names can repeat in a plan with no group: keyed by
    name these two would be co-added into one picture of nothing."""
    s = SessionStacker()
    s.start()
    feed(s, "A", 0, ident="t-1")
    feed(s, "A", 0, ident="t-2")
    feed(s, "A", 1, ident="t-1")
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["t-1", "t-2"]
    assert [p["target"] for p in st["panels"]] == ["A", "A"]
    assert s.status(panel="t-1")["frames"] == 2
    assert s.status(panel="t-2")["frames"] == 1


def test_a_panel_is_found_by_key_first_then_by_name():
    s = SessionStacker()
    s.start()
    feed(s, "Ha", 0, ident="t-9")
    feed(s, "Oiii", 0, ident="t-8")
    # by key
    assert s.status(panel="t-9")["target"] == "Ha"
    # by name (the hand-typed URL)
    assert s.status(panel="Ha")["frames"] == 1
    assert s.rgb_preview(120, panel="Oiii") is not None
    # the panel is the second positional argument too, ahead of `quality`
    assert s.rgb_preview(120, "Oiii") is s.rgb_preview(120, panel="Oiii")
    assert s.channel_preview("R", 120, "Oiii") is s.channel_preview(
        "R", 120, panel="Oiii") is not None
    assert s.rgb_preview(120, quality=70) is not None
    # a name that is also somebody else's key loses to the key
    feed(s, "t-9", 0, ident="t-7")
    assert s.status(panel="t-9")["target"] == "Ha"


def test_an_unknown_panel_reads_empty_and_renders_nothing():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    assert s.has_panel("A") and not s.has_panel("ghost")
    st = s.status(panel="ghost")
    assert st["frames"] == 0 and st["has_image"] is False
    assert st["channels"] == [] and st["target"] == "ghost"
    assert [p["key"] for p in st["panels"]] == ["A"], \
        "the panel list is the stack's, whichever panel was asked about"
    assert s.rgb_preview(120, panel="ghost") is None
    assert s.channel_preview("R", 120, panel="ghost") is None
    assert s.compose(panel="ghost") is None


def test_a_refused_first_frame_makes_no_panel_and_moves_nothing():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    seq = s.seq
    flat = np.full((400, 480), 400, dtype=np.uint16)
    assert s.add(flat, "R", 60.0, target="B", session=RUN) is None
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["A"], \
        "a panel with nothing stacked is not a panel"
    assert st["target"] == "A" and s.seq == seq


def test_each_panel_keeps_its_own_render_cache():
    s = SessionStacker(min_render_interval_s=0.0)
    s.start()
    feed(s, "A", 0)
    first_a = s.rgb_preview(200, panel="A")
    feed(s, "B", 0)
    # B landing is not news about A: A's picture is reused, not rebuilt.
    assert s.rgb_preview(200, panel="A") is first_a
    feed(s, "A", 1)
    again = s.rgb_preview(200, panel="A")
    assert again is not first_a and again[1]["frames"] == 2
    assert s.rgb_preview(200, panel="B")[1]["frames"] == 1


def test_a_sub_offered_to_two_panels_is_stacked_once():
    """The de-dup is by file, whichever panel claims it: a backfill that names
    the panel differently from the live path must not fold one photograph into
    two pictures."""
    s = SessionStacker()
    s.start()
    f = field(seed=5)
    assert s.add(f, "R", 60.0, target="A", target_id="a", session=RUN,
                 key="/x/1.fits") == "R"
    # Another panel takes the foreground, so the panel that holds the frame is
    # NOT the foreground when it is offered again: a check that looked only at
    # the foreground, or only at the panel being fed, would miss it.
    assert s.add(field(60.0, 20.0, seed=6), "R", 60.0, target="C",
                 target_id="c", session=RUN, key="/x/2.fits") == "R"
    assert s.add(f, "R", 60.0, target="B", target_id="b", session=RUN,
                 key="/x/1.fits") is None
    assert s.has_frame("/x/1.fits")
    assert [p["key"] for p in s.status()["panels"]] == ["a", "c"]


# --------------------------------------------------------------- run / reset
def test_a_new_run_id_drops_every_panel():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    feed(s, "A", 1)
    assert s.status()["session"] == RUN
    feed(s, "B", 1, run="run-2")
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["B"], \
        "last run's panels survived into the new run"
    assert st["frames"] == 1 and st["session"] == "run-2"
    assert s.status(panel="A")["frames"] == 0


def test_a_second_run_on_the_same_panel_is_still_a_new_picture():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "A", 1)
    feed(s, "A", 2, run="run-2")
    assert s.status(panel="A")["frames"] == 1


def test_reset_and_stop_release_every_panel_and_the_bytes():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    assert s.held_bytes() > 0
    s.reset(s.target, s.session)
    st = s.status()
    assert st["panels"] == [] and st["frames"] == 0 and s.held_bytes() == 0
    assert st["session"] == RUN, "the operator's Reset keeps the run id"
    assert st["target"] == "B", "...and the caption of the picture it cleared"
    feed(s, "A", 0)
    feed(s, "B", 0)
    st = s.stop()
    assert st["panels"] == [] and st["enabled"] is False
    assert s.held_bytes() == 0 and st["evicted"] == []
    assert s.rgb_preview() is None and s.rgb_preview(panel="A") is None


def test_reset_clears_every_panels_cache():
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    assert s.rgb_preview(120, panel="A") is not None
    assert s.channel_preview("R", 120, panel="B") is not None
    s.reset()
    assert s.rgb_preview(120, panel="A") is None
    assert s.channel_preview("R", 120, panel="B") is None
    assert s.status()["render_age_s"] is None


# ------------------------------------------------------------------- budget
def _one_panel_bytes() -> int:
    probe = SessionStacker()
    probe.start()
    feed(probe, "A", 0)
    return probe.held_bytes()


def test_held_bytes_is_measured_from_the_accumulators():
    # (400, 480) at the 2x floor is 200 x 240; three float32 planes (sum, sum of
    # squares, coverage) per channel, one channel here.
    assert _one_panel_bytes() == 200 * 240 * 4 * 3


def test_the_default_budget_is_600_mb():
    assert SessionStacker().max_bytes == 600_000_000


def test_the_budget_evicts_the_least_recently_added_panel():
    one = _one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 2.5))
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    feed(s, "A", 1)             # A was created first but fed LAST
    assert s.status()["evicted"] == []
    feed(s, "C", 0)             # a third panel does not fit
    st = s.status()
    assert st["evicted"] == ["B"], \
        "the victim is the panel whose last add is oldest, not the oldest panel"
    assert [p["key"] for p in st["panels"]] == ["A", "C"]
    assert s.held_bytes() <= int(one * 2.5)
    assert st["target"] == "C"
    assert s.status(panel="A")["frames"] == 2, "A paid nothing for B's eviction"


def test_the_panel_being_fed_is_never_evicted():
    # A budget smaller than one panel: the picture being built is the one thing
    # that cannot be released, or the stack could never hold anything.
    s = SessionStacker(max_bytes=1)
    s.start()
    feed(s, "A", 0)
    feed(s, "A", 1)
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["A"] and st["frames"] == 2
    assert st["evicted"] == []
    feed(s, "B", 0)
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["B"], \
        "the budget holds one panel, and it is the one being fed"
    assert st["evicted"] == ["A"]


def test_an_evicted_panel_reseeds_on_its_next_visit():
    one = _one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 1.5))
    s.start()
    feed(s, "A", 0)
    feed(s, "A", 1)
    feed(s, "B", 0)             # A is released
    assert s.status(panel="A")["frames"] == 0
    feed(s, "A", 2)             # A returns: today's reseed, for A only
    st = s.status()
    assert s.status(panel="A")["frames"] == 1
    # A's return released B in its turn (the budget holds one panel here), and
    # the record keeps both: A lost its two frames, B lost its one.
    assert [p["key"] for p in st["panels"]] == ["A"]
    assert st["evicted"] == ["A", "B"]


def test_an_eviction_is_logged_once_per_panel(monkeypatch):
    lines: list[tuple[str, str]] = []

    class Spy:
        def log(self, level, message, source="hub", **kw):
            lines.append((level, message))

    monkeypatch.setattr(sessionstack_mod, "bus", Spy())
    one = _one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 1.5))
    s.start()
    for panel, n in [("A", 0), ("B", 0), ("A", 1), ("B", 1), ("A", 2)]:
        feed(s, panel, n)
    # Five visits, four of them evictions, two panels: said twice.
    assert len(lines) == 2, lines
    assert all(level == "warning" for level, _ in lines)
    assert "the A panel" in lines[0][1] and "the B panel" in lines[1][1], lines
    assert all("MB" in m for _, m in lines)


def test_the_budget_counts_every_channel_of_every_panel():
    # A filter wheel multiplies the cost: two channels in one panel is two
    # panels' worth of accumulators against the same budget.
    one = _one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 2.5))
    s.start()
    feed(s, "A", 0, filt="R")
    feed(s, "A", 1, filt="G")
    assert s.held_bytes() == 2 * one
    feed(s, "B", 0, filt="R")       # 3 * one: A is released
    assert [p["key"] for p in s.status()["panels"]] == ["B"]
    assert s.status()["evicted"] == ["A"]


# ----------------------------------------------------------------- backfill
def write_fits(path, data, *, filter_name="R", exposure_s=60.0):
    from astrodeck.devices.base import CameraFrame
    from astrodeck.imaging.fitsio import save_fits
    frame = CameraFrame(data=data, exposure_s=exposure_s, gain=100, offset=30,
                        binning=1, bayer_pattern=None, temperature_c=-10.0,
                        timestamp=1_757_000_000.0)
    return save_fits(frame, path, target="x", filter_name=filter_name)


def two_panel_session(tmp_path, order=("A", "B", "A", "B"), run=RUN):
    """A ledger whose frames visit two panels in ``order``, files on disk."""
    targets = {name: Target(name=name, ra_hours=5.5 + 0.1 * i, dec_deg=-5.0,
                            steps=[ExposureStep(filter="R", exposure_s=60.0,
                                                count=99)])
               for i, name in enumerate(sorted(set(order)))}
    plan = SequencePlan(name="panels", targets=list(targets.values()))
    frames = []
    seen = {n: 0 for n in targets}
    for i, name in enumerate(order):
        t = targets[name]
        dx, dy = DITHERS[seen[name] % len(DITHERS)]
        ox, oy = PANEL_OFFSET.get(name, (0.0, 0.0))
        seen[name] += 1
        path = tmp_path / f"Light_{name}_R_2026-09-10_2200{i:02d}_{i}.fits"
        write_fits(path, field(ox + dx, oy + dy, seed=300 + i))
        frames.append(SessionFrame(ts=1000.0 + i, night=run, target_id=t.id,
                                   step_id=t.steps[0].id, path=str(path),
                                   auto_accepted=True, override=None))
    return Session(plan=plan, nights=[run], frames=frames), targets


def test_the_plan_covers_every_target_of_the_run_in_capture_order(tmp_path):
    session, targets = two_panel_session(tmp_path)
    items = plan_backfill(session)
    assert [i.target for i in items] == ["A", "B", "A", "B"]
    assert [i.target_id for i in items] == [targets[n].id
                                            for n in ("A", "B", "A", "B")]
    assert {i.session for i in items} == {RUN}
    # `target=` still restricts, by name or by id.
    assert [i.target for i in plan_backfill(session, target="B")] == ["B", "B"]
    assert len(plan_backfill(session, target=targets["A"].id)) == 2
    # And a stacker in hand drops only what it holds, whichever panel.
    s = SessionStacker()
    s.start()
    first = items[0]
    s.add(field(seed=300), "R", 60.0, target=first.target,
          target_id=first.target_id, session=RUN, key=str(first.path))
    rest = plan_backfill(session, stacker=s)
    assert [i.target for i in rest] == ["B", "A", "B"]


def test_a_backfill_over_two_panels_does_not_abort_on_the_hop(tmp_path):
    session, targets = two_panel_session(tmp_path)
    s = SessionStacker()
    s.start()
    p = run_backfill(s, plan_backfill(session, stacker=s))
    assert p.error == "", p.error
    assert (p.total, p.done, p.added, p.failed) == (4, 4, 4, 0), p
    st = s.status()
    assert [(q["target"], q["frames"]) for q in st["panels"]] == \
        [("A", 2), ("B", 2)]
    # Keyed by the ledger's target ids, the key the live path uses: a backfill
    # that named a panel differently would build a second picture of it.
    assert [q["key"] for q in st["panels"]] == \
        [targets["A"].id, targets["B"].id]


def test_a_backfill_survives_the_live_path_feeding_another_panel(tmp_path):
    """The live frame loop keeps handing the stacker subs, of whichever panel it
    is on, while the worker reads old ones off disk. Injected from inside the
    pass so the interleaving is reproducible."""
    session, targets = two_panel_session(tmp_path, order=("A", "A", "B", "B"))
    s = SessionStacker()
    s.start()
    items = plan_backfill(session, stacker=s)

    real = s.add
    n = {"live": 0}

    def hooked(*a, **kw):
        out = real(*a, **kw)
        if n["live"] < 2 and kw.get("key"):
            n["live"] += 1
            real(field(-50.0 + n["live"], -30.0, seed=900 + n["live"]), "R",
                 60.0, target="C", target_id="t-live", session=RUN,
                 key=f"/live/{n['live']}.fits")
        return out

    s.add = hooked                          # type: ignore[method-assign]
    p = run_backfill(s, items)
    assert p.error == "" and p.added == 4 and p.done == 4, p
    got = {q["target"]: q["frames"] for q in s.status()["panels"]}
    assert got == {"A": 2, "B": 2, "C": 2}, got


def test_a_backfill_still_stops_when_the_run_changes(tmp_path):
    session, _ = two_panel_session(tmp_path)
    s = SessionStacker()
    s.start()
    items = plan_backfill(session, stacker=s)
    real = s.add
    n = {"calls": 0}

    def hooked(*a, **kw):
        n["calls"] += 1
        out = real(*a, **kw)
        if n["calls"] == 2:
            # a NEW run's frame lands on the live path
            real(field(seed=77), "R", 60.0, target="A", session="run-2",
                 key="/live/new-run.fits")
        return out

    s.add = hooked                          # type: ignore[method-assign]
    p = run_backfill(s, items)
    assert p.error == "the stack moved to another run", p.error
    assert p.done < p.total


def test_the_hub_backfill_counts_every_panels_subs(tmp_path, monkeypatch):
    import astrodeck.hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = hub_module.Hub()
    session, _ = two_panel_session(tmp_path)
    h.engine = type("E", (), {"_session": session,
                              "reporter": type("R", (), {"id": RUN})()})()
    try:
        h.start_session_stack()
        assert h.session_stack_status()["backfill"]["available"] == 4
        h.start_session_stack(backfill=True)
        deadline = time.time() + 30.0
        while time.time() < deadline:
            p = h.session_stack.backfill
            if not p.running and p.done >= p.total:
                break
            time.sleep(0.02)
        st = h.session_stack_status()
        assert st["backfill"]["error"] == "", st["backfill"]
        assert [(q["target"], q["frames"]) for q in st["panels"]] == \
            [("A", 2), ("B", 2)]
        assert st["backfill"]["available"] == 0
    finally:
        h.stop_session_stack()


# ---------------------------------------------------------------------- hub
def _hub_feed(h, pid, info, data, **kw):
    from astrodeck.hub import PreviewEntry
    h.previews[pid] = PreviewEntry(display=b"", mime="image/jpeg", thumb=b"",
                                   linear=data)
    return h.session_stack_add({"id": pid, **info}, **kw)


def test_the_hub_keys_the_panel_by_the_targets_id(tmp_path, monkeypatch):
    import astrodeck.hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = hub_module.Hub()
    try:
        h.start_session_stack()
        info = {"filter": "R", "exposure_s": 60.0, "bayer_pattern": None,
                "binning": 1}
        a = _hub_feed(h, 1, dict(info, saved_path=str(tmp_path / "a1.fits")),
                      field(seed=1), target="NGC 1", target_id="t-1")
        b = _hub_feed(h, 2, dict(info, saved_path=str(tmp_path / "b1.fits")),
                      field(40.0, 10.0, seed=2), target="NGC 1",
                      target_id="t-2")
        assert a == "R" and b == "R"
        st = h.session_stack_status()
        assert [(p["key"], p["target"]) for p in st["panels"]] == \
            [("t-1", "NGC 1"), ("t-2", "NGC 1")]
        # the legacy call (no id) still works and keys by name
        c = _hub_feed(h, 3, dict(info, saved_path=str(tmp_path / "c1.fits")),
                      field(-40.0, 10.0, seed=3), target="M31")
        assert c == "R"
        assert [p["key"] for p in h.session_stack_status()["panels"]] == \
            ["t-1", "t-2", "M31"]
    finally:
        h.stop_session_stack()


def test_the_hubs_reset_clears_every_panel_and_keeps_the_run(tmp_path,
                                                             monkeypatch):
    import astrodeck.hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = hub_module.Hub()
    try:
        h.start_session_stack()
        h.engine = type("E", (), {"_session": None,
                                  "reporter": type("R", (), {"id": RUN})()})()
        info = {"filter": "R", "exposure_s": 60.0, "bayer_pattern": None,
                "binning": 1}
        for i, (name, ident) in enumerate([("A", "t-a"), ("B", "t-b")]):
            ox, oy = PANEL_OFFSET[name]
            assert _hub_feed(h, 10 + i,
                             dict(info, saved_path=str(tmp_path / f"{i}.fits")),
                             field(ox, oy, seed=40 + i), target=name,
                             target_id=ident) == "R"
        assert len(h.session_stack_status()["panels"]) == 2
        st = h.reset_session_stack()
        assert st["panels"] == [] and st["frames"] == 0
        assert st["enabled"] is True and st["session"] == RUN
    finally:
        h.stop_session_stack()


# -------------------------------------------------------------- concurrency
def test_two_threads_feeding_two_panels_each_count_once():
    """The lock under contention, now with the panel table inside it: two
    threads offer the same eight keys to two panels in opposite orders."""
    s = SessionStacker()
    s.start()
    frames = [(f"/x/{i}.fits", field(i * 0.5, -i * 0.4, seed=500 + i))
              for i in range(8)]
    results: list[list] = [[], []]

    def worker(idx, order):
        for key, data in order:
            panel = "A" if int(key.split("/")[-1].split(".")[0]) % 2 else "B"
            results[idx].append(
                s.add(data, "R", 60.0, target=panel, session=RUN, key=key))

    ts = [threading.Thread(target=worker, args=(0, frames)),
          threading.Thread(target=worker, args=(1, list(reversed(frames))))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    landed = sum(1 for r in results[0] + results[1] if r)
    held = sum(p["frames"] for p in s.status()["panels"])
    assert landed == held <= 8, (landed, held)
    assert all(s.has_frame(k) for k, _ in frames)


# ------------------------------------------- what a panel's numbers promise
def test_a_new_run_and_a_reset_forget_what_the_last_one_released():
    """``evicted`` is the night's record. A run that has released nothing must
    not open with a warning about panels the last run lost, and an operator's
    Reset empties the list with the pixels it describes."""
    one = _one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 1.5))
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)                 # A is released
    assert s.status()["evicted"] == ["A"]
    feed(s, "C", 0, run="run-2")    # a new run drops every panel
    assert s.status()["evicted"] == [],         "last run's release was carried into the new run"
    feed(s, "D", 0, run="run-2")    # this run releases C in its turn
    assert s.status()["evicted"] == ["C"]
    s.reset(s.target, s.session)
    assert s.status()["evicted"] == [], "Reset left the release record behind"


def test_a_panels_seq_moves_only_when_that_panel_does():
    """The client's cache key for a pinned panel is that panel's own ``seq``. A
    stacker-wide counter in its place would refetch panel A's picture at every
    frame of panel B, which is the cost the per-panel render cache exists to
    avoid on the server."""
    s = SessionStacker()
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    a0 = s.status(panel="A")["seq"]
    b0 = s.status(panel="B")["seq"]
    assert a0 != b0 and b0 > a0, \
        "two panels read one status seq: it is the stacker's, not the panel's"
    feed(s, "B", 1)
    assert s.status(panel="A")["seq"] == a0,         "a frame on panel B moved panel A's status seq"
    assert s.status(panel="B")["seq"] > b0
    by = {p["key"]: p["seq"] for p in s.status()["panels"]}
    assert by["A"] == a0 and by["B"] == s.status()["seq"],         "the panel list and the status disagree about a panel's seq"
    assert s.rgb_preview(120, panel="A")[1]["seq"] == a0
    assert s.channel_preview("R", 120, panel="A")[1]["seq"] == a0
    feed(s, "A", 1)
    assert s.status(panel="A")["seq"] > s.status(panel="B")["seq"],         "the foreground flipping back repeated an old value"


def test_an_add_naming_no_panel_feeds_the_foreground_panel():
    """``target`` omitted is "the picture in hand", whichever key it has. A panel
    keyed by id must not be re-keyed by its name and split in two."""
    s = SessionStacker()
    s.start()
    feed(s, "A", 0, ident="t-1")
    feed(s, "B", 0, ident="t-2")
    ox, oy = PANEL_OFFSET["B"]
    dx, dy = DITHERS[1]
    assert s.add(field(ox + dx, oy + dy, seed=77), "R", 60.0,
                 session=RUN) == "R"
    st = s.status()
    assert [p["key"] for p in st["panels"]] == ["t-1", "t-2"],         "a frame that named no panel made a panel of its own"
    assert s.status(panel="t-2")["frames"] == 2
    assert s.status(panel="t-1")["frames"] == 1


def test_a_name_shared_by_two_panels_answers_with_the_latest_of_them():
    """A hand-typed ``?panel=<name>`` meets two panels with one name: it gets
    the one the run is shooting, not the first one it ever made."""
    s = SessionStacker()
    s.start()
    feed(s, "A", 0, ident="t-1")
    feed(s, "A", 0, ident="t-2")
    feed(s, "A", 1, ident="t-1")
    assert s.status(panel="A")["frames"] == 2          # t-1 is the latest
    feed(s, "A", 1, ident="t-2")
    feed(s, "A", 2, ident="t-2")
    assert s.status(panel="A")["frames"] == 3,         "the name answered with the older panel, not the one being fed"
    assert s.status(panel="t-1")["frames"] == 2, "a key still means its panel"
