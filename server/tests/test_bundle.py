"""PRO-10 stacking-bundle tests: additive FrameRecord fields + `_frame_altitude`
+ the pure bundle core (weighting / grouping / group-normalization / serializers
/ injection-safe build script / PRO-1 library adapter) + the zip route.

Pure-core tests need no filesystem (``is_local`` is injected); the route test
uses an isolated app with CAPTURE_DIR pointed at tmp (mirrors
test_calibration_api.py). Coords/paths are synthetic (privacy)."""
from __future__ import annotations

import csv
import io
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.calibration.matcher import MasterRecord
from astrodeck.sequence.report import FrameRecord, SessionReport
from astrodeck.sequence.engine import _frame_altitude
from astrodeck.sequence.bundle import (HFR_TO_FWHM_K, CalibKey,
                                       CalibrationLibraryAdapter,
                                       NullMasterLibrary, build_bundle,
                                       bundle_materialize_plan, build_script,
                                       bundle_summary, fwhm_from_hfr,
                                       manifest_json, readme_text, sub_weight,
                                       weights_csv)


def _rep(frames):
    return SessionReport(id="r1", plan_name="P", frames=frames)


# ------------------------------------------------------------ Task A: fields

def test_frame_record_carries_new_fields_and_defaults_none():
    fr = FrameRecord(ts=1.0, target="M42", filter="Ha", exposure_s=300.0,
                     gain=100, offset=30, binning=1, ecc=0.12, altitude_deg=61.5)
    d = fr.model_dump()
    assert d["gain"] == 100 and d["binning"] == 1
    assert d["ecc"] == 0.12 and d["altitude_deg"] == 61.5
    # backward-compat: an old record without the new keys loads with None
    old = FrameRecord(ts=2.0, target="M31", exposure_s=60.0)
    assert old.gain is None and old.ecc is None and old.altitude_deg is None
    assert old.offset is None and old.binning is None


def test_old_report_dict_without_new_keys_loads_unchanged():
    # A persisted report dict from before PRO-10 (no gain/offset/binning/ecc/
    # altitude keys on its frames) must still deserialize with None defaults.
    raw = {"id": "legacy", "plan_name": "old",
           "frames": [{"ts": 1.0, "target": "M42", "filter": "Ha",
                       "frame_type": "Light", "exposure_s": 120.0,
                       "accepted": True, "hfr": 2.1, "saved_path": "/cap/x.fits"}]}
    rep = SessionReport(**raw)
    f = rep.frames[0]
    assert f.gain is None and f.offset is None and f.binning is None
    assert f.ecc is None and f.altitude_deg is None
    assert f.hfr == 2.1 and f.saved_path == "/cap/x.fits"


# ---------------------------------------------------- Task B: _frame_altitude

def test_frame_altitude_uses_site_and_returns_none_on_bad_input():
    site = {"latitude": 34.0, "longitude": -118.0}   # neutral, NOT the backyard
    class T:
        ra_hours = 5.5
        dec_deg = -5.4
    alt = _frame_altitude(T(), site, 1_700_000_000.0)
    assert alt is None or -90.0 <= alt <= 90.0
    assert _frame_altitude(None, site, 1.0) is None            # defensive
    assert _frame_altitude(T(), {}, 1.0) is None               # missing lat/lon


def test_frame_altitude_default_site_still_returns_a_number():
    # lat/lon default 0.0 -> altaz still returns a real altitude; not special-cased.
    class T:
        ra_hours = 12.0
        dec_deg = 0.0
    alt = _frame_altitude(T(), {"latitude": 0.0, "longitude": 0.0}, 1_700_000_000.0)
    assert alt is not None and -90.0 <= alt <= 90.0


# --------------------------------------------------------- Task 1: weighting

def test_sub_weight_best_is_one_and_missing_metrics_ignored():
    # single metric present, this sub is group-best -> 1.0
    assert sub_weight(2.0, None, None, min_hfr=2.0, min_rms=None) == 1.0
    # worse HFR/ecc/rms than group min -> < 1.0, monotone
    w_good = sub_weight(2.0, 0.10, 1.0, min_hfr=2.0, min_rms=1.0)
    w_bad = sub_weight(4.0, 0.40, 3.0, min_hfr=2.0, min_rms=1.0)
    assert 0.0 < w_bad < w_good <= 1.0
    # no metrics at all -> neutral 1.0 (never penalize an un-measured sub to 0)
    assert sub_weight(None, None, None, min_hfr=None, min_rms=None) == 1.0


