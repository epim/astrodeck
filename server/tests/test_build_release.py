"""Release-eng: build_release.py bundles vendored ASTAP + a baseline survey pack
into the staged package at the paths the runtime discovers, and --strict refuses
to ship a bundle that is missing one of them — including one nobody asked for,
because #100 was not a flag that failed, it was a flag nobody passed."""
import json
import sys
from pathlib import Path

import pytest

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


def test_build_bundles_astap(tmp_path):
    repo = _fake_repo(tmp_path)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out,
                        astap_dir=_fake_astap(tmp_path))

    staging = out / "astrodeck-9.9.9"
    vend = staging / "server" / "astrodeck" / "vendor" / "astap"
    assert (vend / "astap").read_bytes() == b"ELF"
    assert (vend / "d05.290").read_bytes() == b"db"
    assert "MPL-2.0" in (vend / "NOTICE.txt").read_text()

    manifest = json.loads((staging / "manifest.json").read_text())
    assert "server/astrodeck/vendor/astap" in manifest["contents"]
    assert (out / "astrodeck-9.9.9.tar.gz").is_file()


def test_dss2_tiles_never_reach_the_artifact_even_when_handed_over(
        tmp_path, capsys):
    """#198, at the only layer that actually matters.

    ``seed_bundled_pack`` already declines to INSTALL these tiles, but that is
    the second line of defence: SHIPPING is the infringement, and a tarball
    holding 45 MB of All-Rights-Reserved imagery infringes whether or not
    anything ever unpacks it. So the refusal is here too, where the bytes would
    be copied — and it PRINTS, because a caller still passing --survey-pack
    would otherwise conclude the flag worked."""
    repo = _fake_repo(tmp_path)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out,
                        astap_dir=_fake_astap(tmp_path),
                        survey_pack_dir=_fake_pack(tmp_path))

    staging = out / "astrodeck-9.9.9"
    assert not (staging / "server" / "astrodeck" / "catalog"
                / "_bundled_pack").exists(), "DSS2 tiles were staged anyway"
    manifest = json.loads((staging / "manifest.json").read_text())
    assert not [c for c in manifest["contents"] if "_bundled_pack" in c]
    said = capsys.readouterr().out
    assert "not ours to redistribute" in said, (
        f"the builder swallowed the refusal: {said}")


def _fake_ui(repo: Path) -> Path:
    d = repo / "ui" / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>")
    return d


def test_build_omits_assets_when_absent(tmp_path):
    repo = _fake_repo(tmp_path)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out)                       # no assets
    staging = out / "astrodeck-9.9.9"
    assert not (staging / "server" / "astrodeck" / "vendor").exists()
    manifest = json.loads((staging / "manifest.json").read_text())
    # The manifest lists what LANDED. It used to name ui/dist unconditionally,
    # so a bundle with no SPA in it still advertised one.
    assert manifest["contents"] == ["server"]


def test_strict_refuses_a_release_with_no_built_ui(tmp_path, capsys):
    """The bundle whose omission is invisible until someone installs it: the
    server comes up, /healthz is green, and there is no interface."""
    repo = _fake_repo(tmp_path)
    with pytest.raises(SystemExit):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True)
    out = capsys.readouterr().out
    assert "built UI" in out
    assert "no interface" in out               # what the omission COSTS


def test_an_empty_dist_directory_is_not_a_built_ui(tmp_path):
    """A `npm run build` that half-ran, or a dist left by a cleaned checkout,
    leaves a directory that `is_dir()` calls a UI. api/app.py mounts the SPA on
    index.html for exactly this reason, and the bundler now agrees with it."""
    repo = _fake_repo(tmp_path)
    (repo / "ui" / "dist" / "assets").mkdir(parents=True)          # no index.html
    with pytest.raises(SystemExit):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True)


def test_an_index_without_assets_is_not_a_built_ui(tmp_path):
    """The half-run `npm run build` the other way round: the HTML shell landed
    and the bundle it loads did not. api/app.py mounts the SPA only when
    index.html AND assets/ are both there, so this dist ships, the server comes
    up, /healthz is green, /assets/index-*.js 404s and the page is blank — the
    same user-visible outcome, from a bundle that passed the strict check."""
    repo = _fake_repo(tmp_path)
    (repo / "ui" / "dist").mkdir(parents=True)
    (repo / "ui" / "dist" / "index.html").write_text("<!doctype html>")
    with pytest.raises(SystemExit):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True)


