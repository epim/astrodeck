# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A test that needs the ``astrodeck_native`` wheel says so with
``@pytest.mark.native`` and nothing else (#1255).

Before this, a wheel-gated test gated itself by one of eight idioms of its
own, and the CI ``native`` job ran a hand-written list of files. 406 tests in
41 files ran on no job: the ``server`` job has no wheel, so every one skipped
there, and nothing named them. One marker, one hook in ``conftest.py``
(``_native_gate.py``), and the ``native`` job running ``pytest -m native`` over
the whole of ``server/tests`` close that.

This file keeps it closed. It reads source text and fails, naming the file and
line, when a test file gates itself on the wheel any other way:

* ``skipif`` whose condition names the wheel (``NATIVE_AVAILABLE``,
  ``_HAS_NATIVE``, ``astrodeck_native``);
* ``importorskip("astrodeck_native")``, at module level or in a body;
* ``try: import astrodeck_native ... except ImportError`` (a flag set there is
  what the skip then reads); ``_native_gate.optional_native()`` is the import
  that keeps a file collectable without the wheel;
* ``if not NATIVE_AVAILABLE:`` followed by ``pytest.skip``, or a
  ``pytest.skip`` whose reason names the wheel.

``conftest.py`` and ``_native_gate.py`` hold the one gate and are not scanned.

NAMED MUTANT "a test file gates itself with a bare skipif on the wheel again"
(``pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="...")`` put
back at the top of ``test_native_guider_stop.py``, run from a byte backup and
restored with its sha256 checked): ``test_no_test_file_gates_itself_on_the_
wheel`` is RED, naming that file and line.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

#: The files that ARE the gate, and this one, which names every idiom.
_EXEMPT = frozenset({"conftest.py", "_native_gate.py",
                     "test_native_gate_ratchet.py"})

_WHEEL = r"(?:NATIVE_AVAILABLE|_HAS_NATIVE|astrodeck_native)"

_IMPORTORSKIP = re.compile(r"importorskip\(\s*[\"']astrodeck_native[\"']")
_SKIPIF = re.compile(r"skipif\(")
_TRY_IMPORT = re.compile(
    r"^(?P<indent>[ \t]*)try:[ \t]*\n(?:(?P=indent)[ \t]+.*\n){0,3}?"
    r"(?P=indent)[ \t]+(?:import astrodeck_native|from astrodeck_native)\b"
    r".*\n(?:(?P=indent)[ \t]+.*\n){0,3}?(?P=indent)except (?:ImportError|"
    r"ModuleNotFoundError)", re.M)
_IF_NOT_AVAILABLE = re.compile(
    r"^[ \t]*if not [\w.]*(?:NATIVE_AVAILABLE|_HAS_NATIVE)\b[^\n]*\n"
    r"(?:[ \t]*#[^\n]*\n)*[ \t]+pytest\.skip\(", re.M)
_SKIP_NAMING_WHEEL = re.compile(
    r"pytest\.skip\(\s*(?:f?[\"'][^\"']*(?:wheel|astrodeck_native)[^\"']*[\"'])",
    re.I)


def _line(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _call_text(src: str, open_paren: int) -> str:
    """The text of the call whose ``(`` is at ``open_paren``."""
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren:i + 1]
    return src[open_paren:]


def _code_only(src: str) -> str:
    """The source with whole-line comments blanked, line numbers kept."""
    return "\n".join("" if ln.lstrip().startswith("#") else ln
                     for ln in src.split("\n"))


def gating_offences(src: str) -> list[tuple[int, str]]:
    """Every line of ``src`` that gates on the wheel by a way other than the
    marker, as (line, what)."""
    code = _code_only(src)
    found: list[tuple[int, str]] = []
    for m in _IMPORTORSKIP.finditer(code):
        found.append((_line(code, m.start()), "importorskip of astrodeck_native"))
    for m in _SKIPIF.finditer(code):
        if re.search(_WHEEL, _call_text(code, m.end() - 1)):
            found.append((_line(code, m.start()), "skipif on the wheel"))
    for m in _TRY_IMPORT.finditer(code):
        found.append((_line(code, m.start()),
                      "try/except around the import of astrodeck_native"))
    for m in _IF_NOT_AVAILABLE.finditer(code):
        found.append((_line(code, m.start()),
                      "pytest.skip behind NATIVE_AVAILABLE"))
    for m in _SKIP_NAMING_WHEEL.finditer(code):
        found.append((_line(code, m.start()), "pytest.skip naming the wheel"))
    return sorted(set(found))


