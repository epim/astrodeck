"""#609: scripts/deps_floor_check.py reads the release's dependency floors.

A rig deploy runs this checker as the gate that refuses to start a release on
a venv below its pyproject floors. It must read EVERY requirement in the
[project] table's list. Its first version stopped at the "]" inside
"uvicorn[standard]>=0.30", so it saw 3 of 14 requirements and would have
passed a venv with cryptography and pillow below their security floors and
sgp4 missing.

MUTANT "the list ends at any ']'" (the quote-stripping removed from the
end-of-list test): RED, observed -
    AssertionError: assert ['fastapi>=0.141.1', 'starlette>=1.6.0', 'uvicorn[standard]>=0.30'] == [...]
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _checker():
    spec = importlib.util.spec_from_file_location("deps_floor_check", ROOT / "scripts" / "deps_floor_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_requirement_is_read_past_an_extras_bracket(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text(
        '[project]\nname = "x"\nversion = "1"\n'
        'dependencies = [\n'
        '    "fastapi>=0.141.1",\n'
        '    "starlette>=1.6.0",\n'
        '    "uvicorn[standard]>=0.30",\n'
        '    # a comment line with a ] bracket in it\n'
        '    "cryptography>=50.0.0",\n'
        '    "sgp4>=2.23",\n'
        ']\n'
        '[tool.other]\ndependencies = ["not-this>=1"]\n',
        encoding="utf-8")
    assert _checker().project_dependencies(str(p)) == [
        "fastapi>=0.141.1", "starlette>=1.6.0", "uvicorn[standard]>=0.30",
        "cryptography>=50.0.0", "sgp4>=2.23"]


def test_the_real_pyproject_lists_sgp4_and_the_security_floors():
    deps = _checker().project_dependencies(str(ROOT / "server" / "pyproject.toml"))
    names = {d.split(">")[0].split("[")[0].split("=")[0] for d in deps}
    assert {"fastapi", "starlette", "cryptography", "pillow", "sgp4"} <= names, deps


def test_a_missing_package_fails_the_gate_and_report_only_does_not(tmp_path, capsys):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\ndependencies = ["surely-not-installed-pkg-609>=1.0"]\n', encoding="utf-8")
    mod = _checker()
    assert mod.main(["x", str(p)]) == 1
    assert "MISSING" in capsys.readouterr().out
    assert mod.main(["x", str(p), "--report"]) == 0
