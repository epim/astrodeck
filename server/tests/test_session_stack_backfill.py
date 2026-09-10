"""Backfill: the subs the run shot BEFORE the stack was switched on.

The stack used to start from the next frame, so arming it at 2am showed two of
the night's ninety subs. This is the opt-in pass that walks the run's own ledger
and folds the earlier ones in, and the assertions worth making are the ones that
separate "it stacked some files" from "it stacked the right files exactly once":

  * a QUALITY-REJECTED sub is written to disk under the same name as an accepted
    one, so a directory scan cannot tell them apart and the ledger has to be the
    authority. If this ever regresses, the trailed frames the gate refused end
    up in the only picture the operator looks at;
  * nothing is folded in twice -- not by running the pass again, not by
    disabling and re-enabling, and not by racing the live path;
  * a backfilled composite is the SAME PICTURE the live path would have built
    from the same frames. That is the claim the feature makes ("what we can hope
    to expect when done") and the only honest way to check it is to build both;
  * the counter reaches ``done == total`` in every case, including no frames at
    all and every frame already stacked -- a progress bar that stops at 7 of 9
    reads as a hang.
"""
import math
import threading
import inspect
import time

import numpy as np
import pytest

from astrodeck.imaging.sessionstack import SessionStacker, frame_key
from astrodeck.imaging.stackbackfill import (
    BackfillItem, plan_backfill, read_backfill_frame, run_backfill,
)
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame

STARS = [(60, 70, 1.0), (180, 120, 0.7), (300, 200, 0.55),
         (420, 90, 0.4), (250, 330, 0.35)]


def field(dx: float = 0.0, dy: float = 0.0, *, scale: float = 1.0,
          seed: int = 3, shape=(400, 480), glow: float = 3000.0) -> np.ndarray:
    """The same synthetic field the core test file uses: stars to register on
    plus an extended glow, so a brightness assertion means something."""
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


def write_fits(path, data, *, filter_name="R", exposure_s=60.0,
               bayer=None, binning=1):
    """One sub on disk, through the app's own writer so the cards under test are
    the cards the rig actually produces."""
    from astrodeck.devices.base import CameraFrame
    from astrodeck.imaging.fitsio import save_fits
    frame = CameraFrame(data=data, exposure_s=exposure_s, gain=100, offset=30,
                        binning=binning, bayer_pattern=bayer,
                        temperature_c=-10.0, timestamp=1_757_000_000.0)
    return save_fits(frame, path, target="M42", filter_name=filter_name)


# --------------------------------------------------------------------------
# Building a plausible ledger
# --------------------------------------------------------------------------

def make_session(tmp_path, specs, *, run="night-1", target="M42"):
    """A Session whose ledger points at real files.

    ``specs`` is a list of ``(filter, accepted, override)`` -- or a shorter tuple
    -- one per sub, written in order with a small dither so registration has
    something to find.
    """
    step_ids: dict[str, str] = {}
    steps = []
    for spec in specs:
        filt = spec[0]
        if filt not in step_ids:
            s = ExposureStep(filter=filt, exposure_s=60.0, count=99)
            step_ids[filt] = s.id
            steps.append(s)
    t = Target(name=target, ra_hours=5.5, dec_deg=-5.0, steps=steps)
    plan = SequencePlan(name="backfill-test", targets=[t])

    frames = []
    dithers = [(0, 0), (1.5, -2.0), (-2.0, 1.0), (3.0, 1.0), (2.0, -1.0),
               (-1.0, -1.5), (0.5, 2.0), (-2.5, 0.5)]
    for i, spec in enumerate(specs):
        filt = spec[0]
        accepted = spec[1] if len(spec) > 1 else True
        override = spec[2] if len(spec) > 2 else None
        dx, dy = dithers[i % len(dithers)]
        path = tmp_path / f"Light_{target}_{filt}_2026-09-10_2200{i:02d}_{i}.fits"
        write_fits(path, field(dx, dy, seed=100 + i), filter_name=filt)
        frames.append(SessionFrame(ts=1000.0 + i, night=run, target_id=t.id,
                                   step_id=step_ids[filt], path=str(path),
                                   auto_accepted=accepted, override=override))
    return Session(plan=plan, nights=[run], frames=frames), t


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def test_a_rejected_sub_is_not_in_the_plan(tmp_path):
    # The one that matters. Rejects are kept on disk (hfr_reject_action defaults
    # to "warn") under the SAME filename template, so nothing on disk
    # distinguishes them -- only the ledger does.
    session, _ = make_session(tmp_path, [("R", True), ("R", False),
                                         ("R", True)])
    items = plan_backfill(session)
    assert len(items) == 2, [i.path.name for i in items]
    names = [i.path.name for i in items]
    assert not any("_1.fits" in n for n in names), \
        f"the rejected sub was planned for stacking: {names}"
    # ...and it really is on disk, so a directory scan would have taken it.
    assert len(list(tmp_path.glob("*.fits"))) == 3


