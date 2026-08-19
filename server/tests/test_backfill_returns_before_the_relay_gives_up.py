"""A backfill that cannot answer in time is a backfill nobody can run remotely.

2026-08-19: `POST /api/gallery/thumbs/backfill` returned 504 through the relay.
Its own docstring says it "can occupy a core for twenty minutes on a large
library" and it awaited inline, so against the relay's 30s upstream timeout it
could never complete — on the very deployment where the operator would reach for
it, from a phone, after noticing the gallery was slow.

The work itself was fine; the rig finished it. But the caller got an error, the
UI got nothing to render, and the request was aborted mid-flight.

The contract already had the answer: `truncated` means "run again to continue".
So the fix is a TIME BUDGET rather than a new async job — the route returns what
it managed, honestly flagged, and the caller loops.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from astrodeck import gallery


@pytest.fixture
def cap(tmp_path, monkeypatch):
    import astrodeck.hub as hub_mod
    root = tmp_path / "captures"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", root)
    gallery.clear_meta_cache()
    return root


def _frames(cap, n):
    from astropy.io import fits
    rng = np.random.default_rng(0)
    for i in range(n):
        p = cap / "NGC 7129" / f"Light_{i:03d}.fits"
        p.parent.mkdir(parents=True, exist_ok=True)
        fits.PrimaryHDU(rng.normal(400, 5, (120, 180)).astype(np.uint16)).writeto(
            p, overwrite=True)


def test_a_budget_stops_it_and_says_it_stopped(cap):
    _frames(cap, 12)
    slow = {"n": 0}
    real = gallery.precompute

    def crawl(rel, widths=None):
        slow["n"] += 1
        time.sleep(0.05)
        return real(rel, widths)

    gallery.precompute = crawl
    try:
        out = gallery.backfill(budget_s=0.12)
    finally:
        gallery.precompute = real
    assert out["truncated"] is True, out
    assert out["frames"] < 12, (
        f"processed {out['frames']}/12 with a 0.12s budget — the budget is not "
        "being honoured, so a large library still outlives the relay timeout")
    assert out["frames"] >= 1, "a budget must still make progress"


def test_no_budget_still_does_the_whole_library(cap):
    """The local caller — capture's own warm path, or a console — has no relay in
    the way and must not be truncated by a default."""
    _frames(cap, 6)
    out = gallery.backfill()
    assert out["frames"] == 6 and out["truncated"] is False, out


def test_the_route_defaults_to_a_budget_under_the_relay_timeout():
    """The relay gives up at 30s. The default has to be comfortably inside it,
    or the remote caller is back where it started."""
    import inspect
    from astrodeck.gallery import DEFAULT_BACKFILL_BUDGET_S
    assert 0 < DEFAULT_BACKFILL_BUDGET_S <= 20, DEFAULT_BACKFILL_BUDGET_S
    src = inspect.getsource(gallery.backfill)
    assert "budget_s" in src


def test_a_LIMITED_backfill_admits_it_only_did_a_prefix(cap):
    """`limit` truncation has never been reported.

    `rows` is sliced by `limit` and only THEN measured, so the flag compared the
    limit against a count the limit had already capped — `limit < len(rows)` can
    never be true. A `limit=3` pass over a 10-frame library returned
    truncated=False, i.e. "everything is warm", which is the exact failure mode
    the flag's own comment warns about: "a backfill that quietly stopped reads
    exactly like one that finished, and the frames past the cap stay cold
    forever".

    Found by sabotaging the clause and watching nothing fail.
    """
    _frames(cap, 10)
    out = gallery.backfill(limit=3)
    assert out["frames"] == 3, out
    assert out["truncated"] is True, (
        "a 3-of-10 backfill reported complete coverage")
