"""The progress route shows a locked angle and where it came from (#189,
Revision 2 owner ruling 9, the server half; spec 1.2, 6.9).

Ruling 9: an unframed TARGET takes the position angle its first plate solve
measures, the session stores it as the target's locked angle
(``Session.lock_angle``), and from then on every run commands it. "The flow
editor shows the locked angle and where it came from, so it is visible and
not a hidden fact." The flow editor's card reads ``GET
/api/flows/{id}/progress`` (``flows.progress.flow_progress``), so that answer
carries, per panel and per TARGET block, ``locked_angle``: the angle, and
where it came from in words (``LOCK_SOURCE``).

What it must NOT carry. The route is ``CAP_VIEW_STATUS`` and a viewer reads
it (spec 6.9), and a lock's times are when the run first reached the target,
a moment the target's altitude at the site decides. So the record's
``solved_at`` and ``exposed_at`` never reach the answer, and neither does
the free ``source`` text the engine stored, which nothing here can vet. The
key is absent where there is no lock, so an answer without one is S1's to
the byte, and S1's site-free progress tests
(``test_flows_progress.py``, ``test_flows_progress_route.py``) stay as they
were.

MUTATIONS. Each named mutant was applied to a byte-for-byte copy of
``flows/progress.py`` in a private copy of ``server/`` under the session
scratchpad, only this file was run there, and the copy was restored and
SHA-256 compared after every mutant; the shared tree was never written.
Failures are quoted from ``--tb=short``, wrapped to fit.
"""
from __future__ import annotations

import json
import math

import pytest

import astrodeck.api.app as app_module
import test_flows_progress as s1_pure
from astrodeck.auth import principal_for_role, set_active_provider
from astrodeck.auth.capabilities import CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS
from astrodeck.config import Site
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.progress import LOCK_SOURCE, _block_lock, flow_progress
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.session import Session, session_store
from test_flows_progress_route import (ALLOWED, SITE_A, SITE_B,
                                       TARGET_AND_POOL, _compiled, _Fixed)
from test_flows_progress_route import _frames as _route_frames
from test_flows_progress_route import (_restore_provider,  # noqa: F401
                                       api)  # noqa: F401 (fixtures)

#: The lock's two times: not round, so no other number in the answer can be
#: them by coincidence, and each spelled the way JSON would write it.
SOLVED_AT, EXPOSED_AT = 1790001234.125, 1790001221.5
#: What the engine might store as the lock's source: free text, with a clock
#: time in it, which the route must not forward.
STORED_SOURCE = "centring solve of the first light frame at 21:43:07"
#: Every spelling of the lock's times and stored text the answer must not hold.
WITHHELD = ("1790001234", "1790001221", "21:43", "solved_at", "exposed_at",
            STORED_SOURCE)


def _lock(s: Session, target_id: str, pa: float) -> None:
    s.lock_angle(target_id, pa, solved_at=SOLVED_AT, exposed_at=EXPOSED_AT,
                 source=STORED_SOURCE)


def _target_and_pool():
    """TARGET M31 feeding POOL (M42, M31) feeding one CAPTURE: a block of
    each kind. Returns (compiled, plan, the TARGET's target, the members)."""
    g = s1_pure
    graph = FlowGraph(
        nodes=[g._n("t", "target", name="M31", ra="00h 42m 44s",
                    dec="+41 16 09", rotation=-1),
               g._n("p", "pool", x=50, members="M42, M31", minAlt=0,
                    moonSep=0, maxHA=0),
               g._n("c", "capture", x=100, filter="L", exposure=60,
                    gain=100, bin="1", count=5, goal=0)],
        edges=[g._e("t", "target", "p", "arm"),
               g._e("p", "target", "c", "run")])
    compiled, plan = g._compile(graph)
    return compiled, plan, plan.targets[0], plan.targets[1:]


def _progress(compiled, plan, session):
    return flow_progress(compiled, plan, session, flow_id=s1_pure.FLOW)


def _walk(payload: dict) -> dict[str, set[str]]:
    """The keys seen at each level, including the new ``locked_angle``."""
    seen: dict[str, set[str]] = {"block": set(), "panel": set(),
                                 "locked_angle": set()}
    for block in payload["blocks"]:
        seen["block"] |= set(block)
        if "locked_angle" in block:
            seen["locked_angle"] |= set(block["locked_angle"])
        for panel in block["panels"]:
            seen["panel"] |= set(panel)
            if "locked_angle" in panel:
                seen["locked_angle"] |= set(panel["locked_angle"])
    return seen


