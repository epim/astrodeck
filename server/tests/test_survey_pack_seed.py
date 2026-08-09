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


def test_the_dss2_pack_is_never_seeded_however_it_got_into_the_build(
        tmp_path, monkeypatch):
    """#198. DSS is All Rights Reserved. STScI grants USE for non-profit
    activity and says nothing about third-party redistribution; CDS permit
    mirroring only where the original copyright authorises it, which here
    cannot be established. A release that bundles ~45 MB of these tiles and
    copies them onto the operator's disk at first boot is us distributing them.

    Refused HERE and not only in packaging, deliberately: a build that
    accidentally includes the directory would otherwise ship it silently, and
    "we removed it from the manifest" is a claim about a file nobody re-checks.
    The refusal SAYS WHY and names the permitted route, because a feature that
    stops working with no stated cause gets filed as a bug.
    """
    _dirs(tmp_path, monkeypatch)
    _write_pack(tmp_path / "bundled")                       # a bundle IS present
    assert sp.pack_present("CDS/P/DSS2/color") is None
    said: list[str] = []
    assert sp.seed_bundled_pack(log=said.append) is False
    assert sp.pack_present("CDS/P/DSS2/color") is None, (
        "the restricted pack was seeded onto the operator's disk")
    assert not (sp.pack_dir() / "pack.json").exists()
    assert any("licensed" in m for m in said), (
        f"the refusal did not state its reason: {said}")
    assert any("Atlas" in m for m in said), (
        f"the refusal did not name the permitted route: {said}")


def test_the_copy_mechanism_itself_still_works(tmp_path, monkeypatch):
    """The seeding CODE is not the problem — DSS2's licence is. So the copy
    path stays covered on an unrestricted slug, or removing the last permitted
    survey would silently delete the coverage along with the caller."""
    _dirs(tmp_path, monkeypatch)
    slug = "some-open-survey"
    _write_pack(tmp_path / "bundled", slug)
    assert sp.seed_bundled_pack(slug) is True
    dest = sp.pack_dir(slug)
    assert (dest / "pack.json").is_file()
    assert (dest / "Norder0" / "Dir0" / "Npix0.jpg").read_bytes() == b"\xff\xd8tile"


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
