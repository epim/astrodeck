"""GN-04: trailed subs are REJECTED, on the real grading path, by default.

THE DEFECT. Every staircase-trailed sub of 2026-09-06 was accepted at HFR 3.10
and stacked in. The engine has had an eccentricity gate all along
(`_check_quality` against the `max_eccentricity` imaging standard) and it had
never once been consulted, because the standard shipped at 0 = off. A gate
nobody can see is off is not a gate.

WHY TWO STATISTICS ON ONE DIAL. Measured on every full frame still on disk
(the triage spec's "Whole-frame eccentricity" table), no single median ceiling
has margin on both sides: the clean frames top out at median 0.56 and the
mildest staircase - a frame whose guider ran the field away - sits at 0.61.
The DISTRIBUTION separates them. At the shipped 0.65 default the clean frames
put at most 10% of their mid-bright stars above 0.80; that same staircase puts
37%, and the double blob / PE drift / donut put 84-93%.

    rule       clean frames    ceiling    mildest reject
    median     <= 0.56         0.65       0.61 (whole-frame staircase)
    fraction   <= 0.10         0.25       0.26 (whole-frame staircase)

Both are one operator dial: the fraction rule measures against
`max_eccentricity + 0.15` and fires above 25% of the marks.

WHAT THIS FILE GRADES. The eight real 512x512 crops in
`fixtures/ngc604_20260906`, each through the SAME helper the hub grades a live
sub with (`grade_frame`) and the SAME gate the engine runs
(`SequenceEngine._check_quality`) - no re-implementation of either, because a
gate tested against a copy of itself is the shape that let this defect ship.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrodeck.config import CONFIG_SCHEMA, AppConfig, ConfigStore, StandardsConfig
from astrodeck.events import bus
from astrodeck.flows.doctor import check as flow_doctor
from astrodeck.flows.models import FlowGraph, FlowNode
from astrodeck.hub import Hub
from astrodeck.imaging.stars import grade_frame
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.policy import (ECC_ELONGATED_FRACTION,
                                       ECC_ELONGATED_MARGIN,
                                       ECC_MIN_MARKS_FOR_FRACTION,
                                       resolve_policy)

FIXTURES = Path(__file__).parent / "fixtures" / "ngc604_20260906"

#: fixture stem -> (header VERDICT, measured median ecc, measured fraction of
#: ecc-carrying marks above 0.80, accepted at the 0.65 default?). Measured
#: 2026-09-06 with the real path below; the medians reproduce the triage spec's
#: baseline table exactly.
EXPECTED: dict[str, tuple[str, float, float, bool]] = {
    "clean_R60":      ("clean",     0.440, 0.000, True),
    "jump_R60":       ("trailed",   0.488, 0.063, True),
    "tail_Ha300":     ("tailed",    0.558, 0.250, True),
    "m33core_G60":    ("galaxy",    0.739, 0.478, False),
    "donut_L60":      ("defocused", 0.764, 0.333, False),
    "doubleblob_L60": ("trailed",   0.805, 0.524, False),
    "pedrift_L60":    ("trailed",   0.811, 0.722, False),
    "staircase_G60":  ("trailed",   0.859, 0.553, False),
}


def _fixture(stem: str) -> tuple[np.ndarray, str]:
    with fits.open(FIXTURES / f"{stem}.fits.gz") as hdul:
        return np.asarray(hdul[0].data), str(hdul[0].header.get("VERDICT") or "")


def _graded(stem: str) -> dict:
    """The frame through the REAL grader the hub feeds the engine from."""
    data, _verdict = _fixture(stem)
    return grade_frame(data, full_well=None)


def _engine(max_eccentricity: float | None = None) -> SequenceEngine:
    """A bare engine whose policy carries the rig's DEFAULT standards.

    `plan = ...` re-derives the policy (see the plan setter), and a plan field
    left None inherits the standard - which is the layering this row ships.
    """
    eng = SequenceEngine(Hub())
    eng.plan = SequencePlan(name="p", targets=[],
                            max_eccentricity=max_eccentricity)
    return eng


def _said(q) -> list[str]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append(str(ev.data.get("message", "")))
    return out


def _frac_above(info: dict, threshold: float) -> float:
    eccs = [m["ecc"] for m in info.get("star_list") or [] if "ecc" in m]
    return (sum(1 for e in eccs if e > threshold) / len(eccs)) if eccs else 0.0


# ------------------------------------------------ the fixtures, the real path

@pytest.mark.parametrize("stem", sorted(EXPECTED))
def test_the_default_gate_grades_every_real_fixture_as_a_human_did(stem):
    """Eight real subs from the NGC 604 night, graded and gated by the code the
    run uses. The four trailed frames, the donut and the galaxy crop of a
    staircase are REJECTED; the clean sub, the single jump and the faint tail
    are accepted (the last two knowingly - a jump-then-settle is not an
    eccentricity signature, and GN-02/GN-03 stop that class at the source)."""
    verdict, median, frac, accept = EXPECTED[stem]
    data, hdr_verdict = _fixture(stem)
    assert hdr_verdict == verdict, "fixture header changed under the test"

    info = grade_frame(data, full_well=None)
    assert info["ecc"] == pytest.approx(median, abs=0.01), (
        f"{stem}: the grader's median eccentricity moved")
    assert _frac_above(info, 0.80) == pytest.approx(frac, abs=0.01), (
        f"{stem}: the elongated fraction moved")

    q = bus.subscribe()
    try:
        got = _engine()._check_quality(info)
        said = _said(q)
    finally:
        bus.unsubscribe(q)

    assert got is accept, (
        f"{stem} ({verdict}): median {info['ecc']:.3f}, "
        f"{frac:.0%} of stars above 0.80 - expected "
        f"{'ACCEPT' if accept else 'REJECT'} at the 0.65 default")
    if not accept:
        warnings = [s for s in said if "eccentricity" in s]
        assert warnings, f"{stem} was rejected with nothing said about why"
        assert "median" in warnings[0], (
            f"the warning must name the rule that fired: {warnings[0]!r}")
        assert f"{info['ecc']:.2f}" in warnings[0], (
            f"the warning must carry the number: {warnings[0]!r}")


def test_the_default_standard_is_the_measured_ceiling():
    """0.65, and it reaches a bare plan through the standards layer."""
    assert AppConfig().standards.max_eccentricity == 0.65
    assert _engine()._policy.max_eccentricity == 0.65
    assert _engine()._policy.sources["max_eccentricity"] == "rig"


# ------------------------------------------------ the elongated-fraction rule

def _synthetic(median: float, n: int, above: int, hot: float = 0.85) -> dict:
    """`info` as the grader emits it: a median plus the marks behind it."""
    marks = [{"x": 1.0, "y": 1.0, "hfr": 2.0, "ecc": hot} for _ in range(above)]
    marks += [{"x": 1.0, "y": 1.0, "hfr": 2.0, "ecc": 0.40}
              for _ in range(n - above)]
    return {"ecc": median, "star_list": marks}


def test_a_round_median_with_an_elongated_tail_is_still_rejected():
    """THE ROW'S WHOLE POINT. The mildest staircase reads median 0.61 - under
    any ceiling that keeps the clean frames - while 37% of its stars are
    smeared. The fraction rule is what catches it."""
    q = bus.subscribe()
    try:
        got = _engine()._check_quality(_synthetic(0.55, 20, 8))
        said = _said(q)
    finally:
        bus.unsubscribe(q)
    assert got is False, "40% of the stars above 0.80 must reject the frame"
    warn = next(s for s in said if "eccentricity" in s)
    assert "40%" in warn and "0.80" in warn, warn
    assert "20 stars" in warn, warn
    assert "0.55" in warn, "the median belongs in the sentence too: " + warn


def test_a_few_elongated_stars_do_not_reject_a_round_frame():
    """15% is under the 25% limit, and a clean frame reaching 10% is normal."""
    assert _engine()._check_quality(_synthetic(0.55, 20, 3)) is True


def test_the_fraction_rule_abstains_on_too_few_stars():
    """One star of seven is 14%; four is 57%. A fraction over a handful of
    marks is noise, so below the floor only the median rule runs."""
    info = _synthetic(0.55, 7, 4)
    assert len([m for m in info["star_list"] if "ecc" in m]) < ECC_MIN_MARKS_FOR_FRACTION
    assert _engine()._check_quality(info) is True
    # the same shape with one more star DOES fire, so the floor is what saved it
    assert _engine()._check_quality(_synthetic(0.55, 8, 5)) is False


def test_the_two_rules_hang_off_one_dial():
    """Raising the ceiling raises the elongated threshold with it - the
    operator has one number to turn, not two."""
    assert ECC_ELONGATED_FRACTION == 0.25
    assert ECC_ELONGATED_MARGIN == 0.15
    loose = _engine(0.80)          # elongated threshold becomes 0.95
    assert loose._check_quality(_synthetic(0.70, 20, 20, hot=0.90)) is True
    tight = _engine(0.50)          # elongated threshold becomes 0.65
    assert tight._check_quality(_synthetic(0.45, 20, 8, hot=0.70)) is False


def test_zero_is_the_operator_turning_it_off():
    """A deliberate 0 disarms BOTH rules - it is the only way to shoot a night
    the gate would refuse, and a companion rule that ignored it would be a
    setting that does not work."""
    off = _engine(0.0)
    for stem in EXPECTED:
        assert off._check_quality(_graded(stem)) is True, stem
    assert off._check_quality(_synthetic(0.99, 20, 20)) is True


def test_calibration_frames_stay_exempt():
    """Darks and flats have no stars; grading them on roundness is nonsense."""
    eng = _engine()
    assert eng._check_quality(_graded("staircase_G60"), calibration=True) is True
    assert eng._check_quality(_synthetic(0.55, 20, 8), calibration=True) is True


# ------------------------------------------------------------ the migration

def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def test_a_stored_zero_from_before_the_default_is_raised_once(tmp_path):
    """"0 stays off only when the operator SET it". Every rig on disk carries a
    0 that was the built-in, not a decision, and leaving those alone would ship
    the fix to nobody. The schema stamp is the only thing that can tell the two
    apart, so the raise happens exactly once, on the 1 -> 2 migration."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 1, "version": 7,
                  "standards": {"max_eccentricity": 0.0, "min_stars": 40}})
    cfg = ConfigStore(path=path).cfg()
    assert cfg.standards.max_eccentricity == 0.65
    assert cfg.standards.min_stars == 40, "the migration touched a neighbour"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == CONFIG_SCHEMA == 2
    assert on_disk["standards"]["max_eccentricity"] == 0.65, (
        "the raise has to be persisted, or every boot re-migrates for ever")


