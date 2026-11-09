# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``POST /api/sequence/retry-set-aside``, dormant path, knows a mosaic group
from a pool group (WP 5.1c, #183).

The route brings back tonight's set-aside PANELS of a mosaic and, by the
coordinator's ruling, the set-aside CANDIDATES of a pool alike: a pool's id is
a group it knows, retry-all clears both, and its words say "candidates" for a
pool and "panels" for a mosaic.

The harness is the real app over the real stores (test_flows_progress_route).
The session is one no flow made, so the route resolves against its frozen
plan, which carries a pool group directly; the pinned app clock keeps the
night key the same at any hour.
"""
from __future__ import annotations

import astrodeck.api.app as app_module
from astrodeck.events import night_key
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.session import Session, session_store
from test_flows_progress_route import (_Clock, _local,  # noqa: F401
                                       _restore_provider, api)

POOL = "pool-1"
MOSAIC = "mosaic-1"
ROUTE = "/api/sequence/retry-set-aside"
REASON = "RSN-1c gave up after repeated rejects"


def _plan() -> SequencePlan:
    step = ExposureStep(id="s", filter="L", exposure_s=60.0, count=3)

    def target(tid, group, **cell):
        return Target(id=tid, name=tid, ra_hours=1.0, dec_deg=20.0,
                      mosaic_group=group, steps=[step.model_copy()], **cell)

    return SequencePlan(
        name="beside",
        targets=[target("cand-a", POOL), target("cand-b", POOL),
                 target("pan-1", MOSAIC, panel_row=0, panel_col=0),
                 target("pan-2", MOSAIC, panel_row=0, panel_col=1)],
        groups=[TargetGroup(id=POOL, name="Pool", kind="pool"),
                TargetGroup(id=MOSAIC, name="Mosaic")])


def _dormant_with_everything_set_aside(monkeypatch) -> Session:
    clock = _Clock(_local(2026, 9, 20, 16, 0))
    monkeypatch.setattr(app_module, "time", clock)
    s = Session(name="beside", status="dormant", plan=_plan())
    for tid in ("cand-a", "cand-b", "pan-1", "pan-2"):
        s.note_set_aside(tid, REASON, night=night_key(clock.t),
                         kind="rejects", ts=clock.t - 300.0)
    write_json_atomic(session_store._path(s.id), s.model_dump(), backup=False)
    return s


def _logged(monkeypatch) -> list[str]:
    lines: list[str] = []
    real = app_module.bus.log

    def log(level, text, *a, **kw):
        lines.append(text)
        return real(level, text, *a, **kw)

    monkeypatch.setattr(app_module.bus, "log", log)
    return lines


def _standing(s: Session) -> set[str]:
    return {r["target_id"] for r in session_store.load(s.id).set_aside_on(
        night_key(app_module.time.time()))}


async def test_a_pools_id_is_a_group_the_retry_clears_as_candidates(
        api, monkeypatch):
    s = _dormant_with_everything_set_aside(monkeypatch)
    lines = _logged(monkeypatch)
    res = await api.client.post(ROUTE, json={"group": POOL,
                                             "session_id": s.id})
    assert res.status_code == 200, res.text
    assert sorted(res.json()["cleared"]) == ["cand-a", "cand-b"], res.text
    assert _standing(s) == {"pan-1", "pan-2"}, "only the pool's records clear"
    assert lines == [
        "Pool: set-aside candidates retried by the operator: cand-a, cand-b; "
        "the next start tonight takes them up"], lines


async def test_retrying_every_group_clears_both_and_words_each(
        api, monkeypatch):
    s = _dormant_with_everything_set_aside(monkeypatch)
    lines = _logged(monkeypatch)
    res = await api.client.post(ROUTE, json={"session_id": s.id})
    assert res.status_code == 200, res.text
    assert sorted(res.json()["cleared"]) == ["1-1", "1-2", "cand-a", "cand-b"]
    assert _standing(s) == set()
    assert sorted(lines) == [
        "Mosaic: set-aside panels retried by the operator: 1-1, 1-2; the "
        "next start tonight takes them up",
        "Pool: set-aside candidates retried by the operator: cand-a, cand-b; "
        "the next start tonight takes them up"], lines
