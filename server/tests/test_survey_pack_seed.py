"""Release-eng (UX-07): first-boot seeding of the bundled baseline survey pack."""
import json
from pathlib import Path

import astrodeck.catalog.survey_pack as sp


def _write_pack(root: Path, slug: str = "dss2color", *, order: int = 3) -> Path:
    p = root / slug
    (p / "Norder0" / "Dir0").mkdir(parents=True, exist_ok=True)
    (p / "Norder0" / "Dir0" / "Npix0.jpg").write_bytes(b"\xff\xd8tile")
    (p / "properties").write_text("hips_order = 3\n")
    (p / "pack.json").write_text(json.dumps(
        {"survey": "CDS/P/DSS2/color", "slug": slug, "order": order}))
    return p


def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PACK_ROOT", tmp_path / "persist" / "_survey_pack")
    monkeypatch.setattr(sp, "BUNDLED_PACK_ROOT", tmp_path / "bundled")


def test_seed_copies_bundled_pack_when_absent(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    _write_pack(tmp_path / "bundled")                       # bundled baseline present
    assert sp.pack_present("CDS/P/DSS2/color") is None      # persistent absent
    assert sp.seed_bundled_pack() is True
    dest = sp.pack_dir()
    assert (dest / "pack.json").is_file()
    assert (dest / "Norder0" / "Dir0" / "Npix0.jpg").read_bytes() == b"\xff\xd8tile"
    assert sp.pack_present("CDS/P/DSS2/color") == dest


def test_seed_noop_when_persistent_pack_present(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    _write_pack(tmp_path / "bundled", order=3)
    _write_pack(tmp_path / "persist" / "_survey_pack", order=5)   # user's deeper pack
    assert sp.seed_bundled_pack() is False
    assert sp.read_manifest(sp.pack_dir())["order"] == 5         # untouched


def test_seed_noop_when_no_bundle(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)                                 # no bundled dir
    assert sp.seed_bundled_pack() is False
    assert sp.pack_present("CDS/P/DSS2/color") is None