def test_sub_weight_altitude_is_optin_and_off_by_default():
    import math
    # OFF (default): altitude is ignored -> byte-identical to the v1 score.
    base = sub_weight(2.0, None, 1.0, min_hfr=2.0, min_rms=1.0)
    assert sub_weight(2.0, None, 1.0, min_hfr=2.0, min_rms=1.0,
                      altitude_deg=30.0) == base
    # ON: a sin(alt) sub-score joins the mean (s_hfr=1, s_rms=1, s_alt=sin30=0.5).
    on = sub_weight(2.0, None, 1.0, min_hfr=2.0, min_rms=1.0,
                    altitude_deg=30.0, weight_altitude=True)
    assert on == pytest.approx((1.0 + 1.0 + math.sin(math.radians(30.0))) / 3)
    # a higher sub outscores a lower one under altitude weighting.
    hi = sub_weight(None, None, None, min_hfr=None, min_rms=None,
                    altitude_deg=80.0, weight_altitude=True)
    lo = sub_weight(None, None, None, min_hfr=None, min_rms=None,
                    altitude_deg=20.0, weight_altitude=True)
    assert hi > lo


def test_build_bundle_weight_altitude_reweights_within_group():
    frames = [
        FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=2.0, altitude_deg=80.0, accepted=True,
                    saved_path="/cap/a.fits"),
        FrameRecord(ts=2, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=2.0, altitude_deg=20.0, accepted=True,
                    saved_path="/cap/b.fits"),
    ]
    # OFF (default): equal HFR -> equal weight (both normalize to 1.0).
    off = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)
    w_off = [l.weight for l in off.groups[0].lights]
    assert w_off[0] == w_off[1] == 1.0 and off.weight_altitude is False
    # ON: the 80-deg sub is the group best; the 20-deg sub is down-weighted.
    on = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True,
                      weight_altitude=True)
    w_on = [l.weight for l in on.groups[0].lights]
    assert on.weight_altitude is True
    assert w_on[0] == 1.0 and w_on[1] < 1.0


def test_bundle_summary_and_manifest_carry_weight_altitude():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          hfr=2.0, altitude_deg=50.0, accepted=True,
                          saved_path="/cap/a.fits")]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True,
                     weight_altitude=True)
    assert bundle_summary(b)["weight_altitude"] is True
    assert manifest_json(b)["weight_altitude"] is True


# ------------------------------------------ Task 1: build_bundle grouping/etc

def test_build_bundle_groups_selects_locals_matches_masters():
    frames = [
        FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=2.0, ecc=0.1, guide_rms_total=0.5,
                    saved_path="/cap/a.fits", accepted=True),
        FrameRecord(ts=2, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=4.0, ecc=0.4, guide_rms_total=1.0,
                    saved_path="/cap/b.fits", accepted=True),
        # different exposure -> its own group
        FrameRecord(ts=3, target="M42", filter="Ha", exposure_s=120, gain=100,
                    binning=1, hfr=2.5, saved_path="/cap/c.fits", accepted=True),
        # a Dark (not a light) -> excluded from groups
        FrameRecord(ts=4, target="M42", frame_type="Dark", exposure_s=300,
                    saved_path="/cap/d.fits", accepted=True),
        # no saved_path -> excluded
        FrameRecord(ts=5, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=3.0, accepted=True),
    ]

    class Lib:
        def match(self, key: CalibKey):
            return "/masters/dark.fits" if key.frame_type == "Dark" else None

    b = build_bundle(_rep(frames), Lib(), is_local=lambda p: p.startswith("/cap/"))
    assert len(b.groups) == 2                       # (Ha,300,g100,bin1) and (Ha,120,…)
    g = next(g for g in b.groups if g.exposure_s == 300)
    assert len(g.lights) == 2                       # d(dark) + e(no path) excluded
    assert g.dir == "M42/Ha/300s_g100_bin1"
    best = max(g.lights, key=lambda l: l.weight)
    assert best.hfr == 2.0 and best.weight == 1.0   # group-normalized best
    assert g.masters == {"dark": "masters/masterDark.fits"}
    assert set(g.missing_masters) == {"Flat", "Bias"}
    # the worse sub is normalized below the best but strictly positive
    worst = min(g.lights, key=lambda l: l.weight)
    assert 0.0 < worst.weight < 1.0


