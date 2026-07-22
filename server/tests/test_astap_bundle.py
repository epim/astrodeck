"""Release-eng (UX-04): bundled-ASTAP wiring — the `-d` star-DB flag assembly and
the vendored binary/DB discovery."""
from pathlib import Path

import astrodeck.solve.astap as astap


def test_solve_args_appends_db_dir_when_present():
    args = astap._solve_args("astap", Path("img.fits"), None, None, None, Path("/db/astap"))
    assert args[:5] == ["astap", "-f", "img.fits", "-z", "0"]
    assert "-d" in args
    assert args[args.index("-d") + 1] == str(Path("/db/astap"))


def test_solve_args_omits_db_dir_when_none():
    args = astap._solve_args("astap", Path("img.fits"), None, None, None, None)
    assert "-d" not in args
    assert "-r" in args and args[args.index("-r") + 1] == "180"   # blind solve


def test_solve_args_hinted_includes_ra_spd_fov_and_db():
    args = astap._solve_args("astap", Path("i.fits"), 3.0, 45.0, 1.5, Path("/db"))
    assert args[args.index("-spd") + 1] == "135.0000"            # dec + 90
    assert args[args.index("-fov") + 1] == "1.50"
    assert args[args.index("-d") + 1] == str(Path("/db"))


def test_bundled_db_dir_detects_290(tmp_path, monkeypatch):
    monkeypatch.setattr(astap, "_VENDOR_ASTAP", tmp_path)
    assert astap._bundled_db_dir() is None                       # empty -> no DB
    (tmp_path / "d05_star.290").write_bytes(b"x")
    assert astap._bundled_db_dir() == tmp_path                   # .290 present


def test_db_dir_env_override_wins(tmp_path, monkeypatch):
    env_db = tmp_path / "envdb"
    env_db.mkdir()
    monkeypatch.setenv("ASTAP_DATA", str(env_db))
    monkeypatch.setattr(astap, "_VENDOR_ASTAP", tmp_path / "vendor")  # no bundled DB
    assert astap._db_dir() == env_db


def test_db_dir_none_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.delenv("ASTAP_DATA", raising=False)
    monkeypatch.setattr(astap, "_VENDOR_ASTAP", tmp_path / "nope")
    assert astap._db_dir() is None
