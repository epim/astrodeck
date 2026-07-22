"""Release-eng: build_release.py bundles vendored ASTAP + a baseline survey pack
into the staged package at the paths the runtime discovers."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_release  # noqa: E402


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "server" / "astrodeck" / "catalog").mkdir(parents=True)
    (repo / "server" / "astrodeck" / "__init__.py").write_text("")
    (repo / "server" / "pyproject.toml").write_text("[project]\nname = 'astrodeck'\n")
    return repo


def _fake_astap(tmp_path: Path) -> Path:
    d = tmp_path / "assets" / "astap"
    d.mkdir(parents=True)
    (d / "astap").write_bytes(b"ELF")
    (d / "d05.290").write_bytes(b"db")
    return d


def _fake_pack(tmp_path: Path) -> Path:
    d = tmp_path / "assets" / "dss2color"
    (d / "Norder0" / "Dir0").mkdir(parents=True)
    (d / "Norder0" / "Dir0" / "Npix0.jpg").write_bytes(b"\xff\xd8")
    (d / "pack.json").write_text(json.dumps({"slug": "dss2color", "order": 3}))
    return d


def test_build_bundles_astap_and_pack(tmp_path):
    repo = _fake_repo(tmp_path)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out,
                        astap_dir=_fake_astap(tmp_path),
                        survey_pack_dir=_fake_pack(tmp_path))

    staging = out / "astrodeck-9.9.9"
    vend = staging / "server" / "astrodeck" / "vendor" / "astap"
    assert (vend / "astap").read_bytes() == b"ELF"
    assert (vend / "d05.290").read_bytes() == b"db"
    assert "MPL-2.0" in (vend / "NOTICE.txt").read_text()
    bp = staging / "server" / "astrodeck" / "catalog" / "_bundled_pack" / "dss2color"
    assert (bp / "pack.json").is_file()
    assert (bp / "Norder0" / "Dir0" / "Npix0.jpg").is_file()

    manifest = json.loads((staging / "manifest.json").read_text())
    assert "server/astrodeck/vendor/astap" in manifest["contents"]
    assert "server/astrodeck/catalog/_bundled_pack/dss2color" in manifest["contents"]
    assert (out / "astrodeck-9.9.9.tar.gz").is_file()


def test_build_omits_assets_when_absent(tmp_path):
    repo = _fake_repo(tmp_path)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out)                       # no assets
    staging = out / "astrodeck-9.9.9"
    assert not (staging / "server" / "astrodeck" / "vendor").exists()
    manifest = json.loads((staging / "manifest.json").read_text())
    assert manifest["contents"] == ["server", "ui/dist"]


def test_build_survey_pack_needs_manifest(tmp_path):
    repo = _fake_repo(tmp_path)
    bad = tmp_path / "assets" / "nomanifest"
    (bad / "Norder0").mkdir(parents=True)                         # tiles but no pack.json
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out, survey_pack_dir=bad)
    staging = out / "astrodeck-9.9.9"
    assert not (staging / "server" / "astrodeck" / "catalog" / "_bundled_pack").exists()
    manifest = json.loads((staging / "manifest.json").read_text())
    assert "server/astrodeck/catalog/_bundled_pack/dss2color" not in manifest["contents"]