def test_build_bundle_null_library_warns_and_still_groups():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=2.0, saved_path="/cap/a.fits")]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)
    assert len(b.groups) == 1
    assert any("master" in w.lower() for w in b.warnings)


def test_build_bundle_empty_report_is_empty_but_valid():
    b = build_bundle(_rep([]), NullMasterLibrary(), is_local=lambda p: True)
    assert b.groups == ()
    # serializers stay valid over an empty bundle (empty-but-valid bundle)
    assert manifest_json(b)["groups"] == []
    assert bundle_summary(b)["groups"] == []
    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    assert len(rows) == 1                            # header only
    assert "no local light subs" in readme_text(b).lower()


def test_build_bundle_group_normalization_scales_best_to_one():
    # Neither sub is perfect on every metric, but the group's best must be 1.0.
    frames = [
        FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=3.0, ecc=0.2, guide_rms_total=0.8,
                    saved_path="/cap/a.fits", accepted=True),
        FrameRecord(ts=2, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=3.5, ecc=0.3, guide_rms_total=1.2,
                    saved_path="/cap/b.fits", accepted=True),
    ]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)
    g = b.groups[0]
    assert max(l.weight for l in g.lights) == 1.0


# -------------------------------------------------------- Task 2: serializers

def test_serializers_shapes():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=2.0, ecc=0.1,
                          guide_rms_total=0.5, altitude_deg=61.0,
                          saved_path="/cap/a.fits", accepted=True)]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)

    m = manifest_json(b)
    assert m["report_id"] == "r1" and m["layout"] == "grouped"
    assert m["groups"][0]["lights"][0]["dest"] == "M42/Ha/300s_g100_bin1/lights/a.fits"

    s = bundle_summary(b)
    assert s["groups"][0]["light_count"] == 1
    assert s["groups"][0]["masters"] == {"dark": False, "flat": False, "bias": False}
    assert "lights" not in s["groups"][0]            # slim: no per-light rows

    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    assert rows[0][:3] == ["target", "filter", "exposure_s"]
    assert "weight" in rows[0] and len(rows) == 2     # header + 1 light

    assert "300s_g100_bin1" in readme_text(b)         # references the layout


def test_weights_csv_blanks_none_metrics():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, saved_path="/cap/a.fits")]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)
    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    cols = rows[0]
    row = dict(zip(cols, rows[1]))
    assert row["hfr"] == "" and row["ecc"] == "" and row["altitude_deg"] == ""
    assert row["weight"] == "1.0"                     # neutral for an un-measured sub
    # PRO-10 (c): fwhm_est is blank when there is no HFR to estimate it FROM —
    # we never invent a sharpness number for an un-measured sub.
    assert row["fwhm_est"] == ""
    assert row["keep"] == "True"                      # no threshold -> everything kept

    # ...and it is exactly k*hfr when hfr IS measured, in the CSV and manifest.
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=1.8, saved_path="/cap/a.fits")]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)
    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    row = dict(zip(rows[0], rows[1]))
    assert float(row["fwhm_est"]) == pytest.approx(1.8 * HFR_TO_FWHM_K)
    assert fwhm_from_hfr(None) is None
    light = manifest_json(b)["groups"][0]["lights"][0]
    assert light["fwhm_est"] == pytest.approx(3.6)
    # The manifest says IN BAND that this is estimated, not a measured PSF FWHM.
    assert "ESTIMATED" in manifest_json(b)["fwhm_est_note"]
    assert "fwhm_est" in readme_text(b) and "hfr" in readme_text(b)


# -------------------------------------------------------- Task 3: build_script

def test_build_script_quotes_paths_safely():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=2.0,
                          saved_path="/cap/a b;rm -rf ~.fits", accepted=True)]

    class Lib:
        def match(self, key):
            return "/masters/d'q.fits" if key.frame_type == "Dark" else None

    b = build_bundle(_rep(frames), Lib(), is_local=lambda p: True)

    sh = build_script(b, "sh")
    assert sh.startswith("#!/bin/sh")
    assert "'/cap/a b;rm -rf ~.fits'" in sh          # shlex.quote'd, not raw
    assert "rm -rf ~.fits\n" not in sh               # never becomes a command

    ps = build_script(b, "ps1")
    assert "Copy-Item -LiteralPath '/masters/d''q.fits'" in ps   # '' escapes '
    assert "mkdir" in ps.lower() or "New-Item" in ps