def test_an_operator_override_beats_the_auto_verdict(tmp_path):
    # SessionFrame.effective(): a human accepting a frame the gate refused, and
    # rejecting one it passed, is the whole reason overrides exist.
    session, _ = make_session(tmp_path, [
        ("R", False, "accept"),          # gate said no, operator said yes
        ("R", True, "reject"),           # gate said yes, operator said no
        ("R", True, None),
    ])
    names = sorted(i.path.name for i in plan_backfill(session))
    assert [n.split("_")[-1] for n in names] == ["0.fits", "2.fits"], names


def test_only_this_run_is_planned(tmp_path):
    # A multi-night session's earlier nights were shot after a different
    # centring, and on this rig sometimes a different rotation. The stack's
    # identity is (target, run) and the backfill does not get to disagree.
    session, t = make_session(tmp_path, [("R",), ("R",)], run="night-2")
    older = tmp_path / "Light_M42_R_2026-09-09_220000_9.fits"
    write_fits(older, field(seed=9))
    session.frames.insert(0, SessionFrame(
        ts=1.0, night="night-1", target_id=t.id,
        step_id=session.plan.targets[0].steps[0].id, path=str(older)))

    items = plan_backfill(session)
    assert [i.session for i in items] == ["night-2", "night-2"]
    assert older.name not in [i.path.name for i in items]
    # ...and asking for the older night explicitly gets exactly that one.
    assert [i.path.name for i in plan_backfill(session, run="night-1")] \
        == [older.name]


def test_only_one_target_is_planned(tmp_path):
    # The stacker holds ONE target and resets when it changes, so a two-target
    # backfill would spend the disk reads on both and keep the last.
    session, t = make_session(tmp_path, [("R",), ("R",)])
    other = Target(name="M31", ra_hours=0.7, dec_deg=41.0,
                   steps=[ExposureStep(filter="R", exposure_s=60.0, count=5)])
    session.plan.targets.append(other)
    p = tmp_path / "Light_M31_R_2026-09-10_230000_1.fits"
    write_fits(p, field(seed=42))
    session.frames.append(SessionFrame(
        ts=2000.0, night="night-1", target_id=other.id,
        step_id=other.steps[0].id, path=str(p)))

    # The most recent frame is M31's, so that is the picture about to be shown.
    assert [i.target for i in plan_backfill(session)] == ["M31"]
    # And asking for M42 gets M42's two, not all three.
    items = plan_backfill(session, target="M42")
    assert len(items) == 2 and {i.target for i in items} == {"M42"}


def test_an_unsaved_frame_is_skipped(tmp_path):
    # path == "" is the ledger's own "this frame was not written" (a NINA sub, a
    # discard). There is nothing to read, so it is not a failure, it is absent.
    session, _ = make_session(tmp_path, [("R",), ("R",)])
    session.frames[0].path = ""
    assert len(plan_backfill(session)) == 1


def test_an_empty_ledger_plans_nothing(tmp_path):
    session, _ = make_session(tmp_path, [])
    assert plan_backfill(session) == []


def test_the_plan_skips_what_the_stack_already_holds(tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("R",)])
    s = SessionStacker()
    s.start()
    first = session.frames[0].path
    s.add(field(seed=100), "R", 60.0, target="M42", session="night-1",
          key=first)
    items = plan_backfill(session, stacker=s)
    assert len(items) == 2
    assert frame_key(first) not in [i.key for i in items]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def test_the_header_is_believed_over_the_plan(tmp_path):
    # A step edited after the frame was shot says nothing about the frame. The
    # header describes the file being stacked.
    p = tmp_path / "Light_M42_Ha_2026-09-10_220000_0.fits"
    write_fits(p, field(), filter_name="Ha", exposure_s=300.0)
    data, filt, exp, bayer = read_backfill_frame(
        BackfillItem(path=p, filter_name="R", exposure_s=60.0))
    assert filt == "Ha" and exp == pytest.approx(300.0) and bayer is None
    assert data.dtype == np.uint16 and data.shape == (400, 480)


