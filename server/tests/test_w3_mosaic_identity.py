# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A mosaic is its id, never its label (WP 3.2, #1114: #188 #795 #796 #797).

``naming.mosaic_label`` returns the group's NAME, so two groups that share a
name, and name their panels alike (the Plan names a panel ``<name> r-c``), had
equal labels. The report and the bundle keyed on the label, so their frames
merged into one mosaic row and one stack, and a stacker opened the two as one
smear. A frame record now carries the group's compiled id (``mosaic_id``) and
the panel target's id (``panel_id``) beside the labels, and the report and the
bundle key on the ids. The labels are for display.

WHAT IS PINNED HERE: the report's and the bundle's half (frame records that
carry ids), the frames CSV's columns (held to ``FrameRecord.model_fields`` by
the route itself, not by a literal list), and the legacy path (a report
written before the ids has labels only and groups as it always did).

WHO STAMPS THE IDS (a group member's every frame, a dark included) is the
engine's ``_reporter_record`` through ``_panel_labels``, which this work
package does not own: see the hand-off in the work package's return. Until it
lands every real report carries labels only and is keyed as it always was.

Named mutant: "key mosaics by label instead of id". ``bundle.py`` builds its
group key from ``fr.mosaic`` where it built it from the id, so two same-named
groups merge. Reds ``test_two_same_named_mosaics_build_two_groups``.
"""
from __future__ import annotations

import csv
import inspect
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, create_model

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.sequence.bundle import (NullMasterLibrary, build_bundle,
                                       bundle_summary, externalize_bundle,
                                       manifest_json, readme_text, weights_csv)
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import (FrameRecord, SessionReport,
                                       SessionReporter, TargetBreakdown)

OPERATOR = principal_for_role("operator")


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _light(ts, group, panel, *, accepted=True, ids=True):
    """One light of panel ``panel`` ("r-c") of mosaic ``group`` ("A" or "B").
    Both groups are NAMED "Veil" and name their panels alike: only the ids
    tell them apart."""
    return FrameRecord(
        ts=ts, target=f"Veil {panel}", filter="Ha", frame_type="Light",
        exposure_s=300.0, gain=100, binning=1, hfr=2.0, accepted=accepted,
        saved_path=f"/cap/{group}-{panel}-{ts:g}.fits",
        mosaic="Veil", panel=panel,
        mosaic_id=f"mosaic-{group}" if ids else None,
        panel_id=f"panel-{group}-{panel}" if ids else None)


def _dark(ts, group, panel):
    """A dark a hold shoots on a panel: it names the panel by id but carries
    no display label (the FITS-card rule labels lights only)."""
    return FrameRecord(
        ts=ts, target=f"Veil {panel}", filter="Ha", frame_type="Dark",
        exposure_s=300.0, mosaic_id=f"mosaic-{group}",
        panel_id=f"panel-{group}-{panel}")


def _two_same_named_mosaics():
    return [_light(1, "A", "1-1"), _light(2, "B", "1-1"),
            _light(3, "A", "1-1"), _light(4, "A", "1-2"),
            _light(5, "B", "1-2"), _light(6, "B", "1-2")]


def _bundle(frames, layout="grouped"):
    rep = SessionReport(id="r1", plan_name="P", frames=frames)
    return build_bundle(rep, NullMasterLibrary(), is_local=lambda p: True,
                        layout=layout)


# -------------------------------------------------------------- the frame record

def test_the_ids_default_to_none_on_a_record_written_before_them():
    old = SessionReport(**{
        "id": "legacy", "plan_name": "old",
        "targets": [{"name": "M31", "frames": 3, "rejected": 0,
                     "integration_s": 900.0, "by_filter": []}],
        "frames": [{"ts": 1.0, "target": "M31", "mosaic": "Veil",
                    "panel": "1-1"}]})
    assert (old.frames[0].mosaic_id, old.frames[0].panel_id) == (None, None)
    assert (old.targets[0].mosaic_id, old.targets[0].panel_id) == (None, None)
    assert TargetBreakdown(name="x").model_dump()["mosaic_id"] is None


# -------------------------------------------------------------------- the report

def _rows(report):
    return sorted((t.mosaic_id, t.panel_id, t.name, t.mosaic, t.panel,
                   t.frames, t.rejected) for t in report.targets)


def test_two_same_named_mosaics_are_four_rows_in_the_report():
    r = SessionReporter(SequencePlan(name="Veil"))
    for fr in _two_same_named_mosaics():
        r.record_frame(fr)
    r.record_frame(_light(7, "B", "1-2", accepted=False))
    report = r.finalize("complete")
    assert _rows(report) == [
        ("mosaic-A", "panel-A-1-1", "Veil 1-1", "Veil", "1-1", 2, 0),
        ("mosaic-A", "panel-A-1-2", "Veil 1-2", "Veil", "1-2", 1, 0),
        ("mosaic-B", "panel-B-1-1", "Veil 1-1", "Veil", "1-1", 1, 0),
        ("mosaic-B", "panel-B-1-2", "Veil 1-2", "Veil", "1-2", 2, 1)], (
        "two groups named Veil with the same panel names are two mosaics: "
        "four rows, each with its own counts")
    assert report.frames_captured == 6 and report.frames_rejected == 1


def test_a_dark_joins_the_row_of_its_own_panel_by_id():
    r = SessionReporter(SequencePlan(name="Veil"))
    r.record_frame(_light(1, "A", "1-1"))
    r.record_frame(_light(2, "B", "1-1"))
    r.record_frame(_dark(3, "B", "1-1"))
    report = r.finalize("complete")
    assert len(report.targets) == 2, (
        f"a dark shot on a panel opened a row of its own: "
        f"{[(t.mosaic_id, t.name) for t in report.targets]}")
    b = next(t for t in report.targets if t.mosaic_id == "mosaic-B")
    assert (b.panel_id, b.mosaic, b.panel) == ("panel-B-1-1", "Veil", "1-1")
    assert report.frames_captured == 3


def test_the_ids_survive_a_write_a_read_and_a_resume():
    r = SessionReporter(SequencePlan(name="Veil"))
    for fr in _two_same_named_mosaics():
        r.record_frame(fr)
    r.finalize("incomplete")
    loaded = SessionReporter.load(r.id)
    assert len(loaded.targets) == 4
    assert {(t.mosaic_id, t.panel_id) for t in loaded.targets} == {
        ("mosaic-A", "panel-A-1-1"), ("mosaic-A", "panel-A-1-2"),
        ("mosaic-B", "panel-B-1-1"), ("mosaic-B", "panel-B-1-2")}
    # A crash-resume rebuilds the running totals from the persisted header:
    # one more frame of B's panel 1-1 lands on B's row, not on a row of A's.
    resumed = SessionReporter.attach_existing(r.id)
    resumed.record_frame(_light(8, "B", "1-1"))
    report = resumed.finalize("complete")
    rows = {(t.mosaic_id, t.panel_id): t.frames for t in report.targets}
    assert rows == {("mosaic-A", "panel-A-1-1"): 2,
                    ("mosaic-A", "panel-A-1-2"): 1,
                    ("mosaic-B", "panel-B-1-1"): 2,
                    ("mosaic-B", "panel-B-1-2"): 2}


def test_a_record_with_labels_and_no_ids_groups_as_it_always_did():
    """A report written before the ids has labels only: its rows are keyed by
    target name, as before."""
    r = SessionReporter(SequencePlan(name="Veil"))
    r.record_frame(_light(1, "A", "1-1", ids=False))
    r.record_frame(_light(2, "A", "1-2", ids=False))
    r.record_frame(_light(3, "A", "1-1", ids=False))
    report = r.finalize("complete")
    assert sorted((t.name, t.frames, t.mosaic_id) for t in report.targets) == [
        ("Veil 1-1", 2, None), ("Veil 1-2", 1, None)]


# -------------------------------------------------------------------- the bundle

def test_two_same_named_mosaics_build_two_groups():
    b = _bundle(_two_same_named_mosaics())
    got = sorted((g.mosaic_id, g.panel_id, len(g.lights)) for g in b.groups)
    assert got == [("mosaic-A", "panel-A-1-1", 2),
                   ("mosaic-A", "panel-A-1-2", 1),
                   ("mosaic-B", "panel-B-1-1", 1),
                   ("mosaic-B", "panel-B-1-2", 2)], (
        "two mosaics named Veil, with the same panel names, are two stacks "
        "per panel, not one")


def test_two_same_named_mosaics_get_two_folders_and_distinct_files():
    """The same label gives the same folder prefix, so the second mosaic's is
    told apart: a stacker opens a folder as one stack."""
    b = _bundle(_two_same_named_mosaics())
    dirs = [g.dir for g in b.groups]
    assert len(set(dirs)) == len(dirs), f"two groups share a folder: {dirs}"
    dests = [l.dest for g in b.groups for l in g.lights]
    assert len(set(dests)) == len(dests)
    first = next(g for g in b.groups if g.panel_id == "panel-A-1-1")
    second = next(g for g in b.groups if g.panel_id == "panel-B-1-1")
    assert first.dir == "Veil/Veil 1-1/Ha/300s_g100_bin1"
    assert second.dir == "Veil_2/Veil 1-1/Ha/300s_g100_bin1"


def test_a_mosaic_alone_in_its_folder_keeps_the_plain_folder():
    b = _bundle([_light(1, "A", "1-1"), _light(2, "A", "1-2")])
    assert sorted(g.dir for g in b.groups) == [
        "Veil 1-1/Ha/300s_g100_bin1", "Veil 1-2/Ha/300s_g100_bin1"]


def test_a_bundle_of_labels_without_ids_groups_by_label_as_before():
    frames = [_light(1, "A", "1-1", ids=False), _light(2, "A", "1-1", ids=False)]
    (g,) = _bundle(frames).groups
    assert (g.mosaic, g.panel, g.mosaic_id, g.panel_id) == ("Veil", "1-1",
                                                            None, None)
    assert len(g.lights) == 2


def test_the_serializers_carry_the_ids():
    b = _bundle(_two_same_named_mosaics())
    want = [("mosaic-A", "panel-A-1-1"), ("mosaic-A", "panel-A-1-2"),
            ("mosaic-B", "panel-B-1-1"), ("mosaic-B", "panel-B-1-2")]
    for rows in (manifest_json(b)["groups"], bundle_summary(b)["groups"]):
        assert sorted((r["mosaic_id"], r["panel_id"]) for r in rows) == want
    ext = externalize_bundle(b, lambda p: p.rsplit("/", 1)[-1])
    assert sorted((g.mosaic_id, g.panel_id) for g in ext.groups) == want
    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    by = [dict(zip(rows[0], r)) for r in rows[1:]]
    first = sorted({r["dest"].split("/")[0] for r in by if r["mosaic"]})
    assert first == ["Veil", "Veil_2"], (
        "the weights CSV tells two same-named mosaics apart by dest")
    plain = _bundle([FrameRecord(ts=1, target="M42", frame_type="Light",
                                 saved_path="/cap/a.fits")])
    assert manifest_json(plain)["groups"][0]["mosaic_id"] is None


def test_the_readme_lists_each_mosaic_by_its_own_folder():
    text = readme_text(_bundle(_two_same_named_mosaics()))
    assert "Veil_2/Veil 1-1" in text


# ---------------------------------------------------------------- the frames CSV

@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "captures"
    root.mkdir()
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", root)
    store = config_mod.ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)

    class _Fixed:
        name = "fake"

        async def resolve(self, request):
            return OPERATOR

    set_active_provider(_Fixed())
    with TestClient(app_module.create_app()) as c:
        yield c
    reset_active_provider()


def _install(monkeypatch, frames):
    rep = SessionReport(id="r1", plan_name="P", frames=frames)
    monkeypatch.setattr(app_module.SessionReporter, "load",
                        staticmethod(lambda rid: rep if rid == "r1" else None))


def _csv(client):
    text = client.get("/api/reports/r1/frames.csv").text
    return list(csv.reader(io.StringIO(text)))


def test_the_csv_header_is_every_frame_record_field(client, monkeypatch):
    _install(monkeypatch, _two_same_named_mosaics())
    header = _csv(client)[0]
    assert len(header) == len(set(header)), f"a column twice: {header}"
    assert set(header) == set(FrameRecord.model_fields) | {"ts_utc"}, (
        f"the frames CSV and FrameRecord disagree: "
        f"only in the CSV {sorted(set(header) - set(FrameRecord.model_fields) - {'ts_utc'})}, "
        f"only in the record {sorted(set(FrameRecord.model_fields) - set(header))}")


def test_a_field_added_to_the_record_reaches_the_csv(client, monkeypatch):
    """The check #795 asks for, as a test: a throwaway field on
    ``FrameRecord`` must appear in the header and carry its value, with the
    route unchanged."""
    class Extended(FrameRecord):
        throwaway_w3_field: str | None = "kept"

    monkeypatch.setattr(app_module, "FrameRecord", Extended, raising=False)
    rep = SessionReport.model_construct(
        id="r1", plan_name="P", sky_angles=[],
        frames=[Extended(ts=1.0, target="T")])
    monkeypatch.setattr(app_module.SessionReporter, "load",
                        staticmethod(lambda rid: rep))
    rows = _csv(client)
    assert "throwaway_w3_field" in rows[0], rows[0]
    assert dict(zip(rows[0], rows[1]))["throwaway_w3_field"] == "kept"


def test_the_csv_keeps_the_columns_readers_know_where_they_were(client, monkeypatch):
    """Positional readers: the columns the export has always had keep their
    order, and the fields added since come after them."""
    _install(monkeypatch, _two_same_named_mosaics())
    header = _csv(client)[0]
    assert header[:19] == [
        "ts", "ts_utc", "target", "filter", "frame_type", "exposure_s",
        "gain", "offset", "binning", "accepted", "hfr", "ecc",
        "sensor_temp_c", "guide_rms_total", "altitude_deg", "saved_path",
        "mosaic", "panel", "pointing_unverified"]
    assert header[-2:] == ["mosaic_id", "panel_id"]


def test_the_csv_writes_the_ids(client, monkeypatch):
    _install(monkeypatch, _two_same_named_mosaics())
    rows = _csv(client)
    by = [dict(zip(rows[0], r)) for r in rows[1:]]
    assert sorted({(r["mosaic_id"], r["panel_id"]) for r in by}) == [
        ("mosaic-A", "panel-A-1-1"), ("mosaic-A", "panel-A-1-2"),
        ("mosaic-B", "panel-B-1-1"), ("mosaic-B", "panel-B-1-2")]


def test_the_csv_lead_names_only_fields_the_record_has():
    """A leading column for a field that was removed would write a blank
    column forever: the lead is held to the model."""
    missing = [c for c in app_module._FRAME_CSV_LEAD
               if c != "ts_utc" and c not in FrameRecord.model_fields]
    assert not missing, f"the CSV lead names fields FrameRecord lacks: {missing}"


# ------------------------------------------------- the generated TypeScript mirror

# APPENDED, not inserted first: a script in tools/ must never shadow the
# standard library (the same rule test_credits.py states).
_TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.append(str(_TOOLS))
import gen_report_types as gen  # noqa: E402


def test_the_generated_report_types_are_current():
    assert not gen.stale(), (
        "ui/src/types.report.generated.ts lags the report models: "
        "run python tools/gen_report_types.py")


def test_a_field_added_to_a_report_model_turns_the_mirror_red():
    """#797's check: a server field added without regenerating must fail. The
    model gains a throwaway field and the committed file no longer matches."""
    grown = create_model("FrameRecord", __base__=FrameRecord,
                         throwaway_w3_field=(int, 0))
    models = tuple(grown if m.__name__ == "FrameRecord" else m
                   for m in gen.MODELS)
    old = gen.MODELS
    gen.MODELS = models
    try:
        assert gen.stale(), "a new FrameRecord field left the mirror green"
        assert "throwaway_w3_field: number;" in gen.render()
    finally:
        gen.MODELS = old


def test_every_report_model_is_generated():
    """A model added to report.py and left out of the generator would be a
    mirror nothing checks."""
    import astrodeck.sequence.report as report
    defined = {name for name, obj in vars(report).items()
               if inspect.isclass(obj) and issubclass(obj, BaseModel)
               and obj.__module__ == report.__name__}
    assert {m.__name__ for m in gen.MODELS} == defined


def test_the_withheld_field_is_the_one_the_route_withholds():
    from astrodeck.api.redact import _REPORT_ALTITUDE_KEY
    assert gen.WITHHELD == {"FrameRecord": (_REPORT_ALTITUDE_KEY,)}
    assert "  altitude_deg?: number | null;" in gen.render()


def test_a_type_the_generator_cannot_spell_is_refused_not_guessed():
    with pytest.raises(gen.Unmapped):
        gen.ts_type(set[str])


def test_the_file_is_written_utf8_lf_without_a_bom(tmp_path):
    out = tmp_path / "x.generated.ts"
    gen.write(out)
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    assert raw.isascii()
    assert not gen.stale(out)
    out.write_bytes(raw.replace(b"\n", b"\r\n"))
    assert not gen.stale(out), "a CRLF checkout is the same file"


# ------------------------------------------------------ the session list row

def _plan_with_groups(second: bool):
    """Two groups both named "Veil" (a 2x2 and a 1x2) when ``second``, one
    otherwise, plus a single target and a legacy Plan-UI string."""
    from astrodeck.sequence.models import Target, TargetGroup

    def members(gid, rows, cols):
        return [Target(id=f"{gid}-r{r}c{c}", name=f"Veil {r + 1}-{c + 1}",
                       ra_hours=1.0, dec_deg=2.0, mosaic_group=gid,
                       panel_row=r, panel_col=c)
                for r in range(rows) for c in range(cols)]

    groups = [TargetGroup(id="group-a", name="Veil")]
    targets = members("group-a", 2, 2)
    if second:
        groups.append(TargetGroup(id="group-b", name="Veil"))
        targets += members("group-b", 1, 2)
    targets.append(Target(id="solo", name="M31", ra_hours=0.7, dec_deg=41.0))
    return SequencePlan(name="Veil", targets=targets, groups=groups)


def test_the_list_row_summarises_each_mosaic_by_id():
    from astrodeck.sequence.session import mosaics_of

    got = mosaics_of(_plan_with_groups(True))
    assert got == [
        {"id": "group-a", "name": "Veil", "panels": ["1-1", "1-2", "2-1", "2-2"]},
        {"id": "group-b", "name": "Veil", "panels": ["1-1", "1-2"]}], (
        "two groups that share a name are two mosaics in the row")
    assert mosaics_of(SequencePlan(name="x")) == []


def test_the_list_route_row_carries_mosaics_only_when_there_are_some(
        tmp_path, monkeypatch):
    from astrodeck.sequence.session import Session, SessionStore

    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = SessionStore()
    store.save(Session(id="m" * 32, name="Veil", status="complete",
                       plan=_plan_with_groups(True)))
    store.save(Session(id="s" * 32, name="Plain", status="complete",
                       plan=SequencePlan(name="Plain")))
    rows = {r["name"]: r for r in store.list()}
    assert [m["id"] for m in rows["Veil"]["mosaics"]] == ["group-a", "group-b"]
    assert "mosaics" not in rows["Plain"], (
        "a night with no mosaic adds nothing to its row")