def test_build_script_neutralizes_adversarial_names():
    # A pile of shell/PowerShell metacharacters + an embedded newline in one
    # filename must never break out of the quoted literal in either script. The
    # real property: the path appears ONLY as its exact quoted/escaped literal.
    import shlex
    evil = "/cap/$(reboot)`id`; & rmdir /s \"x\" 'y'\n.fits"
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, saved_path=evil, accepted=True)]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)

    sh = build_script(b, "sh")
    assert sh.startswith("#!/bin/sh")
    assert shlex.quote(evil) in sh                   # exact shlex-quoted literal
    # the metacharacters never appear as a bare (unquoted) command line
    assert "$(reboot)`id`; & rmdir" not in sh.replace(shlex.quote(evil), "<Q>")

    ps = build_script(b, "ps1")
    # PowerShell single-quoted literal with every ' doubled — nothing else is
    # interpreted inside a single-quoted PS string ($, `, ;, newline all literal).
    assert "'" + evil.replace("'", "''") + "'" in ps
    assert "$(reboot)" not in ps.replace(evil.replace("'", "''"), "<Q>")


# --------------------------------------------- integration seam: PRO-1 adapter

class _FakeLib:
    def __init__(self, masters):
        self._m = masters

    def list_masters(self):
        return list(self._m)


def _mr(**kw):
    base = dict(id="x", frame_type="DARK", exposure_s=300.0, gain=100, offset=30,
                temp_c=-10.0, binning=1, filter="", frame_count=20,
                path="/m/x.fits", built_ts=1.0)
    base.update(kw)
    return MasterRecord(**base)


def test_adapter_binds_calibkey_to_pro1_best_master_and_titlecase_maps():
    lib = _FakeLib([
        _mr(id="d1", frame_type="DARK", path="/m/masterDark.fits"),
        _mr(id="f1", frame_type="FLAT", exposure_s=1.0, filter="Ha",
            path="/m/masterFlat.fits"),
        _mr(id="b1", frame_type="BIAS", exposure_s=0.0, path="/m/masterBias.fits"),
    ])
    adapter = CalibrationLibraryAdapter(lib)

    # title-case "Dark" -> upper "DARK"; exact gain/offset/binning + exp/temp in tol
    dkey = CalibKey(frame_type="Dark", exposure_s=300.0, gain=100, offset=30,
                    binning=1, filter="Ha", temp_c=-10.0)
    assert adapter.match(dkey) == "/m/masterDark.fits"

    # Flat keys on filter -> resolves the matching flat
    fkey = CalibKey(frame_type="Flat", exposure_s=1.0, gain=100, offset=30,
                    binning=1, filter="Ha", temp_c=-10.0)
    assert adapter.match(fkey) == "/m/masterFlat.fits"

    # Bias (best_master has no BIAS predicate) -> our manual exact scan resolves it
    bkey = CalibKey(frame_type="Bias", exposure_s=0.0, gain=100, offset=30,
                    binning=1, filter="", temp_c=-10.0)
    assert adapter.match(bkey) == "/m/masterBias.fits"

    # a non-matching key (gain differs) -> None
    bad = CalibKey(frame_type="Dark", exposure_s=300.0, gain=200, offset=30,
                   binning=1, filter="Ha", temp_c=-10.0)
    assert adapter.match(bad) is None


def test_adapter_uppercase_mapping_is_load_bearing():
    # If the adapter did NOT upper-case the title-case frame_type, best_master's
    # {"DARK":…,"FLAT":…} lookup would miss and Dark would never resolve. Prove
    # the mapping is what makes a title-case key match an upper-case master.
    lib = _FakeLib([_mr(id="d1", frame_type="DARK", path="/m/dk.fits")])
    adapter = CalibrationLibraryAdapter(lib)
    key = CalibKey(frame_type="Dark", exposure_s=300.0, gain=100, offset=30,
                   binning=1, filter="", temp_c=-10.0)
    assert adapter.match(key) == "/m/dk.fits"


