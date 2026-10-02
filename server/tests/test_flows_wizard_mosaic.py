# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The wizard's Mosaic kind: the generator behind "Send to Flow Wizard"
(#196, the generator half; #189 U-09; spec 1.4, 1.7, 1.8, S3 item 4).

One TARGET block laid out as a grid, its panel lane without SLEW + CENTER, and
the dashed "pass done" wire from the tail of that lane back to TARGET "next
panel", so the panels rotate every pass. Spec 1.8 sets the bar and the two
things the wizard must supply honestly for it:

* THE DOCTOR BAR. Every generated graph passes at note level or better across
  the option matrix, the Mosaic kind included, with the rig facts the route
  hands over (the doctor reads the rotator and the live field from them).
* THE ANGLE comes from the operator, or from USE MEASURED (the sky angle the
  last centring solve recorded, a rig fact the route injects), and never from
  a default: a default angle nobody chose is the I-04 defect, 23.4 commanding
  a rotator (#150). A grep of the kind's source holds that, and a refusal
  holds it behaviourally.
* THE CAMERA FIELD comes from the rig's live optics. With none, the answer is
  a single target and the reason, never a graph of 0 x 0 degree panels
  (doctor M1).

Every mutant named below was applied in a private scratch copy of server/,
never in the shared tree (#254), and the failure it produced is quoted.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import math
import types
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
import astrodeck.profiles as profiles_module
from astrodeck.catalog import framing
from astrodeck.catalog.coords import parse_dec, parse_ra
from astrodeck.config import Optics
from astrodeck.flows import tonight, wizard
from astrodeck.flows.compile import (compile_plan, is_multi_panel, lane_tail,
                                     loop_wires)
from astrodeck.flows.doctor import check
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.wizard import (
    AUTOMATION_OPTIONS, CAMERA_FIXED_AT_PA, DEFAULT_OPTIONS, KIND_DEEP_SKY,
    KIND_EAA, KIND_MOSAIC, KIND_POOL, NO_OPTICS_REASON, ROTATE_TO_PA,
    generate, generate_answer, generate_record)
from astrodeck.profiles import Profile, ProfileDevice

#: The spec's compact 2x2 field (A.4): 0.9 x 0.6 deg at bin 1.
FIELD = (0.9, 0.6)
WITH_ROTATOR = RigFacts(fov_deg=FIELD, fov_from="profile Test, matched in "
                        "this test", has_rotator=True)
NO_ROTATOR = RigFacts(fov_deg=FIELD, has_rotator=False)
ROTATOR_UNKNOWN = RigFacts(fov_deg=FIELD)

#: A fixed catalogue row at Dec +34, so the grid's coordinates are typed and
#: the doctor's convergence rules (M6, M15) measure it.
NAME = "NGC 7331"
#: Any fixed instant; nothing here depends on the sky at it.
WHEN = 1788313689.0


@pytest.fixture(autouse=True)
def _one_catalogue_search_per_name(monkeypatch):
    """The catalogue search behind ``tonight.resolve_target`` costs about 20
    ms a call, and the matrix below asks it the same name about 1500 times.
    The REAL resolver answers each (name, instant) once per test and its
    answer is replayed; nothing is stubbed, and a test that replaces the
    resolver itself still can."""
    real, memo = tonight.resolve_target, {}

    def once(name, when=None):
        if (name, when) not in memo:
            memo[(name, when)] = real(name, when)
        return memo[(name, when)]

    monkeypatch.setattr(tonight, "resolve_target", once)


def _subsets(items):
    for mask in range(1 << len(items)):
        yield frozenset(x for i, x in enumerate(items) if mask & (1 << i))


def _target(graph):
    [t] = [n for n in graph.nodes if n.type == "target"]
    return t


def _mosaic(opts=DEFAULT_OPTIONS, *, rig=WITH_ROTATOR, rows=2, cols=2,
            angle_mode=ROTATE_TO_PA, pa_deg=30.0, **kw):
    return generate(KIND_MOSAIC, opts, NAME, rows=rows, cols=cols,
                    angle_mode=angle_mode, pa_deg=pa_deg, rig=rig, **kw)


#: The angle cases of the matrix: the answer's angle mode, where the PA
#: comes from, and the rig the route would hand over (spec 2.4: Rotate to PA
#: is locked when the profile has no rotator, so that pair is a refusal and
#: is graded on its own below).
ANGLE_CASES = {
    "rotate-typed": dict(angle_mode=ROTATE_TO_PA, pa_deg=30.0,
                         rig=WITH_ROTATOR),
    "rotate-measured": dict(angle_mode=ROTATE_TO_PA, use_measured=True,
                            measured_pa_deg=212.5, rig=ROTATOR_UNKNOWN),
    "fixed-typed": dict(angle_mode=CAMERA_FIXED_AT_PA, pa_deg=30.0,
                        rig=NO_ROTATOR),
    "fixed-measured": dict(angle_mode=CAMERA_FIXED_AT_PA, use_measured=True,
                           measured_pa_deg=-4.8, rig=NO_ROTATOR),
}
GRIDS = {"2x2": (2, 2), "3x2": (2, 3), "1x3": (1, 3)}
STAGES = {"capture": "", "cycle": "L 60, R 60, G 60"}


class TestTheOptionMatrixPassesTheDoctor:
    """Spec 1.8: "The wizard must still produce output that passes the doctor
    at note level or better across its option matrix". Every chip subset, for
    every angle case, grid and capture stage, asked four questions at once
    (the collected-failures shape of test_flows_wizard's matrix)."""

    @pytest.mark.parametrize("stage", sorted(STAGES))
    @pytest.mark.parametrize("grid", sorted(GRIDS))
    @pytest.mark.parametrize("case", sorted(ANGLE_CASES))
    def test_every_mosaic_answer_is_one_the_night_can_run(self, case, grid,
                                                          stage):
        """THE DOCTOR IS QUIET (nothing above note) with the rig facts the
        answer was made from; THE GRAPH IS VALID; THE LOOP WIRE LEAVES THE
        TAIL of the block's panel lane, and is the only pass wire; and the
        compile reads it as a loop: one entry with the grid, ``loop`` true.
        The first and last chip subsets also go through ``to_plan``: one
        rotating group of rows x cols panels.

        RED under mutant "no loop wire" (``_wire_the_loop`` not called),
        observed verbatim (every case; the doctor's M3 first):

            E   AssertionError: []: doctor says ["▸ TARGET NGC 7331 - panels
                are shot one after another: a night cut short leaves the last
                panels empty. Wire CAPTURE LOOP L 'pass done' to TARGET 'next
                panel' to rotate panels every pass."]
            E     []: pass wires [], tail id='n4' type='capture' ...
            E     []: loop_wires []
            E     []: compiled {'rows': 2, 'cols': 3, ...} loop=False
            E     []: groups [('sequential', 6)]

        (that for [rotate-typed-3x2-capture]; all 24 cases went red).
        """
        kw = dict(ANGLE_CASES[case])
        rig = kw.pop("rig")
        rows, cols = GRIDS[grid]
        problems: list[str] = []
        subsets = list(_subsets(AUTOMATION_OPTIONS))
        for i, opts in enumerate(subsets):
            where = f"{sorted(opts)}"
            g = generate(KIND_MOSAIC, opts, NAME, rows=rows, cols=cols,
                         rig=rig, cycle_plan=STAGES[stage], cycles=3, **kw)
            loud = [x.text for x in check(g, rig=rig)
                    if x.level in ("warn", "danger")]
            if loud:
                problems.append(f"{where}: doctor says {loud}")
            errs = g.validation_errors()
            if errs:
                problems.append(f"{where}: invalid graph {errs}")
            t = _target(g)
            tail = lane_tail(g, t)
            passes = [e for e in g.edges if e.fromPort == "pass"]
            if ([(e.from_, e.to, e.toPort) for e in passes]
                    != [(tail.id, t.id, "next")]):
                problems.append(f"{where}: pass wires {passes}, tail {tail}")
            if len(loop_wires(g, t)) != 1:
                problems.append(f"{where}: loop_wires {loop_wires(g, t)}")
            [entry] = compile_plan(g, "m")["targets"]
            if (entry["mosaic"] or {}).get("rows") != rows or not entry["loop"]:
                problems.append(f"{where}: compiled {entry['mosaic']} "
                                f"loop={entry['loop']}")
            if i in (0, len(subsets) - 1):
                plan, _ = to_sequence_plan(compile_plan(g, "m"), g,
                                           flow_id="matrix", when=WHEN)
                modes = [(x.mode, len([p for p in plan.targets
                                       if p.mosaic_group == x.id]))
                         for x in plan.groups]
                if modes != [("rotate", rows * cols)]:
                    problems.append(f"{where}: groups {modes}")
        assert not problems, "\n".join(problems[:6])

    def test_the_sheets_defaults_as_a_mosaic_draw_nothing_at_all(self):
        """Not "nothing above note": nothing, for a rotator the rig has."""
        assert check(_mosaic(), rig=WITH_ROTATOR) == []

    def test_a_fixed_camera_draws_only_m8s_note(self):
        """The one note the matrix allows, and the reason it is a note: with
        no rotator the operator turns the camera by hand, and the run checks
        the angle at every panel (doctor M8)."""
        issues = check(_mosaic(angle_mode=CAMERA_FIXED_AT_PA, rig=NO_ROTATOR),
                       rig=NO_ROTATOR)
        assert [i.level for i in issues] == ["note"], issues
        assert ("camera fixed: turn the camera by hand to PA 30.0"
                in issues[0].text), issues[0].text


# ================================================================ the angle

#: The functions the Mosaic kind runs, in the order ``_generate`` reaches
#: them. `test_the_grep_watches_the_code_the_kind_runs` proves each is on the
#: live path, so the grep below cannot be watching dead code.
MOSAIC_KIND = ("_mosaic_params", "_mosaic_grid", "_mosaic_angle",
               "_wire_the_loop")

#: The numbers those functions may reach by name, none of them an angle:
#: wizard.py's four (it says what each is), the two grid bounds they import
#: from ``to_plan``, and ``framing.DEFAULT_OVERLAP``.
NAMED_NUMBERS = {"GRID_MIN", "OVERLAP_MIN_PCT", "_PERCENT", "_FULL_TURN_DEG",
                 "GRID_MAX", "OVERLAP_MAX_PCT", "framing.DEFAULT_OVERLAP"}


def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(Path(wizard.__file__).read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _holds_a_number(value) -> bool:
    """A number, or a tuple, list or set holding one. The containers count
    because ``_FIELD = (0.9, 0.6)`` beside the kind is a number reached by
    name just as ``_PA = 0.0`` is, and read as ``_FIELD[0]`` or unpacked it
    is invisible to a check that only asks whether the name is a number."""
    if type(value) in (int, float):
        return True
    return (isinstance(value, (tuple, list, set, frozenset))
            and any(type(x) in (int, float) for x in value))


def _numbers_reached(funcs: dict[str, ast.FunctionDef]) -> dict[str, object]:
    """Every name (``X``) and module attribute (``mod.X``) the Mosaic kind's
    functions read whose value is a number or holds one (``_holds_a_number``),
    resolved the way Python would: a name the function imports is the
    imported object, any other name is a wizard.py global."""
    imported: dict[str, object] = {}
    for fname in MOSAIC_KIND:
        for node in ast.walk(funcs[fname]):
            if isinstance(node, ast.ImportFrom):
                base = importlib.import_module(importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), wizard.__package__))
                for alias in node.names:
                    value = getattr(base, alias.name, None)
                    if value is None:
                        value = importlib.import_module(
                            f"{base.__name__}.{alias.name}")
                    imported[alias.asname or alias.name] = value

    def resolve(name: str):
        return imported[name] if name in imported else getattr(wizard, name,
                                                               None)

    found: dict[str, object] = {}
    for fname in MOSAIC_KIND:
        for node in ast.walk(funcs[fname]):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                value, label = resolve(node.id), node.id
            elif (isinstance(node, ast.Attribute)
                  and isinstance(node.value, ast.Name)):
                owner = resolve(node.value.id)
                value = getattr(owner, node.attr, None) if isinstance(
                    owner, types.ModuleType) else None
                label = f"{node.value.id}.{node.attr}"
            else:
                continue
            if _holds_a_number(value):
                found[label] = value
    return found


class TestNoAngleIsEverDefaulted:
    def test_no_numeric_literal_in_the_mosaic_kind(self):
        """THE GREP (spec S3 tests: "a grep test asserts no numeric angle
        literal in the mosaic kind"). Stricter than asked, on purpose: the
        kind's functions hold no numeric literal at all, so there is no
        number in them an angle could be defaulted to, and every number they
        reach by name is one of ``NAMED_NUMBERS``, none an angle. A number
        reached by any other name, global, imported inside the function, or
        a module's attribute (``framing.X``), fails the second half, so a
        ``_DEFAULT_PA = 0.0`` beside them cannot slip past either.

        RED under mutant "default PA 0" (``_mosaic_angle`` answers a missing
        PA with ``pa_deg if pa_deg is not None else 0``), observed verbatim:

            E   AssertionError: ['_mosaic_angle:410: 0']
            E   assert not ['_mosaic_angle:410: 0']

        RED under mutant "a named default PA" (``_DEFAULT_PA = 0.0`` at module
        level, and the same fallback reads it), observed verbatim:

            E   assert {'GRID_MAX', ...URN_DEG', ...} <= {'GRID_MAX',
                ...PERCENT', ...}
            E     Extra items in the left set:
            E     '_DEFAULT_PA'

        Mutants that add a literal elsewhere in the kind ("emit an M1 graph",
        "overlap default 15%") turn it red too, and "no fold" does through the
        known positives (``_FULL_TURN_DEG`` is no longer reached).

        A CONTAINER OF NUMBERS COUNTS (``_holds_a_number``, added by the S3-W
        verifier): mutant "field hard-coded" (``fov_x, fov_y = _SPEC_FIELD``,
        a module global ``(0.9, 0.6)``) passed the whole file while this
        resolver asked only whether a name was a number. Observed verbatim
        once it asks about containers too:

            E     Extra items in the left set:
            E     '_SPEC_FIELD'
        """
        funcs = _functions()
        missing = [f for f in MOSAIC_KIND if f not in funcs]
        assert not missing, f"the grep watches functions that are gone: {missing}"
        literals = [f"{fname}:{node.lineno}: {node.value!r}"
                    for fname in MOSAIC_KIND
                    for node in ast.walk(funcs[fname])
                    if isinstance(node, ast.Constant)
                    and type(node.value) in (int, float, complex)]
        assert not literals, literals
        reached = _numbers_reached(funcs)
        # The known positives, so a resolver that sees nothing cannot pass.
        assert {"GRID_MIN", "_FULL_TURN_DEG", "GRID_MAX",
                "framing.DEFAULT_OVERLAP"} <= set(reached), reached
        # And the container branch's, since no name the kind reads today is
        # a container of numbers: a field-shaped tuple is one, the kind's
        # tuple of angle names and a bool are not.
        assert _holds_a_number((0.9, 0.6)) and _holds_a_number([30])
        assert not _holds_a_number(wizard.MOSAIC_ANGLES)
        assert not _holds_a_number(True) and not _holds_a_number((True,))
        assert set(reached) <= NAMED_NUMBERS, reached

    def test_the_grep_watches_the_code_the_kind_runs(self, monkeypatch):
        """The control the grep needs: each function it reads is on the path
        a Mosaic answer takes, so a default could not hide in a copy of it
        the kind actually calls. Each is replaced in turn by one that raises,
        and the answer must raise it."""
        class Reached(Exception):
            pass

        for fname in MOSAIC_KIND:
            with monkeypatch.context() as m:
                def _stop(*a, _f=fname, **k):
                    raise Reached(_f)
                m.setattr(wizard, fname, _stop)
                with pytest.raises(Reached, match=fname):
                    _mosaic()

    def test_no_angle_is_a_refusal_naming_both_ways_to_give_one(self):
        """The behaviour the grep stands for. Neither a PA nor USE MEASURED:
        refused, never laid out at 0 or at anything else.

        RED under mutant "default PA 0", observed verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>

        The control for the grep: "no loop wire" makes
        ``test_the_grep_watches_the_code_the_kind_runs`` red (``_wire_the_loop``
        is no longer reached, so replacing it raises nothing).
        """
        with pytest.raises(ValueError, match="type the PA, or use the angle "
                                             "the camera measured"):
            _mosaic(pa_deg=None)

    def test_the_measured_angle_is_not_a_default_either(self):
        """The route may always hand over the last measured angle; it is used
        only when the answer asks for it (USE MEASURED). Otherwise a mosaic
        with no PA would be laid out at whatever the camera last read, which
        is a default by another name.

        RED under mutant "measured as a default" (``_mosaic_angle`` falls back
        to ``measured_pa_deg`` when no PA is typed), observed verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match="type the PA"):
            _mosaic(pa_deg=None, measured_pa_deg=37.2)

    def test_the_operators_pa_is_written_as_given(self):
        t = _target(_mosaic(pa_deg=30.0))
        assert (t.params["angle"], t.params["rotation"]) == (ROTATE_TO_PA,
                                                             30.0)

    def test_use_measured_lays_the_grid_out_at_the_measured_angle(self):
        """Spec 2.4: "One tap lays the grid out at the angle the camera
        actually sits at. That is the no-rotator workflow." """
        t = _target(_mosaic(angle_mode=CAMERA_FIXED_AT_PA, pa_deg=None,
                            use_measured=True, measured_pa_deg=212.5,
                            rig=NO_ROTATOR))
        assert (t.params["angle"], t.params["rotation"]) == (
            CAMERA_FIXED_AT_PA, 212.5)

    def test_a_negative_angle_is_folded_not_read_as_no_angle(self):
        """A negative `rotation` is "Any angle" to every reader (#150), so a
        measured -4.8 written as it came would lay out a mosaic with no angle
        (doctor M2, a refusal at Run). It is the same angle as 355.2.

        RED under mutant "no fold" (``float(value)`` returned without
        ``% _FULL_TURN_DEG``), observed verbatim:

            E   assert -4.8 == 355.2 ± 3.6e-04
            E     comparison failed
            E     Obtained: -4.8
            E     Expected: 355.2 ± 3.6e-04

        (and the matrix's six fixed-measured cases, on doctor M2's danger).
        """
        g = _mosaic(angle_mode=CAMERA_FIXED_AT_PA, pa_deg=None,
                    use_measured=True, measured_pa_deg=-4.8, rig=NO_ROTATOR)
        assert _target(g).params["rotation"] == pytest.approx(355.2)
        assert [i for i in check(g) if i.level == "danger"] == []

    def test_use_measured_with_nothing_measured_is_refused(self):
        with pytest.raises(ValueError, match="no measured angle yet"):
            _mosaic(pa_deg=None, use_measured=True, measured_pa_deg=None)

    def test_a_pa_and_use_measured_together_are_refused(self):
        with pytest.raises(ValueError, match="not both"):
            _mosaic(pa_deg=30.0, use_measured=True, measured_pa_deg=37.2)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "30", True])
    def test_a_pa_that_is_not_a_finite_number_is_refused(self, bad):
        with pytest.raises(ValueError, match="finite number of degrees"):
            _mosaic(pa_deg=bad)

    def test_any_angle_is_refused_for_a_mosaic(self):
        """Doctor M2: a grid is laid out at one angle."""
        with pytest.raises(ValueError, match="laid out at one camera angle"):
            _mosaic(angle_mode=wizard.ANY_ANGLE)

    def test_rotate_to_pa_is_refused_when_the_rig_has_no_rotator(self):
        """Spec 2.4: ROTATE TO "is locked with a reason when the active
        profile has no rotator"; the generator must not offer what the
        editor would not. Doctor M8 would warn on it.

        RED under mutant "no rotator check" (the ``has_rotator is False``
        refusal taken out), observed verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match="no rotator"):
            _mosaic(rig=NO_ROTATOR)

    def test_an_unknown_rotator_is_not_a_no(self):
        """``has_rotator`` None is "nobody said", which RigFacts keeps apart
        from False; the answer stands and the doctor stays silent on M8."""
        g = _mosaic(rig=ROTATOR_UNKNOWN)
        assert _target(g).params["angle"] == ROTATE_TO_PA
        assert check(g, rig=ROTATOR_UNKNOWN) == []


# ============================================================ the camera field

class TestTheCameraFieldComesFromTheRig:
    @pytest.mark.parametrize("rig", [None, RigFacts(),
                                     RigFacts(has_rotator=True)],
                             ids=["no-facts", "empty-facts", "rotator-only"])
    def test_no_optics_is_one_target_and_the_reason(self, rig):
        """Spec 1.8: "With no optics it answers with a single target and says
        why ... It never emits an M1 graph." The reason is the spec's words,
        on the answer and on the card; the graph is the Deep-sky answer's for
        the same target and chips, exactly (the control); and the doctor has
        nothing above a note to say about it.

        RED under mutant "emit an M1 graph" (``_mosaic_params`` lays the grid
        out anyway with the rig's field read as 0 x 0 when it has none, and
        no reason), observed verbatim:

            E   AssertionError: assert () == ('set the cam...an a mosaic',)
            E     Right contains one more item: 'set the camera and focal
                length in Settings > Optics to plan a mosaic'

        (all three cases; and every Mosaic answer of test_flows_wizard.py's
        matrix, on doctor M1's danger).
        """
        ans = generate_answer(KIND_MOSAIC, DEFAULT_OPTIONS, NAME, rows=2,
                              cols=3, angle_mode=ROTATE_TO_PA, pa_deg=55.0,
                              rig=rig)
        assert NO_OPTICS_REASON == ("set the camera and focal length in "
                                    "Settings > Optics to plan a mosaic")
        assert ans.notes == (NO_OPTICS_REASON,)
        g = ans.record.graph
        assert not is_multi_panel(_target(g))
        assert not any(e.fromPort == "pass" for e in g.edges)
        assert [i.text for i in check(g) if i.level != "note"] == []
        assert g.model_dump() == generate(KIND_DEEP_SKY, DEFAULT_OPTIONS,
                                          NAME).model_dump()
        assert ans.record.tagline.endswith(
            f"planned as one target: {NO_OPTICS_REASON}")

    def test_control_with_optics_it_is_a_mosaic_with_no_reason(self):
        ans = generate_answer(KIND_MOSAIC, DEFAULT_OPTIONS, NAME, rows=2,
                              cols=3, angle_mode=ROTATE_TO_PA, pa_deg=55.0,
                              rig=WITH_ROTATOR)
        assert ans.notes == ()
        t = _target(ans.record.graph)
        assert is_multi_panel(t)
        assert ans.record.tagline == "Generated by the wizard — mosaic"

    def test_the_generator_never_takes_the_field_from_the_config(
            self, isolated_config):
        """The field is INJECTED (spec 1.8): the generator never reads it
        off the config, a profile or the hub itself, which is why the route
        is the one place that says what the rig is and why no case in this
        file needs a config of its own (#341; a probe of the whole suite
        found this file and test_flows_wizard.py reading no optics and no
        profile at all). Here the test's own config HAS a field, from its
        optics and from an active profile with optics of its own, and an
        answer handed no rig facts is still one target and the reason.

        The no-facts case above cannot see this: it runs on a config with no
        optics, where a generator that looked would find nothing either.

        RED under mutant "the wizard reads the rig's optics itself" (in
        ``_mosaic_params``, with no field handed in, the field taken from
        ``hub.effective_optics()`` when it has one), run in a private copy
        of ``server/`` (scratchpad s4-testhyg-mut2), observed verbatim:

            E       AssertionError: assert () == ('set the cam...an a
            mosaic',)
            E         Right contains one more item: 'set the camera and focal
            length in Settings > Optics to plan a mosaic'
            FAILED tests/test_flows_wizard_mosaic.py::
            TestTheCameraFieldComesFromTheRig::
            test_the_generator_never_takes_the_field_from_the_config
            1 failed, 6 passed, 66 deselected in 1.37s

        The six that passed include all three cases of the no-facts test
        above.
        """
        optics = Optics(focal_length_mm=1000.0, pixel_size_um=3.76,
                        sensor_width_px=6248, sensor_height_px=4176)
        isolated_config.store.set_optics(optics)
        profiles_module.profiles.save(Profile(
            id="wizard-pure", name="Refractor", primary_backend="native",
            optics=optics.model_copy(update={"focal_length_mm": 500.0}),
            devices=[ProfileDevice(role="rotator", backend="native")]))
        isolated_config.store.cfg().active_profile_id = "wizard-pure"
        assert hub_module.hub.effective_optics()["have_optics"], (
            "premise: the rig's config has a field to take")
        ans = generate_answer(KIND_MOSAIC, DEFAULT_OPTIONS, NAME, rows=2,
                              cols=3, angle_mode=ROTATE_TO_PA, pa_deg=55.0,
                              rig=None)
        assert ans.notes == (NO_OPTICS_REASON,)
        assert not is_multi_panel(_target(ans.record.graph))

    def test_the_field_and_its_provenance_are_the_rigs(self):
        """``fovX``/``fovY`` are the live effective optics at bin 1, which is
        what the modal's MATCH CAMERA snapshots (spec 2.4), and ``fovFrom``
        says where they came from. With no provenance line from the route,
        the node still says it came from the rig."""
        t = _target(_mosaic())
        assert (t.params["fovX"], t.params["fovY"], t.params["fovFrom"]) == (
            0.9, 0.6, "profile Test, matched in this test")
        t = _target(_mosaic(rig=ROTATOR_UNKNOWN))
        assert t.params["fovFrom"] == "the rig's live optics when the wizard " \
                                      "planned it"

    def test_a_second_camera_lays_out_a_second_grid(self):
        """Every other case here hands over the spec's 0.9 x 0.6 deg field,
        so a kind that read only WHETHER the rig has a field, and wrote a
        field of its own, would pass them all. A second camera (the eighth
        Example's IMX571 at 1000 mm) must reach the node, the compile entry
        and the panels: neighbouring columns sit one field width less the
        overlap apart, 1.346 x 0.75 = 1.0095 deg here, where the spec's
        field would put them 0.675 deg apart.

        Added by the S3-W verifier. RED under mutant "field hard-coded"
        (``_mosaic_params`` writes ``fov_x, fov_y = _SPEC_FIELD``, a module
        global ``(0.9, 0.6)``, in place of the rig's field), which every
        other test in this file, test_flows_wizard.py, test_flows_quick.py,
        test_flows_examples_s3.py, the clean-flow file and the route file
        left GREEN (530 passed) before this case and the grep's container
        branch were added, observed verbatim:

            E   assert (0.9, 0.6) == (1.346, 0.9)
            E     At index 0 diff: 0.9 != 1.346

        and in ``test_no_numeric_literal_in_the_mosaic_kind``, whose
        resolver now sees a container of numbers reached by name:

            E     Extra items in the left set:
            E     '_SPEC_FIELD'
        """
        field = (1.346, 0.9)
        rig = RigFacts(fov_deg=field, has_rotator=True)
        g = _mosaic(rig=rig, rows=1, cols=2, angle_mode=ROTATE_TO_PA,
                    pa_deg=0.0)
        t = _target(g)
        assert (t.params["fovX"], t.params["fovY"]) == field
        [entry] = compile_plan(g, "m")["targets"]
        assert (entry["mosaic"]["fov_x"], entry["mosaic"]["fov_y"]) == field
        plan, _ = to_sequence_plan(compile_plan(g, "m"), g, flow_id="field",
                                   when=WHEN)
        west, east = plan.targets
        xi, eta = framing.project(east.ra_hours, east.dec_deg,
                                  west.ra_hours, west.dec_deg)
        # `project` answers in degrees, East and North.
        step = field[0] * (1 - framing.DEFAULT_OVERLAP)
        assert math.hypot(xi, eta) == pytest.approx(step, abs=1e-3)


# ================================================================ the grid

class TestTheGrid:
    def test_the_overlap_defaults_to_the_one_server_constant(self):
        """Spec 2.4: 25%, "one server constant, exported by framing.py".

        RED under mutant "overlap default 15%" (the #/next mosaic.ts value,
        ``overlap = 15`` when none is given), observed verbatim:

            E   assert 15 == (0.25 * 100)
            E    +  where 0.25 = framing.DEFAULT_OVERLAP
        """
        t = _target(_mosaic())
        assert t.params["overlap"] == framing.DEFAULT_OVERLAP * 100 == 25
        assert isinstance(t.params["overlap"], int)

    def test_a_given_overlap_is_written(self):
        assert _target(_mosaic(overlap_pct=12.5)).params["overlap"] == 12.5

    @pytest.mark.parametrize("rows,cols", [(1, 1), (0, 2), (11, 2), (2.5, 2),
                                           (True, 2), (2, "3"),
                                           (float("nan"), 2)])
    def test_a_grid_the_run_would_not_take_is_refused(self, rows, cols):
        with pytest.raises(ValueError):
            _mosaic(rows=rows, cols=cols)

    @pytest.mark.parametrize("overlap", [-5, 55, float("inf"), "25"])
    def test_an_overlap_out_of_bounds_is_refused(self, overlap):
        with pytest.raises(ValueError, match="overlap"):
            _mosaic(overlap_pct=overlap)

    @pytest.mark.parametrize("kind", [KIND_DEEP_SKY, KIND_POOL, KIND_EAA])
    def test_a_grid_or_an_angle_with_another_kind_is_refused(self, kind):
        """Refused rather than dropped (``_checked``'s reason): a 3x2 sent
        with "Deep-sky target" would otherwise come back as one panel looking
        like the mosaic asked for. The rig facts are not answers, and pass."""
        for answer in (dict(rows=2), dict(cols=3), dict(overlap_pct=25),
                       dict(angle_mode=ROTATE_TO_PA), dict(pa_deg=30.0),
                       dict(use_measured=True)):
            with pytest.raises(ValueError, match="belong to the 'Mosaic'"):
                generate(kind, set(), NAME, **answer)
        generate(kind, set(), NAME, rig=WITH_ROTATOR, measured_pa_deg=12.0)


# ============================================================ the loop wire

class TestTheLoopWire:
    @pytest.mark.parametrize("stage", sorted(STAGES))
    def test_the_loop_leaves_the_tail_of_the_lane(self, stage):
        """Spec 1.5 item 5: "Modal DONE, LOOP PANELS, the wizard and every
        generator wire pass from the tail." Found through
        ``compile.lane_tail``, so it is the wire the compile and the doctor
        call the loop: the group is shot "rotate", and M3 and M12 are
        silent. The lane is the block and its stages, no SLEW.

        RED under mutant "no loop wire", observed verbatim:

            E   ValueError: not enough values to unpack (expected 1, got 0)

        (at ``[loop] = loop_wires(g, t)``: there is none).
        """
        g = _mosaic(cycle_plan=STAGES[stage], cycles=3)
        t = _target(g)
        tail = lane_tail(g, t)
        assert tail.type == stage
        [loop] = loop_wires(g, t)
        assert (loop.from_, loop.fromPort, loop.to, loop.toPort) == (
            tail.id, "pass", t.id, "next")
        plan, _ = to_sequence_plan(compile_plan(g, "m"), g, flow_id="loop",
                                   when=WHEN)
        assert [x.mode for x in plan.groups] == ["rotate"]
        assert "slew" not in [n.type for n in g.nodes]

    def test_the_loop_wire_emits_no_rule(self):
        """Spec 1.4 item 3: consumed as structure, never an Instruction."""
        g = _mosaic(opts=frozenset())
        plan, unmapped = to_sequence_plan(compile_plan(g, "m"), g,
                                          flow_id="loop", when=WHEN)
        assert plan.instructions == []
        assert not [u for u in unmapped if "pass" in u["key"]], unmapped


# ======================================================= generate_record (S3-A)

class TestTheRecordCarriesTheMosaicAnswers:
    def test_generate_record_takes_the_mosaic_answers_by_keyword(self):
        """The signature S3-A's route calls: the four positional answers it
        has always passed, then the Mosaic answers and the rig facts by
        keyword. A route that passed them and got a single target back
        would be the dead control the unguided sub length once was."""
        rec = generate_record(KIND_MOSAIC, sorted(DEFAULT_OPTIONS), NAME, None,
                              rows=2, cols=3, overlap_pct=20,
                              angle_mode=CAMERA_FIXED_AT_PA, use_measured=True,
                              measured_pa_deg=37.2, rig=NO_ROTATOR)
        t = _target(rec.graph)
        assert (t.params["rows"], t.params["cols"], t.params["overlap"],
                t.params["angle"], t.params["rotation"]) == (
            2, 3, 20, CAMERA_FIXED_AT_PA, 37.2)
        assert rec.name == NAME and rec.folder == "My flows"

    def test_an_unknown_keyword_is_an_error_not_a_dropped_answer(self):
        with pytest.raises(TypeError):
            generate_record(KIND_MOSAIC, (), NAME, rowz=2)


# ============================================================ the golden 2x2

GOLDEN = Path(__file__).parent / "fixtures" / "flow_plan_golden" / \
    "mosaic_2x2.json"

#: The golden's answers. The PA is this test's input, not a default.
GOLDEN_RIG = RigFacts(fov_deg=FIELD, fov_from="profile Refractor, matched "
                      "2026-09-23", has_rotator=True)
GOLDEN_FLOW_ID = "golden-2x2"


def _golden_now() -> dict:
    """The 2x2 as the code makes it now: the wizard's answer for NGC 7331,
    2 x 2 at the default overlap, Rotate to PA 30, on a rig with a rotator
    and the spec's 0.9 x 0.6 deg field; its compile entry, and its plan with
    the flow's deterministic ids (the rules' ids are minted fresh, so they
    are blanked)."""
    g = generate(KIND_MOSAIC, DEFAULT_OPTIONS, NAME, rows=2, cols=2,
                 angle_mode=ROTATE_TO_PA, pa_deg=30.0, rig=GOLDEN_RIG)
    compiled = compile_plan(g, "NGC 7331 2x2")
    plan, unmapped = to_sequence_plan(compiled, g, flow_id=GOLDEN_FLOW_ID,
                                      when=WHEN)
    dumped = plan.model_dump(mode="json")
    for ins in dumped.get("instructions", []):
        ins["id"] = ""
    [entry] = compiled["targets"]
    return {"compile": entry, "plan": dumped,
            "unmapped": sorted(u["key"] for u in unmapped)}


def _same(want, got, path="$") -> list[str]:
    """Where two dumps differ, floats compared to 1e-9 (the panel centres
    come out of trigonometry, whose last bit a platform's libm may round
    differently; 1e-9 of a degree is far below anything that points)."""
    if isinstance(want, float) or isinstance(got, float):
        ok = (isinstance(want, (int, float)) and isinstance(got, (int, float))
              and not isinstance(want, bool) and not isinstance(got, bool)
              and math.isclose(want, got, rel_tol=0, abs_tol=1e-9))
        return [] if ok else [f"{path}: {want!r} != {got!r}"]
    if isinstance(want, dict) and isinstance(got, dict):
        out = [f"{path}.{k}: missing" for k in want if k not in got]
        out += [f"{path}.{k}: unexpected" for k in got if k not in want]
        for k in want:
            if k in got:
                out += _same(want[k], got[k], f"{path}.{k}")
        return out
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return [f"{path}: {len(want)} items != {len(got)}"]
        out = []
        for i, (a, b) in enumerate(zip(want, got)):
            out += _same(a, b, f"{path}[{i}]")
        return out
    return [] if want == got else [f"{path}: {want!r} != {got!r}"]


class TestTheGolden2x2:
    """Spec S3 tests: "A golden 2x2 compile and expansion." Pinned to the
    fixture, and BOUNDED from the other side by facts that do not come from
    it, so a regenerated fixture cannot wave a changed layout through."""

    def test_the_2x2_compiles_and_expands_as_pinned(self):
        """RED under mutant "overlap default 15%", observed verbatim (the
        fixture as pinned, the code mutated):

            E   AssertionError: $.compile.mosaic.overlap: 25 != 15
            E     $.plan.groups[0].angle_tolerance_deg: 6.259795142249691 !=
                3.253214956587294
            E     $.plan.groups[0].geometry.key: '66c8c2910342a721' !=
                '23126c1e8708b826'
            E     $.plan.groups[0].geometry.overlap: 0.25 != 0.15

        ("the wizard reads the missing-key defaults", "SLEW left in the
        lane", "no loop wire" and "the card formatters" turn it red too).

        RE-PINNED IN BACKLOG WP-09 (#191, 2026-09-30): each of the four
        panels' ``schedule.twilight_deg`` is -18 (its DUSK WINDOW's own
        "Astro dusk" Sun altitude, ``flows.compile._dusk_schedule``), a
        field the golden predates. Regenerated from the same code path.

        RE-PINNED AGAIN IN BACKLOG WP-34 (#195, 2026-09-30): ``plan`` now
        carries ``resume_across_nights: false`` (``SequencePlan``'s new
        field; #195: "Single night" means auto-resume does not arm across
        nights, and the wizard's DUSK WINDOW has no explicit ``repeat``, so
        it defaults to "Single night"), observed as the only diff:

            E   AssertionError: $.plan.resume_across_nights: unexpected

        Regenerated from the same code path, with no other change.
        """
        want = json.loads(GOLDEN.read_text(encoding="utf-8"))
        diffs = _same(want, _golden_now())
        assert not diffs, "\n".join(diffs[:8])

    def test_the_golden_is_the_layout_compute_mosaic_makes(self):
        """The facts the fixture must hold, from framing itself: four panels
        named "NGC 7331 r-c" in the snake order 1-1 1-2 2-2 2-1, each at the
        centre ``compute_mosaic`` gives the block's spec, each commanded to
        PA 30, all four in one rotating group laid out at 30 whose angle
        tolerance is ``framing.angle_tolerance_deg`` of that spec."""
        got = _golden_now()
        entry, plan = got["compile"], got["plan"]
        assert (entry["angle"], entry["loop"], entry["count_mode"]) == (
            "rotate", True, "accepted")
        m = entry["mosaic"]
        assert (m["rows"], m["cols"], m["overlap"], m["fov_x"], m["fov_y"]) \
            == (2, 2, 25, 0.9, 0.6)
        spec = {"ra_hours": parse_ra(entry["ra"]),
                "dec_deg": parse_dec(entry["dec"]), "rows": 2, "cols": 2,
                "overlap": 0.25, "rotation_deg": 30.0, "fov_x_deg": 0.9,
                "fov_y_deg": 0.6}
        panels = framing.compute_mosaic(spec)["panels"]
        [group] = plan["groups"]
        members = [t for t in plan["targets"]
                   if t["mosaic_group"] == group["id"]]
        assert [t["name"] for t in members] == [
            f"NGC 7331 {p['row'] + 1}-{p['col'] + 1}" for p in panels] == [
            "NGC 7331 1-1", "NGC 7331 1-2", "NGC 7331 2-2", "NGC 7331 2-1"]
        for t, p in zip(members, panels):
            assert math.isclose(t["ra_hours"], p["ra_hours"], abs_tol=1e-9)
            assert math.isclose(t["dec_deg"], p["dec_deg"], abs_tol=1e-9)
            assert t["rotation_deg"] == 30.0 and t["acquisition"] == "cycle"
        assert (group["mode"], group["rotate"], group["pa_deg"]) == (
            "rotate", True, 30.0)
        assert math.isclose(group["angle_tolerance_deg"],
                            framing.angle_tolerance_deg(spec), abs_tol=1e-9)
        assert plan["count_mode"] == "accepted"

    def test_the_golden_centre_is_the_catalogues(self):
        """The block was given a name, and its coordinates are the
        catalogue's row for it (#190), written to 0.1 s and 1 arcsec."""
        hit = tonight.resolve_target(NAME)
        entry = _golden_now()["compile"]
        assert abs(parse_ra(entry["ra"]) - hit.ra_hours) < 0.1 / 3600
        assert abs(parse_dec(entry["dec"]) - hit.dec_deg) < 1.0 / 3600