def test_a_bayered_sub_comes_back_with_its_pattern(tmp_path):
    p = tmp_path / "Light_M42_L_2026-09-10_220000_0.fits"
    write_fits(p, field(), filter_name="", bayer="RGGB")
    _, _, _, bayer = read_backfill_frame(BackfillItem(path=p))
    assert bayer == "RGGB"


def test_a_binned_bayered_sub_is_not_debayered(tmp_path):
    # BAYERPAT describes the SENSOR. A 2x2-binned readout has already summed
    # the colours together, so splitting it into three planes would produce
    # three copies of the same grey and call it colour.
    p = tmp_path / "Light_M42_L_2026-09-10_220000_0.fits"
    write_fits(p, field(shape=(200, 240)), filter_name="", bayer="RGGB",
               binning=2)
    _, _, _, bayer = read_backfill_frame(BackfillItem(path=p))
    assert bayer is None


def test_the_filename_is_the_last_resort_for_a_filter(tmp_path):
    # No FILTER card (a frame written by something else) and no plan step: the
    # capture template's own filter token is still there to be read.
    from astropy.io import fits
    p = tmp_path / "Light_M42_Oiii_2026-09-10_220000_0.fits"
    fits.PrimaryHDU(field().astype(np.uint16)).writeto(p)
    _, filt, _, _ = read_backfill_frame(BackfillItem(path=p))
    assert filt == "Oiii"


def test_a_missing_file_raises_and_the_pass_survives_it(tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("R",)])
    session.frames[1].path = str(tmp_path / "gone.fits")
    s = SessionStacker()
    s.start()
    notes: list[str] = []
    p = run_backfill(s, plan_backfill(session), on_error=notes.append)
    assert p.total == 3 and p.done == 3
    assert p.failed == 1 and p.added == 2, p
    assert notes and "gone.fits" in notes[0]
    assert p.running is False and not p.error


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------

def test_progress_counts_to_completion(tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("G",), ("G",)])
    s = SessionStacker()
    s.start()
    p = run_backfill(s, plan_backfill(session))
    assert (p.total, p.done, p.added, p.skipped, p.failed) == (4, 4, 4, 0, 0)
    assert p.running is False
    assert s.status()["backfill"]["done"] == 4
    assert {c["channel"] for c in s.status()["channels"]} == {"R", "G"}


def test_zero_frames_finishes_rather_than_hanging_at_nothing(tmp_path):
    # "0 of 0, running" forever is indistinguishable from a hang in the UI.
    s = SessionStacker()
    s.start()
    p = run_backfill(s, [])
    assert (p.total, p.done, p.running) == (0, 0, False)
    assert p.finished_ts and not p.error


def test_a_pass_over_frames_already_stacked_counts_them_skipped(tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("R",)])
    s = SessionStacker()
    s.start()
    items = plan_backfill(session)
    run_backfill(s, items)
    before = s.status()
    # The SAME items again -- as if the operator pressed it twice.
    p = run_backfill(s, items)
    assert (p.total, p.done, p.skipped, p.added) == (3, 3, 3, 0), p
    after = s.status()
    assert after["frames"] == before["frames"], \
        "a second pass over the same subs double-counted them"
    assert after["integrated_s"] == before["integrated_s"]