def test_adapter_bias_temp_tolerance_and_empty_lib():
    # empty library -> None
    assert CalibrationLibraryAdapter(_FakeLib([])).match(
        CalibKey("Bias", 0.0, 100, 30, 1, "", -10.0)) is None
    # bias temp outside tolerance -> None; nearest within tolerance wins
    lib = _FakeLib([
        _mr(id="cold", frame_type="BIAS", exposure_s=0.0, temp_c=-30.0,
            path="/m/cold.fits"),
        _mr(id="near", frame_type="BIAS", exposure_s=0.0, temp_c=-10.5,
            path="/m/near.fits"),
    ])
    adapter = CalibrationLibraryAdapter(lib)
    key = CalibKey("Bias", 0.0, 100, 30, 1, "", -10.0)
    assert adapter.match(key) == "/m/near.fits"   # -10.5 within 2C, -30 rejected


# ----------------------------------------------------------- Task 4: zip route

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    cap = tmp_path / "captures"
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, cap


def _seed_report(cap):
    from astrodeck.persist import write_json_atomic
    light = cap / "M42" / "light_0001.fits"
    light.parent.mkdir(parents=True, exist_ok=True)
    light.write_bytes(b"FAKEFITS")               # real local file -> is_local True
    rep = SessionReport(id="M42-20260723-010101", plan_name="P",
                        frames=[FrameRecord(ts=1.0, target="M42", filter="Ha",
                                            exposure_s=300.0, gain=100, offset=30,
                                            binning=1, hfr=2.0,
                                            saved_path=str(light), accepted=True)])
    reports = cap / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    write_json_atomic(reports / f"{rep.id}.json", rep.model_dump())
    return rep.id


def test_bundle_routes_preview_and_zip(env):
    c, cap = env
    rid = _seed_report(cap)

    pv = c.get(f"/api/reports/{rid}/bundle")
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert len(body["groups"]) == 1
    assert body["groups"][0]["light_count"] == 1
    # no PRO-1 library on the test hub -> a warning + no matched masters
    assert body["groups"][0]["masters"] == {"dark": False, "flat": False, "bias": False}

    z = c.get(f"/api/reports/{rid}/bundle.zip")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(z.content)).namelist())
    assert {"manifest.json", "weights.csv", "README.txt",
            "build.sh", "build.ps1"} <= names


def test_bundle_routes_404_when_missing(env):
    c, _ = env
    assert c.get("/api/reports/nope/bundle").status_code == 404
    assert c.get("/api/reports/nope/bundle.zip").status_code == 404


# ============================================================================
# PRO-10 enrichments: layout variants / fwhm_est / keep_threshold / materialize
# (design: docs/superpowers/specs/2026-07-24-export-enrich-design.md)
# ============================================================================

def _two_group_bundle(layout="grouped"):
    """Two groups (Ha + OIII) with a Dark master matched for both."""
    frames = [
        FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=2.0, saved_path="/cap/ha_1.fits", accepted=True),
        FrameRecord(ts=2, target="M42", filter="OIII", exposure_s=300, gain=100,
                    binning=1, hfr=2.5, saved_path="/cap/o3_1.fits", accepted=True),
    ]

    class Lib:
        def match(self, key):
            return "/masters/dark.fits" if key.frame_type == "Dark" else None

    return build_bundle(_rep(frames), Lib(), is_local=lambda p: True, layout=layout)


@pytest.mark.parametrize("layout,light_dir,master_dest", [
    ("grouped", "lights", "masters/masterDark.fits"),
    ("siril", "lights", "M42/Ha/300s_g100_bin1/darks/masterDark.fits"),
    ("app", "Light", "M42/Ha/300s_g100_bin1/Dark/masterDark.fits"),
])
def test_layout_variants_shape_dests_and_build_script(layout, light_dir, master_dest):
    """(b) Layout is entirely a function of the relative dest strings — and the
    build script replays exactly those, in both shells."""
    b = _two_group_bundle(layout)
    assert b.layout == layout
    g0 = b.groups[0]
    assert g0.dir == "M42/Ha/300s_g100_bin1"
    assert g0.lights[0].dest == f"M42/Ha/300s_g100_bin1/{light_dir}/ha_1.fits"
    assert g0.masters["dark"] == master_dest
    # grouped shares ONE top-level masters/ across groups; per-group layouts give
    # each group its own copy (each stack's calibration is independent).
    if layout == "grouped":
        assert b.groups[1].masters["dark"] == master_dest
    else:
        assert b.groups[1].masters["dark"].startswith("M42/OIII/")

    # (these paths need no shell quoting, so shlex.quote passes them through)
    sh = build_script(b, "sh")
    assert f"mkdir -p M42/Ha/300s_g100_bin1/{light_dir}" in sh
    assert f"cp -- /cap/ha_1.fits M42/Ha/300s_g100_bin1/{light_dir}/ha_1.fits" in sh
    assert f"cp -- /masters/dark.fits {master_dest}" in sh
    ps1 = build_script(b, "ps1")
    assert f"'M42/Ha/300s_g100_bin1/{light_dir}'" in ps1
    assert f"-Destination '{master_dest}'" in ps1
    # the README's illustrative tree follows the layout too
    assert light_dir in readme_text(b)