# ================================================================ the pure half

class TestTheAnswerCarriesTheLock:
    def test_per_panel_and_per_target_block_with_its_source_in_words(self):
        """The TARGET is locked at 23.4 and the pool's M42 at 101.25: the
        TARGET's panel and block carry 23.4, M42's panel carries 101.25, and
        the pool block and the unlocked M31 member carry nothing.

        RED under mutant "no locked angle" (``_locked`` returns None as its
        first statement):

            KeyError: 'locked_angle'
        """
        compiled, plan, target, members = _target_and_pool()
        s = s1_pure._session(plan, [])
        _lock(s, target.id, 23.4)
        _lock(s, members[0].id, 101.25)
        got = _progress(compiled, plan, s)
        want = {"pa_deg": 23.4, "source": LOCK_SOURCE}
        tblock, pblock = got["blocks"]
        assert tblock["panels"][0]["locked_angle"] == want
        assert tblock["locked_angle"] == want
        assert [p["name"] for p in pblock["panels"]] == ["M42", "M31"]
        assert pblock["panels"][0]["locked_angle"] == {
            "pa_deg": 101.25, "source": LOCK_SOURCE}
        assert "locked_angle" not in pblock["panels"][1]
        assert "locked_angle" not in pblock
        assert LOCK_SOURCE == "measured by the first shot's plate solve"

    def test_never_the_lock_time_nor_the_stored_source_text(self):
        """The answer's JSON holds neither of the lock's times, nor the free
        text the engine stored as its source, in any spelling.

        RED under mutant "forward the record" (``_locked`` returns
        ``dict(lock)`` in place of the two keys it builds):

            AssertionError: the answer carries '1790001234'
            assert '1790001234' not in '{"flow_id":..."steps": 0}}'
              '1790001234' is contained here:
                lved_at": 1790001234.125, "exposed_at": 1790001221.5,
                "source": "centring solve of the first light frame at
                21:43:07"}}], ...

        RED under mutant "forward the stored source" (``"source":
        LOCK_SOURCE`` -> ``"source": lock.get("source")``):

            AssertionError: the answer carries '21:43'
            assert '21:43' not in '{"flow_id":..."steps": 0}}'
              '21:43' is contained here:
                 frame at 21:43:07"}}], "locked_angle": {"pa_deg": 23.4,
                 "source": "centring solve of the first light frame at
                 21:43:07"}}, ...
        """
        compiled, plan, target, members = _target_and_pool()
        s = s1_pure._session(plan, [])
        _lock(s, target.id, 23.4)
        _lock(s, members[0].id, 101.25)
        text = json.dumps(_progress(compiled, plan, s), allow_nan=False)
        assert "locked_angle" in text, "premise: the lock is in the answer"
        for needle in WITHHELD:
            assert needle not in text, f"the answer carries {needle!r}"

    def test_every_key_is_s1s_allow_list_plus_the_lock(self):
        """S1's allow-list (``test_flows_progress_route.ALLOWED``, the same
        list the pure half pins) holds every block and panel key but
        ``locked_angle``, and a lock holds exactly ``pa_deg`` and ``source``.

        RED under mutant "forward the record" (see above):

            AssertionError: a lock carries keys outside its allow-list
            assert {'exposed_at'...at', 'source'} == {'pa_deg', 'source'}
              Extra items in the left set:
              'exposed_at'
              'solved_at'
              Use -v to get more diff

        RED under mutant "no locked angle", on the premise that the lock is
        there to walk:

            AssertionError: assert set() == {'locked_angle'}
              Extra items in the right set:
              'locked_angle'
        """
        compiled, plan, target, members = _target_and_pool()
        s = s1_pure._session(plan, [])
        _lock(s, target.id, 23.4)
        _lock(s, members[0].id, 101.25)
        seen = _walk(_progress(compiled, plan, s))
        assert seen["block"] - ALLOWED["block"] == {"locked_angle"}
        assert seen["panel"] - ALLOWED["panel"] == {"locked_angle"}
        assert seen["locked_angle"] == {"pa_deg", "source"}, (
            "a lock carries keys outside its allow-list")

    def test_zero_is_an_angle(self):
        """0 is a real position angle (the #189 semantics flip, spec 3.1), so
        a lock at 0 is shown like any other.

        RED under mutant "a falsy angle is no lock" (``if (isinstance(pa,
        bool) ...`` -> ``if (not pa or isinstance(pa, bool) ...``):

            KeyError: 'locked_angle'
        """
        compiled, plan, target, _members = _target_and_pool()
        s = s1_pure._session(plan, [])
        _lock(s, target.id, 0.0)
        got = _progress(compiled, plan, s)
        assert got["blocks"][0]["panels"][0]["locked_angle"] == {
            "pa_deg": 0.0, "source": LOCK_SOURCE}
        assert got["blocks"][0]["locked_angle"]["pa_deg"] == 0.0

    def test_a_pool_block_carries_no_one_angle(self):
        """Both pool members locked to one angle: each panel shows it, the
        pool block does not, because its members are separate objects.

        RED under mutant "pools too" (the block lock asked of every block,
        the ``else:`` under the pool's name dropped so it runs for both
        kinds; ``else:`` -> ``if True:``):

            assert 'locked_angle' not in {'banked': 0, 'kind': 'pool',
            'locked_angle': {'pa_deg': 45.0, 'source': "measured by the first
            shot's plate solve"}, 'name': 'M42, M31', ...}
        """
        compiled, plan, _target, members = _target_and_pool()
        s = s1_pure._session(plan, [])
        for m in members:
            _lock(s, m.id, 45.0)
        pblock = _progress(compiled, plan, s)["blocks"][1]
        assert all(p["locked_angle"]["pa_deg"] == 45.0
                   for p in pblock["panels"])
        assert "locked_angle" not in pblock

    def test_control_no_lock_is_s1s_answer(self):
        """No lock, or a lock held by a target id this compile does not name
        (the old ids of a re-framed block, spec 3.3 and ruling 3): no
        ``locked_angle`` anywhere, and the answer equals the one for the
        same ledger with no ``locked_angles`` at all. A control: green on the
        code and under every mutant named in this file."""
        compiled, plan, _target, _members = _target_and_pool()
        frames = s1_pure._frames(plan.targets[0].steps[0].id, 2)
        bare = _progress(compiled, plan, s1_pure._session(plan, frames))
        s = s1_pure._session(plan, frames)
        _lock(s, "a-target-id-no-compile-names", 12.0)
        got = _progress(compiled, plan, s)
        assert got == bare
        assert "locked_angle" not in json.dumps(got)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, "12",
                                     True, None])
    def test_an_angle_that_is_not_a_finite_number_is_no_lock(self, bad):
        """``lock_angle`` refuses NaN and infinity, but a session file is
        read without that check, and NaN in the answer would fail the
        route's JSON rendering (``allow_nan=False``) for every reader. Such
        a record is shown as no lock, and the answer stays JSON-ready.

        RED under mutant "no finiteness check" (``or not
        math.isfinite(pa)`` removed), for nan, inf and -inf:

            ValueError: Out of range float values are not JSON compliant:
            nan

        (``inf`` and ``-inf`` in their turn); "12", True and None pass on
        it, as the type check still stands.
        """
        compiled, plan, target, _members = _target_and_pool()
        s = s1_pure._session(plan, [])
        s.locked_angles[target.id] = {"pa_deg": bad, "solved_at": SOLVED_AT,
                                      "exposed_at": None, "source": "solve"}
        got = _progress(compiled, plan, s)
        text = json.dumps(got, allow_nan=False)
        assert "locked_angle" not in text