# ------------------------------------------------------------- the real tree

def _scanned() -> list[Path]:
    return sorted(p for p in TESTS.glob("*.py") if p.name not in _EXEMPT)


def test_no_test_file_gates_itself_on_the_wheel():
    offences = [f"{p.name}:{line}: {what}"
                for p in _scanned()
                for line, what in gating_offences(
                    p.read_text(encoding="utf-8"))]
    assert not offences, (
        "a test needs the astrodeck_native wheel by a way other than "
        "@pytest.mark.native (or a module-level pytestmark = "
        "pytest.mark.native). Any other gate skips on every CI job that has "
        "no wheel and is invisible on the one that has it. Where the import "
        "must happen at module level, use _native_gate.optional_native(). "
        "Offences:\n  " + "\n  ".join(offences))


def test_the_scan_sees_the_files_it_is_meant_to_grade():
    names = {p.name for p in _scanned()}
    assert "test_native_guider_stop.py" in names
    assert "test_tppa_engine_geometry.py" in names
    assert "conftest.py" not in names and "_native_gate.py" not in names


# ------------------------------------------------- the scanner, on made-up sources

@pytest.mark.parametrize("src, what", [
    ('import pytest\nfrom astrodeck.providers import NATIVE_AVAILABLE\n\n'
     'pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="x")\n',
     "skipif on the wheel"),
    ('native_only = pytest.mark.skipif(\n    not providers.NATIVE_AVAILABLE,\n'
     '    reason="astrodeck_native wheel not installed")\n',
     "skipif on the wheel"),
    ('needs = pytest.mark.skipif(not _HAS_NATIVE, reason="x")\n',
     "skipif on the wheel"),
    ('native = pytest.importorskip("astrodeck_native")\n',
     "importorskip of astrodeck_native"),
    ("def test_a():\n    nat = pytest.importorskip(\n        'astrodeck_native')\n",
     "importorskip of astrodeck_native"),
    ('try:\n    import astrodeck_native as an\n    _HAS = True\n'
     'except ImportError:\n    an = None\n    _HAS = False\n',
     "try/except around the import of astrodeck_native"),
    ('def test_a():\n    if not NATIVE_AVAILABLE:\n'
     '        pytest.skip("no engine")\n',
     "pytest.skip behind NATIVE_AVAILABLE"),
    ('def test_a():\n    if "lock" not in e.stats():\n'
     '        pytest.skip("installed astrodeck_native wheel predates lock")\n',
     "pytest.skip naming the wheel"),
])
def test_each_gating_idiom_is_named(src, what):
    offences = gating_offences(src)
    assert [w for _line, w in offences] == [what], offences


def test_the_offence_names_the_line():
    src = ("import pytest\n\n\n"
           "pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE,\n"
           "                                reason=\"x\")\n")
    assert gating_offences(src) == [(4, "skipif on the wheel")]


@pytest.mark.parametrize("src", [
    # the marker, plain and with a probe
    "pytestmark = pytest.mark.native\n",
    "@pytest.mark.native(lacks=_wheel_lacks_peek)\ndef test_a():\n    pass\n",
    # the import that keeps a file collectable without the wheel
    "from _native_gate import optional_native\nnative = optional_native()\n",
    # a skipif on something else
    'import sys\npytestmark = pytest.mark.skipif(sys.platform != "win32", '
    'reason="Windows only")\n',
    # a comment that names the idiom
    "# was pytest.mark.skipif(not NATIVE_AVAILABLE, reason='x')\n",
    # a test about the flag, not gated by it
    "def test_a(monkeypatch):\n"
    "    monkeypatch.setattr(providers, 'NATIVE_AVAILABLE', True)\n",
    "def test_b():\n    if not NATIVE_AVAILABLE:\n        assert g is None\n",
    # a try/except ImportError that is not the wheel
    "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n",
])
def test_what_is_not_a_gate_is_not_flagged(src):
    assert gating_offences(src) == []


# ------------------------------------------------------- the gate, on a stub item

class _Item:
    """Just enough of a pytest item for ``_native_gate.gate``."""

    def __init__(self, *marks):
        self._marks = [m.mark for m in marks]

    def iter_markers(self, name):
        return iter(m for m in self._marks if m.name == name)

    def get_closest_marker(self, name):
        return next(self.iter_markers(name), None)