def test_strict_fails_on_an_asset_the_command_line_never_mentioned(tmp_path, capsys):
    """#100's actual mechanism, and why turning --strict on in the workflow was
    not by itself the fix: a check that only inspects the assets it was handed
    cannot miss the one it was never given. A release job has to know what a
    complete release contains.

    The survey pack was the ORIGINAL example here and is deliberately no longer
    one (#198) — a complete release now ships no DSS2 tiles. ASTAP carries the
    property instead; it is the same mechanism with an asset we may actually
    distribute."""
    repo = _fake_repo(tmp_path)
    _fake_ui(repo)
    with pytest.raises(SystemExit):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True)
    out = capsys.readouterr().out
    assert "ASTAP" in out
    assert "plate solving" in out                   # what the omission COSTS
    assert "survey pack" not in out, (
        "strict is demanding the asset #198 removed — a check that blocks "
        "every release on the thing the fix deleted")


def test_an_omission_must_be_named_and_then_it_ships_in_the_manifest(tmp_path):
    """A release CAN go out without an asset — deliberately, by name. The waiver
    is then a decision at the call site and a record in the artifact, so the box
    with the black Atlas can say why instead of a build log nobody read."""
    repo = _fake_repo(tmp_path)
    _fake_ui(repo)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out, strict=True,
                        survey_pack_dir=_fake_pack(tmp_path),
                        allow_missing=["astap"])
    manifest = json.loads((out / "astrodeck-9.9.9" / "manifest.json").read_text())
    assert list(manifest["omitted"]) == ["astap"]
    assert "plate solving" in manifest["omitted"]["astap"]


def test_a_waiver_that_names_no_real_asset_is_refused(tmp_path):
    """A typo'd waiver waives nothing, and would otherwise read as one."""
    repo = _fake_repo(tmp_path)
    _fake_ui(repo)
    with pytest.raises(SystemExit, match="no such asset"):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True,
                            allow_missing=["astap_binary"])   # it is astap


def test_strict_passes_when_every_requested_asset_is_present(tmp_path):
    """The other direction: strict must not block a complete release."""
    repo = _fake_repo(tmp_path)
    _fake_ui(repo)
    out = tmp_path / "dist"
    tarball = build_release.build("9.9.9", repo, out, strict=True,
                                  astap_dir=_fake_astap(tmp_path),
                                  survey_pack_dir=_fake_pack(tmp_path))
    assert tarball.is_file()
    manifest = json.loads((out / "astrodeck-9.9.9" / "manifest.json").read_text())
    assert manifest["contents"] == [
        "server", "ui/dist",
        "server/astrodeck/vendor/astap",
    ]
    assert manifest["omitted"] == {}


def test_a_per_platform_astap_tree_is_not_a_solver_the_runtime_can_find(tmp_path):
    """`fetch_astap.py --all-platforms` writes <dir>/<platform>/astap_cli, and
    solve/astap.py::_bundled_candidates looks only at vendor/astap/astap[_cli].
    Copying that tree in ships 102 MB of star database and a solver nothing will
    ever execute, so it is not a bundled ASTAP and --strict must say so."""
    d = tmp_path / "assets" / "multi"
    (d / "linux-x86_64").mkdir(parents=True)
    (d / "linux-x86_64" / "astap_cli").write_bytes(b"ELF")
    (d / "d05.290").write_bytes(b"db")
    repo = _fake_repo(tmp_path)
    _fake_ui(repo)
    with pytest.raises(SystemExit):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True,
                            astap_dir=d, survey_pack_dir=_fake_pack(tmp_path),
                            allow_missing=["ui"])


def test_the_w08_database_counts_as_a_star_database(tmp_path):
    """W08 is 0.6 MB and ships where D05's 102 MB does not (a Pi image, a CI
    smoke test). Its files are `.001`; solve/astap.py::DB_EXTENSIONS accepts
    that, and the bundler used to check only .290/.1476 and call a working
    bundle databaseless."""
    d = tmp_path / "assets" / "w08"
    d.mkdir(parents=True)
    (d / "astap_cli").write_bytes(b"ELF")
    (d / "w08.001").write_bytes(b"db")
    repo = _fake_repo(tmp_path)
    _fake_ui(repo)
    out = tmp_path / "dist"
    build_release.build("9.9.9", repo, out, strict=True, astap_dir=d,
                        survey_pack_dir=_fake_pack(tmp_path))
    manifest = json.loads((out / "astrodeck-9.9.9" / "manifest.json").read_text())
    assert "server/astrodeck/vendor/astap" in manifest["contents"]


def test_strict_names_every_missing_asset_in_one_pass(tmp_path, capsys):
    """One rebuild per discovery is how a release takes an afternoon."""
    repo = _fake_repo(tmp_path)                                    # no ui/dist
    with pytest.raises(SystemExit):
        build_release.build("9.9.9", repo, tmp_path / "dist", strict=True,
                            astap_dir=tmp_path / "nope",
                            survey_pack_dir=tmp_path / "also-nope")
    out = capsys.readouterr().out
    assert "built UI" in out and "ASTAP" in out


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