class TestTheBlockLock:
    """``_block_lock`` over panels a multi-panel TARGET block will have once
    S3 compiles a grid; before S3 every TARGET block is one panel, so the
    flow cannot build these, and the rule is held here directly."""

    @staticmethod
    def _panel(tid, pa=None):
        p = {"target_id": tid}
        if pa is not None:
            p["locked_angle"] = {"pa_deg": pa, "source": LOCK_SOURCE}
        return p

    def test_one_angle_across_every_panel_is_the_blocks(self):
        """RED under mutant "any locked panel's angle" (the body replaced by
        ``locks = [p["locked_angle"] for p in panels if "locked_angle" in
        p]; return dict(locks[0]) if locks else None``), at the second case,
        the first it reaches:

            AssertionError: panels locked to different angles have no one
            angle
            assert {'pa_deg': 10.0, 'source': "measured by the first shot's
            plate solve"} is None
             +  where {'pa_deg': 10.0, 'source': "measured by the first
             shot's plate solve"} = _block_lock([{'locked_angle': {'pa_deg':
             10.0, 'source': "measured by the first shot's plate solve"},
             'target_id': 'a'}, {'locked_angle': {'pa_deg': 12.0, 'source':
             "measured by the first shot's plate solve"}, 'target_id': 'b'}])
        """
        p = self._panel
        assert _block_lock([p("a", 10.0), p("b", 10.0)]) == {
            "pa_deg": 10.0, "source": LOCK_SOURCE}
        assert _block_lock([p("a", 10.0), p("b", 12.0)]) is None, (
            "panels locked to different angles have no one angle")
        assert _block_lock([p("a", 10.0), p("b")]) is None, (
            "a block with an unlocked panel has no one angle")

    def test_control_a_panel_the_plan_dropped_is_not_asked(self):
        """A panel ``to_plan`` dropped (``target_id`` null) can hold no lock
        and does not stop the block showing its panels' one angle; a block
        of nothing but dropped panels shows none. The returned lock is a copy,
        so the block and the panel are two JSON objects."""
        p = self._panel
        panels = [p("a", 7.5), p(None)]
        got = _block_lock(panels)
        assert got == {"pa_deg": 7.5, "source": LOCK_SOURCE}
        assert got is not panels[0]["locked_angle"]
        assert _block_lock([p(None)]) is None
        assert _block_lock([]) is None