@pytest.mark.parametrize("layout", ["grouped", "siril", "app"])
def test_build_sh_never_emits_a_backslash(layout):
    """The bundle's dests are POSIX by construction, so the SCRIPT must be too.

    ``str(Path(dest).parent)`` is os-dependent: on a Windows-hosted AstroDeck it
    yields ``M42\\Ha\\300s...\\darks``, and under ``set -eu`` the emitted build.sh
    would create one literal backslash-named directory then abort at the next
    ``cp`` — taking every remaining group with it. The per-group layouts are what
    exposed this (grouped's master parent is the separator-free 'masters')."""
    b = _two_group_bundle(layout)
    sh = build_script(b, "sh")
    assert "\\" not in sh
    # the mkdir really does precede its cp, with the SAME directory prefix
    for g in b.groups:
        for kind_lc, dest in g.masters.items():
            if g.master_sources.get(kind_lc):
                assert f"mkdir -p {dest.rsplit('/', 1)[0]}" in sh
    assert "\\" not in build_script(b, "ps1")


def test_bad_layout_and_threshold_rejected(env):
    """Pure guard raises ValueError; the routes turn that into 400 (not a 500)."""
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          saved_path="/cap/a.fits")]
    with pytest.raises(ValueError):
        build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True,
                     layout="pixinsight")
    with pytest.raises(ValueError):
        build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True,
                     keep_threshold=1.5)

    c, cap = env
    rid = _seed_report(cap)
    assert c.get(f"/api/reports/{rid}/bundle?layout=nope").status_code == 400
    assert c.get(f"/api/reports/{rid}/bundle.zip?layout=nope").status_code == 400
    assert c.get(f"/api/reports/{rid}/bundle?keep_threshold=2").status_code == 400
    assert c.post(f"/api/reports/{rid}/bundle/materialize?layout=nope").status_code == 400
    # ...and the good ones still work through every surface
    assert c.get(f"/api/reports/{rid}/bundle?layout=siril").status_code == 200
    assert c.get(f"/api/reports/{rid}/bundle.zip?layout=app").status_code == 200


@pytest.mark.parametrize("threshold,expected_keep,expected_kept", [
    (None, [True, True, True], 3),
    (0.5, [True, True, False], 2),      # the 0.4-weight tail gets flagged
    (1.0, [True, False, False], 1),     # only the best sub clears a 1.0 cutoff
])
def test_keep_threshold_flags_tail_but_never_deletes(threshold, expected_keep,
                                                     expected_kept):
    """(d) keep is ADVISORY: it flags the relatively-worst subs of a group and
    NEVER removes one from the bundle, the manifest, or the build script."""
    # hfr 2/4/5 -> normalized weights 1.0 / 0.5 / 0.4 within the one group.
    frames = [FrameRecord(ts=i, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=h,
                          saved_path=f"/cap/a{i}.fits", accepted=True)
              for i, h in enumerate([2.0, 4.0, 5.0])]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True,
                     keep_threshold=threshold)
    assert [l.keep for l in b.groups[0].lights] == expected_keep
    assert b.keep_threshold == threshold
    assert bundle_summary(b)["groups"][0]["kept_count"] == expected_kept
    assert manifest_json(b)["groups"][0]["kept_count"] == expected_kept

    # SAFETY PROPERTY: every sub is still present everywhere, flagged or not.
    assert len(b.groups[0].lights) == 3
    assert len(manifest_json(b)["groups"][0]["lights"]) == 3
    sh = build_script(b, "sh")
    for i in range(3):
        assert f"/cap/a{i}.fits" in sh
    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    assert len(rows) == 4                              # header + all 3 subs
    keep_col = rows[0].index("keep")
    assert [r[keep_col] for r in rows[1:]] == [str(k) for k in expected_keep]