def test_a_zero_written_at_the_new_schema_is_the_operators_and_is_left_alone(tmp_path):
    """After the migration, 0 means somebody typed 0."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 2, "version": 7,
                  "standards": {"max_eccentricity": 0.0}})
    assert ConfigStore(path=path).cfg().standards.max_eccentricity == 0.0


def test_the_migration_does_not_overwrite_a_real_choice(tmp_path):
    """A schema-1 file with a NON-zero ceiling was an operator who found the
    setting. Their number survives."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 1, "version": 7,
                  "standards": {"max_eccentricity": 0.9}})
    assert ConfigStore(path=path).cfg().standards.max_eccentricity == 0.9


def test_an_unstamped_file_migrates_too(tmp_path):
    """A config written before the marker reads back as version 0, which is
    also below 2."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"version": 3, "standards": {"max_eccentricity": 0.0}})
    assert ConfigStore(path=path).cfg().standards.max_eccentricity == 0.65


# ---------------------------------------------------- a compiled flow inherits

def test_a_compiled_flow_runs_under_the_new_standard():
    """The flow compiler says nothing about frame quality, so the run has to
    pick the ceiling up from the rig - the layering #239 stage A built. A flow
    that silently ran with the gate off is exactly how last night happened."""
    from astrodeck.flows.compile import compile_plan
    from astrodeck.flows.examples import examples
    from astrodeck.flows.to_plan import to_sequence_plan

    m16 = next(e for e in examples() if e.id == "example-m16")
    plan, _unmapped = to_sequence_plan(compile_plan(m16.graph, m16.name), m16.graph)
    assert plan.max_eccentricity is None, "the compiler must leave it to the rig"

    p = resolve_policy(plan, AppConfig())
    assert p.max_eccentricity == 0.65
    assert p.sources["max_eccentricity"] == "rig"


# --------------------------------------------------------------- the doctor

def _capture_graph() -> FlowGraph:
    from astrodeck.flows.examples import examples
    return next(e for e in examples() if e.id == "example-m16").graph


def _ecc_issues(issues) -> list[str]:
    return [i.text for i in issues if "eccentricity" in i.text]


def test_the_doctor_says_when_the_gate_is_off():
    graph = _capture_graph()
    off = StandardsConfig(max_eccentricity=0.0)
    said = _ecc_issues(flow_doctor(graph, standards=off))
    assert len(said) == 1, said
    assert "eccentricity rejection is off" in said[0]
    assert "trailed subs will be accepted" in said[0]
    assert all(i.level == "warn" for i in flow_doctor(graph, standards=off)
               if "eccentricity" in i.text)


def test_the_doctor_is_quiet_when_the_gate_is_armed():
    graph = _capture_graph()
    on = StandardsConfig(max_eccentricity=0.65)
    assert _ecc_issues(flow_doctor(graph, standards=on)) == []
    assert [i.to_json() for i in flow_doctor(graph, standards=on)] == \
           [i.to_json() for i in flow_doctor(graph)]


def test_a_graph_with_no_capture_stage_is_not_warned():
    """A calibration-only graph shoots no lights, so roundness is not its
    problem, and a rule that fires on every flow is a rule nobody reads."""
    graph = FlowGraph(nodes=[FlowNode(id="q", type="calib",
                                      params={"flats": "Skip", "quota": 20})],
                      edges=[])
    off = StandardsConfig(max_eccentricity=0.0)
    assert _ecc_issues(flow_doctor(graph, standards=off)) == []


def test_the_old_signature_still_works_and_says_exactly_what_it_did():
    """`check(graph)` is called positionally from the UI-facing route's tests
    and from `test_flows_doctor_agrees_with_the_engine`; the new keyword must
    not change one word of what those see."""
    graph = _capture_graph()
    assert [i.to_json() for i in flow_doctor(graph)] == \
           [i.to_json() for i in flow_doctor(graph, standards=None)]
    assert _ecc_issues(flow_doctor(graph)) == []


def test_the_compile_route_hands_the_doctor_the_rigs_standards(monkeypatch, tmp_path):
    """The rule is worthless if the route never passes the standards in."""
    from fastapi.testclient import TestClient

    import astrodeck.api.app as app_module
    from astrodeck.config import config_store
    from astrodeck.flows.examples import examples

    monkeypatch.setattr(app_module.hub_module, "CAPTURE_DIR", tmp_path)
    cfg = config_store.cfg()
    before = cfg.standards.max_eccentricity
    m16 = next(e for e in examples() if e.id == "example-m16")
    body = {"graph": m16.graph.model_dump(by_alias=True), "name": m16.name}
    try:
        with TestClient(app_module.create_app()) as c:
            cfg.standards.max_eccentricity = 0.0
            r = c.post("/api/flows/compile", json=body)
            assert r.status_code == 200, r.text
            texts = [i["text"] for i in r.json()["issues"]]
            assert any("eccentricity rejection is off" in t for t in texts), texts

            cfg.standards.max_eccentricity = 0.65
            r = c.post("/api/flows/compile", json=body)
            texts = [i["text"] for i in r.json()["issues"]]
            assert not any("eccentricity" in t for t in texts), texts
    finally:
        cfg.standards.max_eccentricity = before


# --------------------------------------------------------------- the route

def test_the_standards_route_serves_the_new_default(monkeypatch, tmp_path):
    """What a rig that never opens Settings is graded against."""
    from fastapi.testclient import TestClient

    import astrodeck.api.app as app_module
    from astrodeck.config import config_store

    monkeypatch.setattr(app_module.hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    with TestClient(app_module.create_app()) as c:
        r = c.get("/api/config")
        assert r.status_code == 200, r.text
        assert r.json()["standards"]["max_eccentricity"] == 0.65


# ----------------------------------------------- the grader the engine reads

def test_grade_frame_is_the_hubs_own_grading_and_carries_the_marks():
    """The gate reads `ecc` AND `star_list`; a grader that produced one without
    the other would silently disable the companion rule."""
    info = _graded("staircase_G60")
    assert set(info) >= {"hfr", "stars", "star_list", "ecc"}
    eccs = [m["ecc"] for m in info["star_list"] if "ecc" in m]
    assert len(eccs) >= ECC_MIN_MARKS_FOR_FRACTION
    assert info["ecc"] == pytest.approx(float(np.median(eccs)), abs=0.001), (
        "`ecc` must be the median of the very marks the fraction rule counts")