# ============================================================== the route half

def _seed_locked(fid: str, locks: dict[int, float]) -> Session:
    """The TARGET + POOL flow's session, dormant, with one frame on every
    target's first step and ``locks`` (plan target index -> angle), written
    as the file ``SessionStore.save`` writes."""
    _c, plan = _compiled(TARGET_AND_POOL, fid)
    frames = [f for t in plan.targets
              for f in _route_frames(t.id, t.steps[0].id, 1)]
    s = Session(name=f"{fid}@100", created_ts=100.0, updated_ts=100.0,
                status="dormant", plan=plan, nights=["night-1"],
                frames=frames, origin="flow", origin_id=fid)
    for index, pa in locks.items():
        _lock(s, plan.targets[index].id, pa)
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, s.model_dump(), backup=False)
    return s


class TestAViewerReadsTheLock:
    @pytest.mark.parametrize("role", ["viewer", "admin"])
    async def test_it_reads_the_lock_and_nothing_moves_with_the_site(
            self, api, role):
        """A viewer (and an admin) reads the flow's progress: the TARGET's
        block and panel carry the lock and its words, none of the lock's
        times or stored text reach the wire, and the body is byte-identical
        under two synthetic sites, the method of S1's
        ``TestItCarriesNoSiteData``.

        RED under mutant "no locked angle" (``_locked`` returns None as its
        first statement), for both roles:

            AssertionError: the viewer reads the lock
            assert None == {'pa_deg': 23.4, 'source': "measured by the first
            shot's plate solve"}
             +  where None = <built-in method get of dict object at
             0x0000025603D54780>('locked_angle')

        Red too, for both roles, under "forward the record" and "forward
        the stored source" (``Differing items: {'source': 'centring solve of
        the first light frame at 21:43:07'} != {'source': "measured by the
        first shot's plate solve"}``).
        """
        fid = await api.save_flow(TARGET_AND_POOL)
        _seed_locked(fid, {0: 23.4})
        principal = principal_for_role(role)
        set_active_provider(_Fixed(principal))
        if role == "viewer":
            assert principal.has(CAP_VIEW_STATUS)
            assert not principal.has(CAP_VIEW_SITE_DERIVED), (
                "premise: a viewer is a caller the site is withheld from")
        bodies = []
        for lat, lon in (SITE_A, SITE_B):
            api.store.set_site(Site(name="fixture", latitude=lat,
                                    longitude=lon, elevation_m=10.0,
                                    is_default=False))
            assert app_module.hub.site["latitude"] == lat, (
                "premise: the hub reads the site this test configured")
            r = await api.progress(fid)
            assert r.status_code == 200, r.text
            bodies.append(r.content)
        got = json.loads(bodies[0])
        want = {"pa_deg": 23.4, "source": LOCK_SOURCE}
        assert got["blocks"][0]["panels"][0].get("locked_angle") == want, (
            "the viewer reads the lock")
        assert got["blocks"][0].get("locked_angle") == want
        text = bodies[0].decode("utf-8")
        for needle in WITHHELD:
            assert needle not in text, f"the wire carries {needle!r}"
        assert bodies[0] == bodies[1], "the body moved with the site"