def test_materialize_plan_dests_stay_under_root_and_refuse_escapes(tmp_path):
    """(a)/R1 — the pure planner IS the containment guard the write loop trusts."""
    import dataclasses
    from pathlib import Path

    root = tmp_path / "captures" / "exports" / "M42-1"
    b = _two_group_bundle("siril")
    plan = bundle_materialize_plan(b, root)
    assert len(plan) == 4                              # 2 lights + 2 per-group darks
    for it in plan:
        assert it.refused is None
        assert Path(it.dest).is_relative_to(root)
    assert {it.kind for it in plan} == {"light", "dark"}

    # A hostile target can't escape either — _group_dir sanitizes every component
    # BEFORE the planner ever sees it (belt), and the planner re-checks (braces).
    evil = build_bundle(
        _rep([FrameRecord(ts=1, target="../../../etc", filter="../x",
                          exposure_s=300, saved_path="/cap/a.fits")]),
        NullMasterLibrary(), is_local=lambda p: True)
    evil_plan = bundle_materialize_plan(evil, root)
    assert evil_plan and all(it.refused is None for it in evil_plan)
    for it in evil_plan:
        assert Path(it.dest).is_relative_to(root)

    # And if a dest ever DID escape (a future bug upstream), the planner refuses
    # it rather than handing the write loop an arbitrary-write primitive.
    g = b.groups[0]
    tampered = dataclasses.replace(
        b, groups=(dataclasses.replace(
            g, lights=(dataclasses.replace(g.lights[0], dest="../../pwned.fits"),),
            masters={}, master_sources={}),))
    bad = bundle_materialize_plan(tampered, root)
    assert len(bad) == 1 and bad[0].refused is not None
    assert "escapes" in bad[0].refused


def test_materialize_route_links_then_falls_back_to_copy(env, monkeypatch):
    """(a) Route integration: real files land under captures/exports/<id>/ via
    hardlink, and a filesystem that can't hardlink transparently copies."""
    import os as _os

    c, cap = env
    rid = _seed_report(cap)
    export = cap / "exports" / rid

    r = c.post(f"/api/reports/{rid}/bundle/materialize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["export_dir"] == str(export)
    assert body["failed"] == []
    assert body["linked"] + body["copied"] == 1
    laid = export / "M42" / "Ha" / "300s_g100_bin1" / "lights" / "light_0001.fits"
    assert laid.exists() and laid.read_bytes() == b"FAKEFITS"
    assert "read-only" in body["hardlink_note"]

    # Idempotent: re-materializing the same tree is a no-op, not an error.
    again = c.post(f"/api/reports/{rid}/bundle/materialize")
    assert again.status_code == 200 and again.json()["failed"] == []

    # EXDEV / no-hardlink filesystem -> copy2 fallback (still lands the bytes).
    def _boom(*a, **k):
        raise OSError(18, "EXDEV")

    monkeypatch.setattr(_os, "link", _boom)
    r2 = c.post(f"/api/reports/{rid}/bundle/materialize?layout=app")
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["copied"] == 1 and b2["linked"] == 0 and b2["failed"] == []
    assert b2["bytes_copied"] == len(b"FAKEFITS")
    app_laid = export / "M42" / "Ha" / "300s_g100_bin1" / "Light" / "light_0001.fits"
    assert app_laid.exists()
    # every laid-out file is inside the export root — nothing escaped
    for p in export.rglob("*.fits"):
        assert p.resolve().is_relative_to(export.resolve())


def test_materialize_404_and_no_local_subs(env):
    c, cap = env
    assert c.post("/api/reports/nope/bundle/materialize").status_code == 404
    # a report whose subs are NOT on this box has nothing to materialize: an
    # honest 400 telling the user to run build.sh on the imaging host.
    from astrodeck.persist import write_json_atomic
    rep = SessionReport(id="remote-1", plan_name="P",
                        frames=[FrameRecord(ts=1.0, target="M42", exposure_s=300,
                                            saved_path="Z:/nina/remote.fits")])
    (cap / "reports").mkdir(parents=True, exist_ok=True)
    write_json_atomic(cap / "reports" / "remote-1.json", rep.model_dump())
    r = c.post("/api/reports/remote-1/bundle/materialize")
    assert r.status_code == 400 and "build.sh" in r.json()["detail"]