def test_switching_the_stack_off_stops_the_pass(tmp_path):
    session, _ = make_session(tmp_path, [("R",)] * 6)
    s = SessionStacker()
    s.start()
    items = plan_backfill(session)

    real = s.add
    calls = {"n": 0}

    def counting(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            s.enabled = False              # the operator flips the switch
        return real(*a, **kw)

    s.add = counting                       # type: ignore[method-assign]
    p = run_backfill(s, items)
    assert p.error == "stopped"
    assert p.done < p.total, "the pass read every frame after being switched off"


def test_a_reset_stops_the_pass(tmp_path):
    # Carrying on filling a stack the operator just cleared would undo the press.
    session, _ = make_session(tmp_path, [("R",)] * 6)
    s = SessionStacker()
    s.start()
    items = plan_backfill(session)
    real = s.add
    calls = {"n": 0}

    def counting(*a, **kw):
        calls["n"] += 1
        out = real(*a, **kw)
        if calls["n"] == 2:
            s.reset(s.target, s.session)
        return out

    s.add = counting                       # type: ignore[method-assign]
    p = run_backfill(s, items)
    assert p.error == "stopped" and p.done < p.total


# --------------------------------------------------------------------------
# Idempotence
# --------------------------------------------------------------------------

def test_enable_backfill_disable_enable_backfill_is_the_same_picture(tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("G",), ("B",)])
    items = plan_backfill(session)

    s = SessionStacker()
    s.start()
    run_backfill(s, items)
    first = s.status()
    jpeg_a = s.rgb_preview(240)[0]

    s.stop()                                # off: pixels released
    s.start()
    p = run_backfill(s, plan_backfill(session, stacker=s))
    second = s.status()

    assert p.added == 4 and p.skipped == 0, \
        "a stack switched off keeps a memory of frames whose pixels it threw away"
    assert second["frames"] == first["frames"] == 4
    assert second["integrated_s"] == first["integrated_s"]
    assert [c["channel"] for c in second["channels"]] == \
        [c["channel"] for c in first["channels"]]
    # Same frames, same order, same numbers -> byte-for-byte the same picture.
    assert s.rgb_preview(240)[0] == jpeg_a


def test_the_live_path_and_a_backfill_do_not_both_take_a_frame(tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("R",)])
    s = SessionStacker()
    s.start()
    # The live path already took the middle sub (it landed after the switch).
    mid = session.frames[1]
    s.add(field(1.5, -2.0, seed=101), "R", 60.0, target="M42",
          session="night-1", key=mid.path)
    assert s.status()["frames"] == 1

    p = run_backfill(s, plan_backfill(session))
    assert p.skipped == 1 and p.added == 2, p
    assert s.status()["frames"] == 3, \
        "the backfill re-stacked a sub the live path had already folded in"


def test_a_frame_offered_twice_inside_add_is_stacked_once(tmp_path):
    # The check and the accumulation are one critical section; this is that
    # promise at its narrowest.
    s = SessionStacker()
    s.start()
    data = field(seed=7)
    assert s.add(data, "R", 60.0, target="M42", key="/x/a.fits") == "R"
    assert s.add(data, "R", 60.0, target="M42", key="/x/a.fits") is None
    # ...including a differently-spelled path for the same file.
    assert s.add(data, "R", 60.0, target="M42", key="/x\\a.FITS") is None
    assert s.status()["frames"] == 1


def test_a_frame_with_no_identity_is_still_stacked(tmp_path):
    # A NINA sub or an unsaved capture has no path. Refusing it would silently
    # empty the stack for a whole class of rig; there is simply nothing to
    # de-duplicate against, and that is documented rather than hidden.
    s = SessionStacker()
    s.start()
    assert s.add(field(seed=1), "R", 60.0, target="M42") == "R"
    assert s.add(field(1.5, -2.0, seed=2), "R", 60.0, target="M42") == "R"
    assert s.status()["frames"] == 2


# --------------------------------------------------------------------------
# The claim: a backfilled composite is the live one
# --------------------------------------------------------------------------

def test_a_backfilled_composite_equals_the_live_one(tmp_path):
    """N frames live vs the same N by backfill: the same picture.

    This is the feature's actual promise -- "that approximation of what we can
    hope to expect when done" -- and the only honest way to check it is to build
    the composite both ways and compare the bytes. Same frames, same order, one
    reading them from memory and one off disk.
    """
    specs = [("R",), ("R",), ("G",), ("G",), ("B",)]
    session, _ = make_session(tmp_path, specs)

    live = SessionStacker()
    live.start()
    for i, spec in enumerate(specs):
        data, filt, exp, bayer = read_backfill_frame(
            BackfillItem(path=session.frames[i].path))
        live.add(data, filt, exp, target="M42", session="night-1",
                 bayer_pattern=bayer, key=f"/live/{i}.fits")

    backfilled = SessionStacker()
    backfilled.start()
    p = run_backfill(backfilled, plan_backfill(session))
    assert p.added == len(specs)

    a, meta_a = live.rgb_preview(320)
    b, meta_b = backfilled.rgb_preview(320)
    assert meta_a["frames"] == meta_b["frames"] == len(specs)
    assert meta_a["mode"] == meta_b["mode"] == "rgb"
    assert [c["channel"] for c in meta_a["channels"]] == \
        [c["channel"] for c in meta_b["channels"]]
    assert a == b, \
        "the backfilled composite is not the picture the live path would have " \
        "built from the same subs"