def _wheel(monkeypatch, present: bool, require: bool):
    import _native_gate
    monkeypatch.setattr(_native_gate, "optional_native",
                        lambda: object() if present else None)
    if require:
        monkeypatch.setenv(_native_gate.REQUIRE_ENV, "1")
    else:
        monkeypatch.delenv(_native_gate.REQUIRE_ENV, raising=False)
    return _native_gate


def test_no_wheel_skips_with_the_one_reason(monkeypatch):
    gate = _wheel(monkeypatch, present=False, require=False)
    with pytest.raises(pytest.skip.Exception) as ei:
        gate.gate(_Item(pytest.mark.native))
    assert str(ei.value) == gate.WHEEL_ABSENT


def test_no_wheel_is_an_error_where_the_wheel_is_required(monkeypatch):
    gate = _wheel(monkeypatch, present=False, require=True)
    with pytest.raises(pytest.fail.Exception) as ei:
        gate.gate(_Item(pytest.mark.native))
    assert gate.WHEEL_ABSENT in str(ei.value)
    assert "ASTRODECK_REQUIRE_NATIVE=1" in str(ei.value)


def test_a_wheel_that_lacks_a_symbol_skips_or_errors_the_same_way(monkeypatch):
    item = _Item(pytest.mark.native(lacks=lambda: "the wheel has no peek_next"))
    gate = _wheel(monkeypatch, present=True, require=False)
    with pytest.raises(pytest.skip.Exception, match="no peek_next"):
        gate.gate(item)
    gate = _wheel(monkeypatch, present=True, require=True)
    with pytest.raises(pytest.fail.Exception, match="no peek_next"):
        gate.gate(item)


def test_a_wheel_that_has_what_the_test_needs_lets_it_run(monkeypatch):
    gate = _wheel(monkeypatch, present=True, require=True)
    gate.gate(_Item(pytest.mark.native))
    gate.gate(_Item(pytest.mark.native(lacks=lambda: "")))


def test_a_test_without_the_marker_is_left_alone(monkeypatch):
    gate = _wheel(monkeypatch, present=False, require=True)
    gate.gate(_Item())


def test_a_wheel_that_is_installed_but_will_not_import_is_not_hidden(monkeypatch):
    import _native_gate
    monkeypatch.setitem(sys.modules, "astrodeck_native", None)
    monkeypatch.setattr(_native_gate, "_installed", lambda: True)
    with pytest.raises(ImportError):
        _native_gate.optional_native()
    monkeypatch.setattr(_native_gate, "_installed", lambda: False)
    assert _native_gate.optional_native() is None


# ------------------------------------------- a child session over a copy of conftest

def test_a_conftest_copied_alone_gates_nothing_it_does_not_mark(tmp_path):
    """Several tests run a child session over a copy of conftest.py, alone in a
    temp directory (test_real_config_guard.py), or load it by path from another
    rootdir. ``_native_gate.py`` is not beside it there, and a test with no
    ``native`` marker must never need it: with the hook importing it for every
    test, each such test ERRORED at setup with ModuleNotFoundError (the server
    job's first run of #1255).

    NAMED MUTANT "the hook imports the gate for every test" (``from
    _native_gate import gate`` put back above the marker check, run from a byte
    backup, restored with its sha256 checked): RED, the child's one test is an
    error at setup."""
    import os
    import subprocess

    import astrodeck
    tests = tmp_path / "repo" / "server" / "tests"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_bytes((TESTS / "conftest.py").read_bytes())
    (tests / "test_probe.py").write_text("def test_trivial():\n    assert True\n",
                                         encoding="utf-8")
    ini = tmp_path / "repo" / "server" / "pytest.ini"
    ini.write_text("[pytest]\naddopts =\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["ASTRODECK_CONFIG_DIR"] = str(tmp_path / "config")
    env["ASTRODECK_CAPTURE_DIR"] = str(tmp_path / "repo" / "captures")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(astrodeck.__file__).resolve().parents[1]),
         *filter(None, [env.get("PYTHONPATH")])])
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p",
         "no:randomly", "-n", "0", "-q", "-c", str(ini), "--rootdir",
         str(tmp_path / "repo" / "server"), str(tests / "test_probe.py")],
        cwd=tmp_path / "repo" / "server", env=env, capture_output=True,
        text=True, timeout=300)
    assert "1 passed" in run.stdout and run.returncode == 0, (
        run.stdout[-2000:] + run.stderr[-1000:])
