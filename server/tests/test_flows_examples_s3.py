# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Examples after slice S3 (#189 U-09; spec 1.7, S3 item 4; Revision 2
rulings 2 and 9).

The Examples are the acceptance corpus: the Definition of Done requires every
one of them to "load, validate, and run end-to-end on the simulator with no
hardware" (examples.py). S3 changed them in three ways, and this file holds
each change to what it may and may not move:

* SLEW + CENTER is gone from all seven. Its settings never reached the run
  (spec 1.7), so dropping it must move NOTHING in any plan: each plan,
  deterministic target and step ids included, is graded against the plan the
  pre-S3 fixtures compiled to.
* Every node is created (``nodes.create_params``), so every TARGET and POOL
  counts accepted subs only. That is ruling 2's deliberate switch, and the one
  thing the plans may differ in. M31 keeps Rotate to PA 23.4 (ruling 9).
* An eighth Example, "M31 3x2, rotating": a real camera-field snapshot that
  says where it came from, a set angle, and the loop wire. It loads,
  validates, compiles to six panels in one rotating group, and runs on the
  simulator through the group driver.

Every mutant named below was applied in a private scratch copy of server/,
never in the shared tree (#254), and the failure it produced is quoted.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest

from _group_harness import T0, Night, group_hub, group_store  # noqa: F401
from astrodeck.catalog import framing
from astrodeck.catalog.coords import parse_dec, parse_ra
from astrodeck.config import fov_deg
from astrodeck.flows.compile import (compile_plan, is_multi_panel, lane_tail,
                                     loop_wires, owner_of)
from astrodeck.flows.doctor import CONVERGENCE_WARN_SHARE, check
from astrodeck.flows.examples import (M31_MOSAIC_FOV, M31_MOSAIC_FOV_FROM,
                                      M31_MOSAIC_SENSOR, examples)
from astrodeck.flows.models import FlowRecord
from astrodeck.flows.to_plan import losses, to_sequence_plan
from astrodeck.sequence.session import session_store

#: Any fixed instant: the pool Examples' members are placed by the catalogue
#: at it (every one of them a fixed row, so the instant moves nothing).
WHEN = 1788313689.0

MOSAIC_ID = "example-m31-mosaic"

#: Each pre-S3 Example's plan, compiled with its own id as the flow id (the
#: way `run_flow` compiles it), with the rules' ids blanked (they are minted
#: fresh on every compile) and hashed with sorted keys. Computed in a scratch
#: copy of server/ holding the pre-S3 examples.py, against the compile and
#: to_plan the S3 tree has. The target and step ids are in it, so a changed
#: id (which would orphan a ledger) is a changed hash.
#:
#: RE-PINNED IN BACKLOG WP-09 (#191, 2026-09-30): every Example's DUSK
#: WINDOW picks "Astro dusk" (the unset-Start default), which now compiles
#: its own ``schedule.twilight_deg`` (-18) onto every TARGET
#: (``compile._dusk_schedule``), a field no Example's plan had when these
#: hashes were last captured, so all seven move together. Regenerated from
#: the same ``_plan_digest`` against the fixed code, with no other change.
#:
#: RE-PINNED AGAIN FOR BACKLOG WP-34 (#195, 2026-09-30): ``SequencePlan``
#: gained ``resume_across_nights``, a field every one of these seven dumps
#: now carries (``plan.model_dump`` writes every field, not only the ones
#: ``compile_plan`` chose to write), so all seven move together again, this
#: time whatever its value: True for example-campaign and example-eaa
#: (their DUSK WINDOWs are not "Single night"), False for the other five
#: (#195: "Single night" means auto-resume does not arm across nights).
#: Diffed against the pre-change dump for each of the seven and confirmed
#: this one key is the only thing that moved. Regenerated from the same
#: ``_plan_digest`` against the fixed code, with no other change.
HEAD_PLAN_SHA256 = {
    "example-campaign":
        "712f21bc36fc88d0a52addba55d2f2b80852d087081d9636559863f1cb0cfede",
    "example-m31":
        "a3d327a352ee887fc00b058ede3ae0c73fbe4467c6708da66d0051442a373bad",
    "example-m16":
        "ad6365b76d02fc7a9fd55239b36a2bd9f00df1b48d47998a74a7fa100f66e0fd",
    "example-cycle":
        "e91f2e1e86e1f1565f213b1cb029ee76a10fb92afd8e45243bb1d1fe8fcf6031",
    "example-pool":
        "cd874f8c7eedb6b169dab1c42f07e96d1d6c2aed1cfc6fbb303e948f3c27985c",
    "example-nb":
        "2e178320e071762d3d2d06b31758343b1b93c47119fe37c89d6649b68f520ea3",
    "example-eaa":
        "fe4981cb80d1fe65d33bffa6333c2f99881713523cfe2c4424f313e7d70c9e47",
}


def _example(ex_id: str) -> FlowRecord:
    found = [e for e in examples() if e.id == ex_id]
    assert len(found) == 1, f"{ex_id}: {len(found)} Examples"
    return found[0]


def _plan(ex: FlowRecord):
    return to_sequence_plan(compile_plan(ex.graph, ex.name), ex.graph,
                            flow_id=ex.id, when=WHEN)


def _plan_digest(ex: FlowRecord) -> str:
    """``HEAD_PLAN_SHA256``'s hash of ``ex``'s plan as it compiles now, with
    the count mode set back to what every pre-S3 Example counted by."""
    plan, _ = _plan(ex)
    got = plan.model_dump(mode="json")
    for ins in got.get("instructions", []):
        ins["id"] = ""
    assert got["count_mode"] == "accepted", (ex.id, got["count_mode"])
    got["count_mode"] = "attempts"
    return hashlib.sha256(json.dumps(got, sort_keys=True).encode()).hexdigest()


class TestSlewIsGone:
    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_no_example_draws_a_slew_and_each_block_feeds_its_first_stage(
            self, ex):
        """Spec 1.7: "The wizard lane ..., the brief sentence ... and the
        Examples drop SLEW in slice S3." Each TARGET or POOL now feeds the
        stage that followed the SLEW, so that stage's owner is the block
        (``compile.owner_of``), and every stage still has one.

        RED under mutant "M31 keeps its SLEW" (n4 SLEW + CENTER put back
        between n2 and n5), observed verbatim:

            E   AssertionError: example-m31
            E   assert 'slew' not in ['dusk', 'target', 'safety', 'slew',
                'autofocus', 'guide', ...]

        (and ``test_no_example_draws_the_legacy_note[example-m31]``, on L1).
        """
        assert "slew" not in [n.type for n in ex.graph.nodes], ex.id
        for n in ex.graph.nodes:
            if n.type in ("autofocus", "guide", "capture", "cycle"):
                owner = owner_of(ex.graph, n.id)
                assert owner is not None and owner.type in ("target",
                                                            "pool"), n.id
        feeds = {e.from_: e.to for e in ex.graph.edges
                 if e.fromPort == "target"}
        for block, first in feeds.items():
            assert ex.graph.node(first).type in ("autofocus", "capture"), (
                ex.id, block, first)

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_no_example_draws_the_legacy_note(self, ex):
        """With no SLEW there is no L1 note, and no unmapped SLEW row."""
        assert not [i for i in check(ex.graph) if "SLEW" in i.text]
        _plan_, unmapped = _plan(ex)
        assert "nodes.slew" not in [u["key"] for u in unmapped]


class TestTheExamplesAreCreated:
    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_every_target_and_pool_counts_accepted_subs(self, ex):
        """Ruling 2: "The read-only Examples cannot be saved, so their
        fixtures are converted in S3". Every TARGET and POOL is created with
        "Accepted subs", so the plan counts accepted subs and no block
        disagrees (no M7 loss).

        RED under mutant "the Examples read the missing-key defaults"
        (``_graph`` takes ``default_params``), observed verbatim:

            E   AssertionError: assert {'Every sub taken'} == {'Accepted subs'}
            E     Extra items in the left set:
            E     'Every sub taken'
            E     Extra items in the right set:
            E     'Accepted subs'

        (that for example-m31; all eight went red, and so did every
        digest case below, on the count mode).
        """
        blocks = [n for n in ex.graph.nodes if n.type in ("target", "pool")]
        assert blocks, ex.id
        assert {n.params["counts"] for n in blocks} == {"Accepted subs"}
        plan, unmapped = _plan(ex)
        assert plan.count_mode == "accepted"
        assert "count_mode" not in [u["key"] for u in unmapped]

    def test_m31_keeps_rotate_to_pa_23_4(self):
        """Ruling 9: the rotator is set explicitly at the start of every run,
        and the M31 Example keeps the angle it was written with. Created
        params say "Any angle", so the Example has to say "Rotate to PA"
        itself, or its 23.4 would command nothing.

        RED under mutant "M31 loses its angle override" (``"angle": "Rotate
        to PA"`` dropped from n2), observed verbatim:

            E   AssertionError: assert ('Any angle', 23.4) == ('Rotate to PA',
                23.4)
            E     At index 0 diff: 'Any angle' != 'Rotate to PA'
        """
        ex = _example("example-m31")
        t = ex.graph.node("n2")
        assert (t.params["angle"], t.params["rotation"]) == ("Rotate to PA",
                                                             23.4)
        [entry] = compile_plan(ex.graph, ex.name)["targets"]
        assert entry["angle"] == "rotate"
        plan, _ = _plan(ex)
        assert [x.rotation_deg for x in plan.targets] == [23.4]

    def test_control_the_other_targets_command_no_angle(self):
        """The rest were written with rotation -1, and say "Any angle": no
        angle nobody chose reaches a rotator."""
        for ex in examples():
            if ex.id in ("example-m31", MOSAIC_ID):
                continue
            for n in ex.graph.nodes:
                if n.type == "target":
                    assert (n.params["angle"], n.params["rotation"]) == (
                        "Any angle", -1), ex.id
            plan, _ = _plan(ex)
            assert all(x.rotation_deg is None for x in plan.targets), ex.id


class TestDroppingSlewMovedNoPlanButTheCount:
    @pytest.mark.parametrize("ex_id", sorted(HEAD_PLAN_SHA256))
    def test_each_plan_is_the_pre_s3_plan_but_for_the_count(self, ex_id):
        """Spec 1.7: "A test pins that their compiled plans are unchanged",
        and ruling 2 re-pins the one thing that moves. Each plan, compiled as
        `run_flow` compiles it and with its count mode set back to
        "attempts", hashes to the pre-S3 fixture's plan: same targets, same
        steps, same ids, same rules.

        RED under mutant "M31 loses its angle override", on example-m31,
        observed verbatim:

            E   AssertionError: assert 'b6a523bd9b5d...0522fd2c3cb05' ==
                'f41f9f28fc17...b74670d123638'

        RED under mutant "NB's subs go to 240 s" (n7's exposure override
        300 -> 240, a change that is not the SLEW's), observed verbatim:

            E   AssertionError: assert 'cdb7e425b143...425dafcb96e33' ==
                '344a10d6652f...54d7d0d84b216'
        """
        assert _plan_digest(_example(ex_id)) == HEAD_PLAN_SHA256[ex_id]

    def test_the_seven_are_all_there_and_the_mosaic_is_the_eighth(self):
        """The control above is only as wide as its corpus."""
        ids = [e.id for e in examples()]
        assert ids == [*HEAD_PLAN_SHA256, MOSAIC_ID]


# ========================================================= the eighth Example

class TestTheEighthExample:
    def test_it_loads_and_validates_and_the_doctor_is_quiet(self):
        """Loads: it is in the Examples, read-only, and survives the JSON
        round trip the library serves it through. Validates: no structural
        error. And the doctor, whose regression corpus the Examples are, has
        nothing to say about it at all."""
        ex = _example(MOSAIC_ID)
        assert (ex.name, ex.readonly, ex.folder) == ("M31 3x2, rotating", True,
                                                     "Examples")
        again = FlowRecord.model_validate_json(ex.model_dump_json())
        assert again.graph.model_dump() == ex.graph.model_dump()
        assert ex.graph.validation_errors() == []
        assert check(ex.graph) == []

    def test_it_compiles_to_six_panels_in_one_rotating_group(self):
        """One TARGET entry with the 3x2 grid and the loop; to_plan expands
        it to six panels "M31 r-c" in compute_mosaic's snake order, all in
        one group shot "rotate", laid out at and commanded to PA 55, each an
        LRGB cycle of 20.

        RED under mutant "no loop wire" (the n7 pass -> n2 next wire dropped
        from the Example), observed verbatim:

            E   ValueError: not enough values to unpack (expected 1, got 0)

        (at ``[loop] = loop_wires(...)``; the load case went red with it, on
        doctor M3's warning).
        """
        ex = _example(MOSAIC_ID)
        t = ex.graph.node("n2")
        assert is_multi_panel(t)
        [loop] = loop_wires(ex.graph, t)
        assert loop.from_ == lane_tail(ex.graph, t).id == "n7"
        [entry] = compile_plan(ex.graph, ex.name)["targets"]
        assert ((entry["mosaic"]["rows"], entry["mosaic"]["cols"]),
                entry["loop"], entry["angle"]) == ((2, 3), True, "rotate")
        plan, unmapped = _plan(ex)
        [group] = plan.groups
        members = [x for x in plan.targets if x.mosaic_group == group.id]
        assert [x.name for x in members] == [
            "M31 1-1", "M31 1-2", "M31 1-3", "M31 2-3", "M31 2-2", "M31 2-1"]
        assert len(plan.targets) == 6
        assert (group.mode, group.rotate, group.pa_deg) == ("rotate", True,
                                                            55.0)
        for x in members:
            assert x.rotation_deg == 55.0 and x.acquisition == "cycle"
            assert [(s.filter, s.exposure_s, s.count) for s in x.steps] == [
                ("L", 120, 20), ("R", 120, 20), ("G", 120, 20),
                ("B", 120, 20)]
        # Notes only (a stage's settings the run takes elsewhere); nothing
        # `/run` would ask the operator to accept losing.
        assert losses(unmapped) == [], losses(unmapped)

    def test_its_camera_field_is_a_real_snapshot_that_says_where_from(self):
        """Spec 3.1: `fovX`/`fovY` are bin-1 degrees snapshotted at framing,
        and `fovFrom` says where they came from. The numbers are the rig's
        own formula (``config.fov_deg``) for the sensor the line names,
        rounded to the 3 places ``hub.effective_optics`` hands MATCH CAMERA,
        and the line names that sensor's size, pixel and focal length.

        RED under mutant "a field typed by hand" (``M31_MOSAIC_FOV = (1.35,
        0.9)``), observed verbatim:

            E   assert (1.35, 0.9) == (1.346, 0.9)
            E     At index 0 diff: 1.35 != 1.346
        """
        s = M31_MOSAIC_SENSOR
        fw, fh, _diag = fov_deg(s["focal_mm"], s["pixel_um"], s["width_px"],
                                s["height_px"])
        assert M31_MOSAIC_FOV == (round(fw, 3), round(fh, 3))
        t = _example(MOSAIC_ID).graph.node("n2")
        assert (t.params["fovX"], t.params["fovY"]) == M31_MOSAIC_FOV
        assert t.params["fovFrom"] == M31_MOSAIC_FOV_FROM
        # DELIBERATE PIN CHANGE (#405 item 2, S5-TONIGHT): "bin 1" is no
        # longer one of the line's facts. The modal's camera line names the
        # bin itself (the next test), so the line carrying it too made the
        # screen say "bin 1" twice.
        for fact in (f"{s['width_px']} x {s['height_px']} px",
                     f"{s['pixel_um']:g} um", f"{s['focal_mm']:g} mm"):
            assert fact in M31_MOSAIC_FOV_FROM, fact

    def test_its_provenance_names_no_bin_since_the_camera_line_does(self):
        """#405 item 2. GRID's camera-field line is the modal's
        ``cameraFieldLine`` (``framingModel.ts``), which writes "Tiled for
        X x Y deg at bin 1 (<fovFrom>)": it names the bin itself, for every
        block, because ``fovX``/``fovY`` are bin-1 degrees by definition
        (spec 3.1). The Example's ``fovFrom`` ended "(bin 1)" too, so its
        line read "... at bin 1 (IMX571 sensor, ... at 1000 mm focal length
        (bin 1))". The provenance names no bin, and the line, composed as
        ``cameraFieldLine`` composes it (read from its source, which this
        suite cannot run), says "bin 1" once.

        RED under mutant "(bin 1) restored" (``M31_MOSAIC_FOV_FROM`` ending
        "focal length (bin 1)" again), observed; the test above it stays
        green, since it no longer lists the bin among the line's facts:

            E       AssertionError: the Example's provenance names a bin,
            which the camera line already names: 'IMX571 sensor, 6248 x
            4176 px of 3.76 um, at 1000 mm focal length (bin 1)'
            E       assert 'bin' not in 'IMX571 sens...ngth (bin 1)'
            E         'bin' is contained here:
            E           l length (bin 1)
        """
        assert "bin" not in M31_MOSAIC_FOV_FROM, (
            f"the Example's provenance names a bin, which the camera line "
            f"already names: {M31_MOSAIC_FOV_FROM!r}")
        model = (Path(__file__).resolve().parents[2] / "ui" / "src" /
                 "components" / "flows" / "framing" / "framingModel.ts"
                 ).read_text(encoding="utf-8")
        found = re.search(r"export function cameraFieldLine\([^)]*\)[^{]*\{"
                          r"(.*?)\n\}", model, re.S)
        assert found, "premise: framingModel.ts defines cameraFieldLine"
        # The template nests a template literal for the provenance, so it
        # is read to the end of its line, not to the next backtick.
        (template,) = re.findall(r"return `(Tiled for .*)`;\s*$",
                                 found.group(1), re.M)
        assert "at bin 1${from ? ` (${from})` : \"\"}" in template, (
            f"premise: cameraFieldLine names the bin before the provenance: "
            f"{template}")
        t = _example(MOSAIC_ID).graph.node("n2")
        line = (f"Tiled for {t.params['fovX']:.2f} x "
                f"{t.params['fovY']:.2f} deg at bin 1 "
                f"({t.params['fovFrom']})")
        assert line.count("bin 1") == 1, line

    def test_its_angle_is_set_and_lays_the_grid_along_the_galaxy(self):
        """A set angle, Rotate to PA 55 (never a default: spec 1.8). The
        claim examples.py makes of it, measured from the panels: a column
        step points at PA 90 - 55 = 35 east of north, M31's major axis, and
        convergence at +41 uses a small share of the overlap (doctor M6
        would warn past ``CONVERGENCE_WARN_SHARE``)."""
        t = _example(MOSAIC_ID).graph.node("n2")
        assert (t.params["angle"], t.params["rotation"]) == ("Rotate to PA",
                                                             55.0)
        spec = {"ra_hours": parse_ra(t.params["ra"]),
                "dec_deg": parse_dec(t.params["dec"]), "rows": 2, "cols": 3,
                "overlap": t.params["overlap"] / 100,
                "rotation_deg": t.params["rotation"],
                "fov_x_deg": t.params["fovX"], "fov_y_deg": t.params["fovY"]}
        m = framing.compute_mosaic(spec)
        at = {(p["row"], p["col"]): p for p in m["panels"]}
        west, east = at[(0, 0)], at[(0, 2)]
        xi, eta = framing.project(east["ra_hours"], east["dec_deg"],
                                  west["ra_hours"], west["dec_deg"])
        assert math.degrees(math.atan2(xi, eta)) == pytest.approx(35, abs=1)
        assert m["total_fov_x_deg"] == pytest.approx(3.365, abs=1e-3)
        assert framing.convergence_share(spec) < CONVERGENCE_WARN_SHARE

    async def test_it_runs_on_the_simulator_through_the_group_driver(
            self, group_hub, monkeypatch):
        """The Definition of Done's "run end-to-end on the simulator", through
        the REAL group driver (tests/_group_harness.py): the Example compiled
        as `run_flow` compiles it, with a BOUNDED COUNT of 2 subs per filter
        so the night is 48 frames, on the clocked simulator two hours after
        the harness's T0, when M31 stands high and east at the fixture site
        and far from any flip (the harness's catalogue clock on, so the sim
        mount's pier side reads the night's time, not the wall's: #320). The
        sky angle each centring solve reports is the layout's 55.

        The panels ROTATE: two passes, each visiting all six panels once and
        shooting one LRGB round there, so each panel is visited twice and
        shot 2 x L R G B, and the session ends complete.

        RED under mutant "no loop wire", observed verbatim (the group is
        shot panel first: six visits of eight frames):

            E   AssertionError: [('M31 1-1', ('L', 'R', 'G', 'B', 'L', 'R',
                ...)), ('M31 1-2', ('L', 'R', 'G', 'B', 'L', 'R', ...)),
                ('M31 1-3', ('L',...', 'L', 'R', ...)), ('M31 2-2', ('L',
                'R', 'G', 'B', 'L', 'R', ...)), ('M31 2-1', ('L', 'R', 'G',
                'B', 'L', 'R', ...))]
            E   assert [] == ['M31 1-1', '...2', 'M31 2-3']
            E     Right contains 6 more items, first extra item: 'M31 1-1'
        """
        ex = _example(MOSAIC_ID)
        plan, _ = _plan(ex)
        for target in plan.targets:
            for step in target.steps:
                step.count = 2
        night = Night(group_hub, monkeypatch, t0=T0 + 2 * 3600.0,
                      sky=lambda who, n: 55.0, coords_clock=True)
        try:
            done = await night.run(plan)
        finally:
            await night.close()
        assert done, night.trace[-5:]
        names = [x.name for x in plan.targets]
        visits = night.visits()
        assert [who for who, _f in visits[:6]] == names, visits
        assert sorted(who for who, _f in visits[6:]) == sorted(names), visits
        assert len(visits) == 12, visits
        assert all(f == ("L", "R", "G", "B") for _who, f in visits), visits
        assert Counter(night.shots()) == {(n, f): 2 for n in names
                                          for f in "LRGB"}
        stored = session_store.load(night.session_id)
        assert stored.status == "complete", stored.status