def test_backfill_and_live_frames_interleaved_all_land(tmp_path):
    """A run keeps handing frames to the stacker while the backfill reads disk.

    Driven deterministically rather than with real threads: a live sub is
    injected from inside the pass, between two of its reads, which is exactly
    the interleaving the lock has to survive and is reproducible.
    """
    session, _ = make_session(tmp_path, [("R",)] * 4)
    s = SessionStacker()
    s.start()
    items = plan_backfill(session)

    real = s.add
    injected = {"n": 0}

    def hooked(*a, **kw):
        out = real(*a, **kw)
        if injected["n"] < 2 and kw.get("key"):
            injected["n"] += 1
            # A brand-new sub off the camera, mid-backfill.
            real(field(0.5, 0.5, seed=500 + injected["n"]), "G", 60.0,
                 target="M42", session="night-1",
                 key=f"/live/new{injected['n']}.fits")
        return out

    s.add = hooked                          # type: ignore[method-assign]
    p = run_backfill(s, items)
    st = s.status()
    assert p.added == 4 and p.done == 4 and not p.error, p
    assert st["frames"] == 6, st
    counts = {c["channel"]: c["frames"] for c in st["channels"]}
    assert counts == {"R": 4, "G": 2}, counts


def test_add_is_safe_from_two_threads(tmp_path):
    """The lock, under real contention.

    Two threads offer the same twelve keys in opposite orders. Every key must
    land exactly once and the totals must add up, whichever thread got there
    first. Without the lock -- or with the seen-check outside it -- the frame
    count comes out above twelve.
    """
    s = SessionStacker()
    s.start()
    frames = [(f"/x/{i}.fits", field(i * 0.5, -i * 0.4, seed=200 + i))
              for i in range(12)]
    results: list[list[str | None]] = [[], []]

    def worker(idx: int, order) -> None:
        for key, data in order:
            results[idx].append(
                s.add(data, "R", 60.0, target="M42", session="run", key=key))

    threads = [threading.Thread(target=worker, args=(0, frames)),
               threading.Thread(target=worker, args=(1, list(reversed(frames))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    st = s.status()
    landed = sum(1 for r in results[0] + results[1] if r)
    assert landed == st["frames"], \
        f"{landed} adds reported success but the stack holds {st['frames']}"
    assert st["frames"] <= 12, \
        f"the same sub was stacked twice under contention ({st['frames']} > 12)"
    # Every key was consumed exactly once, so the union covers all twelve.
    assert all(s.has_frame(k) for k, _ in frames)


# --------------------------------------------------------------------------
# The hub: the option, the worker thread, and what the UI polls
# --------------------------------------------------------------------------

class FakeEngine:
    """Just enough engine for the backfill to find a ledger. The real one is a
    3000-line coroutine machine and none of it is what is under test here."""

    def __init__(self, session=None, run="night-1"):
        self._session = session
        self.reporter = type("R", (), {"id": run})()


@pytest.fixture
def bare_hub(tmp_path, monkeypatch):
    import astrodeck.hub as hub_module
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = hub_module.Hub()
    yield h
    h.stop_session_stack()


def settle(hub, timeout=20.0):
    """Wait for the worker thread to finish its pass."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = hub.session_stack.backfill
        if not p.running and p.done >= p.total:
            return p
        time.sleep(0.02)
    raise AssertionError(f"the backfill never finished: {hub.session_stack.backfill}")


def test_the_ledger_attribute_the_hub_reaches_for_is_the_engines_real_one():
    """FakeEngine sets ``_session`` itself, so every test above would keep
    passing if the engine renamed that attribute -- and the backfill would
    quietly find nothing on the rig, because ``_live_session`` swallows the
    miss with ``getattr(..., None)``. This is the one test that grades the
    seam instead of the double: the name must exist on the real class, with a
    ledger-or-None annotation, before any of the doubles mean anything.
    """
    import astrodeck.sequence.engine as engine_module

    src = inspect.getsource(engine_module.SequenceEngine)
    assert "self._session: Session | None" in src, (
        "SequenceEngine no longer holds the live ledger in self._session; "
        "Hub._live_session reaches for it by that name through getattr with a "
        "None default, so a rename makes the backfill silently find nothing "
        "on the rig while every test above keeps passing")
    # And the engine has to be the thing the hub actually gets handed.
    assert "hub.engine = self" in src, (
        "SequenceEngine no longer publishes itself on the hub, so "
        "Hub._live_session has nothing to read the ledger off")


def test_the_hub_finds_the_runs_accepted_subs(bare_hub, tmp_path):
    session, _ = make_session(tmp_path, [("R", True), ("R", False), ("G", True)])
    bare_hub.engine = FakeEngine(session)
    items = bare_hub.session_stack_backfill_items()
    assert [i.filter_name for i in items] == ["R", "G"]
    assert bare_hub.session_stack_status()["backfill"]["available"] == 2


def test_with_no_run_there_is_nothing_to_backfill(bare_hub, tmp_path):
    # The ledger is the only record of the quality verdict, so no live run means
    # no backfill -- however many FITS are sitting in the capture directory.
    write_fits(tmp_path / "Light_M42_R_2026-09-10_220000_0.fits", field())
    assert bare_hub.session_stack_backfill_items() == []
    st = bare_hub.start_session_stack(backfill=True)
    assert st["enabled"] is True
    assert st["backfill"]["total"] == 0 and st["backfill"]["running"] is False
    assert st["frames"] == 0


def test_the_option_is_off_by_default(bare_hub, tmp_path):
    # A bare POST has to behave exactly as it always did: stack the NEXT frame.
    # Minutes of disk and CPU is not a thing a switch may do unasked.
    session, _ = make_session(tmp_path, [("R",), ("R",), ("R",)])
    bare_hub.engine = FakeEngine(session)
    st = bare_hub.start_session_stack()
    assert st["frames"] == 0
    assert st["backfill"]["total"] == 0
    # ...and the count of what it WOULD have folded in is on the status, so the
    # UI can offer the choice with a number attached.
    assert st["backfill"]["available"] == 3


def test_asking_for_it_stacks_the_earlier_subs(bare_hub, tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",), ("G",), ("G",)])
    bare_hub.engine = FakeEngine(session)
    bare_hub.start_session_stack(backfill=True)
    p = settle(bare_hub)
    assert (p.total, p.added, p.failed) == (4, 4, 0), p
    st = bare_hub.session_stack_status()
    assert st["frames"] == 4 and st["target"] == "M42"
    assert {c["channel"] for c in st["channels"]} == {"R", "G"}
    # Nothing is left to fold in, which is the counter's "all caught up".
    assert st["backfill"]["available"] == 0
    assert st["backfill"]["done"] == st["backfill"]["total"] == 4


def test_the_backfill_route_catches_an_already_running_stack_up(bare_hub, tmp_path):
    # Reaching this by switching off and on again would throw away everything
    # stacked since, which is why it is its own call.
    session, _ = make_session(tmp_path, [("R",), ("R",)])
    bare_hub.engine = FakeEngine(session)
    bare_hub.start_session_stack()
    assert bare_hub.session_stack_status()["frames"] == 0
    bare_hub.session_stack_backfill()
    settle(bare_hub)
    assert bare_hub.session_stack_status()["frames"] == 2


def test_a_backfill_on_a_stack_that_is_off_does_nothing(bare_hub, tmp_path):
    session, _ = make_session(tmp_path, [("R",), ("R",)])
    bare_hub.engine = FakeEngine(session)
    st = bare_hub.session_stack_backfill()
    assert st["enabled"] is False and st["frames"] == 0


def test_only_one_pass_runs_at_a_time(bare_hub, tmp_path):
    session, _ = make_session(tmp_path, [("R",)] * 3)
    bare_hub.engine = FakeEngine(session)
    bare_hub.start_session_stack()
    bare_hub.session_stack.backfill_begin(99)        # pretend one is in flight
    st = bare_hub.session_stack_backfill()
    assert st["backfill"]["total"] == 99, \
        "a second pass restarted the counter over the top of a running one"
    bare_hub.session_stack.backfill_finish()


def test_a_stack_switched_off_mid_pass_does_not_come_back(bare_hub, tmp_path):
    session, _ = make_session(tmp_path, [("R",)] * 4)
    bare_hub.engine = FakeEngine(session)
    bare_hub.start_session_stack(backfill=True)
    bare_hub.stop_session_stack()
    # Whatever the worker got through, the switch is off and the pixels are gone.
    deadline = time.time() + 20.0
    while bare_hub.session_stack.backfill.running and time.time() < deadline:
        time.sleep(0.02)
    st = bare_hub.session_stack_status()
    assert st["enabled"] is False
    assert st["frames"] == 0, "a stopped stack still has frames in it"


def _bayer_mosaic(dx=0.0, dy=0.0, *, seed=5, pattern="RGGB",
                  red=1.0, green=0.5, blue=0.2):
    """A mosaic whose 2x2 cells hold three differently-bright versions of one
    scene, so a debayer that works produces three different planes."""
    planes = {c: field(dx, dy, scale=s, seed=seed)
              for c, s in (("R", red), ("G", green), ("B", blue))}
    h, w = planes["R"].shape
    m = np.zeros((h, w), dtype=np.uint16)
    for i, c in enumerate(pattern):
        oy, ox = divmod(i, 2)
        m[oy::2, ox::2] = planes[c][oy::2, ox::2]
    return m


def _feed(hub, info, data, *, pid=7, target="M42"):
    """Put pixels in the preview ring where session_stack_add looks for them and
    hand it the frame's published meta -- the live path's exact seam."""
    from astrodeck.hub import PreviewEntry
    hub.previews[pid] = PreviewEntry(display=b"", mime="image/jpeg",
                                     thumb=b"", linear=data)
    return hub.session_stack_add({"id": pid, **info}, target=target)


def test_the_live_path_carries_the_bayer_pattern(bare_hub, tmp_path):
    # THE SEAM. The stacker can debayer and the FITS carries BAYERPAT, and none
    # of that reaches the picture unless the hub passes the pattern from the
    # frame it has in hand. This is the only place that hand-off happens.
    bare_hub.start_session_stack()
    path = str(tmp_path / "Light_M42_L_2026-09-10_220000_0.fits")
    got = _feed(bare_hub, {"filter": "", "exposure_s": 60.0,
                           "bayer_pattern": "RGGB", "binning": 1,
                           "saved_path": path}, _bayer_mosaic())
    assert got == "R+G+B", got
    st = bare_hub.session_stack_status()
    assert [c["channel"] for c in st["channels"]] == ["R", "G", "B"]
    assert st["mode"] == "rgb"


def test_the_live_path_carries_the_frames_identity(bare_hub, tmp_path):
    # ...and the path, or a backfill an hour later has no way to know this sub
    # is already in the stack.
    bare_hub.start_session_stack()
    path = str(tmp_path / "Light_M42_R_2026-09-10_220000_0.fits")
    info = {"filter": "R", "exposure_s": 60.0, "bayer_pattern": None,
            "binning": 1, "saved_path": path}
    assert _feed(bare_hub, info, field(seed=5)) == "R"
    assert bare_hub.session_stack.has_frame(path)
    # The same frame offered again -- a retry, a re-publish -- is a no-op.
    assert _feed(bare_hub, info, field(seed=5), pid=8) is None
    assert bare_hub.session_stack_status()["frames"] == 1


def test_the_live_path_does_not_debayer_a_binned_frame(bare_hub, tmp_path):
    # The camera still reports RGGB at bin 2; the mosaic is gone from the
    # pixels. Splitting it would produce three planes of the same grey.
    bare_hub.start_session_stack()
    got = _feed(bare_hub, {"filter": "", "exposure_s": 60.0,
                           "bayer_pattern": "RGGB", "binning": 2,
                           "saved_path": str(tmp_path / "b.fits")},
                _bayer_mosaic())
    assert got == "L", got
