# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-96 (#308, code part): ``tools/no_light_audit.py`` tabulates the no-light
check's verdicts out of the durable night logs.

#308 asks that the check's band be held against real frames of a thick,
moonless overcast at the solve's readout. Those frames do not exist yet; what
can be built now is the instrument that reads the record when they do. The
server logs one ``failed solve, light check:`` line per judged frame and, since
this WP, ends its evidence with the frame's readout. The audit prints a table
per night with the margin ``(median - reference) / band``, marks the nights the
operator names as moonless, and takes no site input.

The lines below are copies of what ``failed_solve_error`` really logs; the
parser is held to the REAL emitted text by the round-trip case of
``test_failed_solve_says_no_light.py``, which goes red first if the format
moves. These cases hold the tool's own behaviour: the table, the marks, the
exit codes, that it only reads, and that it carries nothing that could place a
site.

MUTATIONS RUN, 2026-10-07, each from a byte backup of tools/no_light_audit.py,
restored and sha256-verified afterwards, the mutant text grepped to be gone.
Each names the case that goes red under it, observed verbatim, in that case's
docstring below; a fourth, "the audit reads a dark master as a bias master"
(the ``("dark master", "dark_master")`` row of ``_SOURCE_KINDS`` changed to
``"bias_master"``), is recorded in the round-trip case of
test_failed_solve_says_no_light.py, where the real lines meet the parser.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

# APPENDED, not inserted at position 0, as test_credits.py does.
_TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.append(str(_TOOLS))
import no_light_audit as audit  # noqa: E402

#: Copies of the lines ``failed_solve_error`` logs for a capped frame against
#: a dark master, a lit overcast against it, and (the old format) the same
#: capped frame before the readout clause existed.
CAPPED = ("failed solve, light check: median 251.0 ADU, robust sigma 8.9 ADU "
          "per pixel; reference 250.0 ADU from the dark master for these "
          "settings (dark master dark_250); band +/-3.00 ADU; no_light; "
          "frame 12 s bin 2 gain 200 offset 30, sensor -5.0 C")
OVERCAST = CAPPED.replace("median 251.0", "median 288.0").replace(
    "robust sigma 8.9", "robust sigma 14.8").replace("; no_light;", "; cloud;")
BEFORE_THE_READOUT = CAPPED.split("; frame ")[0]
NO_PIXELS = "failed solve, light check: unknown: the frame has no pixels to measure"


def _write(directory: Path, night: str, messages: list[str],
           *, other: tuple[str, ...] = ()) -> Path:
    """A night log: the given messages as ``solve`` log events, with
    ``other`` raw lines mixed in."""
    path = directory / f"{night}.jsonl"
    lines = [json.dumps({"type": "log", "ts": 1_760_000_000.0 + i,
                         "data": {"level": "info", "message": m,
                                  "source": "solve"}})
             for i, m in enumerate(messages)]
    path.write_text("\n".join([*lines, *other]) + "\n", encoding="utf-8")
    return path


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = audit.main(argv, out=out)
    return code, out.getvalue()


def _row(text: str, verdict: str) -> list[str]:
    rows = [ln.split() for ln in text.splitlines()
            if len(ln.split()) > 2 and ln.split()[1] == verdict]
    assert len(rows) == 1, f"expected one {verdict} row in:\n{text}"
    return rows[0]


def test_the_table_has_a_row_per_frame_with_its_margin_and_readout(tmp_path):
    """Margin is ``(median - reference) / band``: 1 ADU over a 3 ADU band is
    +0.33, and 38 over is +12.67. The readout columns come from the clause.

    RED under mutant "the margin multiplies by the band" (``/ self.band``
    in ``Reading.margin`` changed to ``* self.band``), observed verbatim:

        E   AssertionError: ['01:53:20', 'no_light', 'dark_master', '251.0', '250.0', '3.00', ...]
        E   assert ['dark_master...0', '12', ...] == ['dark_master...3', '12', ...]
    """
    _write(tmp_path, "2026-10-14", [CAPPED, OVERCAST])

    code, text = _run([str(tmp_path)])

    assert code == 0, text
    capped = _row(text, "no_light")
    assert capped[2:12] == ["dark_master", "251.0", "250.0", "3.00", "+0.33",
                            "12", "2", "200", "30", "-5.0"], capped
    cloud = _row(text, "cloud")
    assert cloud[3:7] == ["288.0", "250.0", "3.00", "+12.67"], cloud
    assert "2 judged: cloud 1, no_light 1" in text, text
    assert "2 judged frames on 1 night in 1 file" in text, text


def test_moonless_marks_only_the_nights_the_operator_names(tmp_path):
    """The tool is told which nights had no Moon and computes nothing: the
    named night's block and rows are marked, the other's are not.

    RED under mutant "no night is marked" (``moonless=night in moonless``
    changed to ``moonless=False`` in ``collect``), observed verbatim:

        E   AssertionError: assert '== 2026-10-14 (moonless) ==' in '== 2026-10-14 ==\ntime      verdict    reference ...'
    """
    _write(tmp_path, "2026-10-14", [CAPPED])
    _write(tmp_path, "2026-10-15", [CAPPED])

    code, text = _run([str(tmp_path), "--moonless", "2026-10-14, 2026-10-30"])

    assert code == 0, text
    first, second = text.split("== 2026-10-15")
    assert "== 2026-10-14 (moonless) ==" in first
    assert _row(first, "no_light")[-1] == "M", first
    assert "(moonless)" not in second and "M" not in _row(second, "no_light")[12:]


def test_a_moonless_night_with_no_log_is_said_not_ignored(tmp_path, capsys):
    """A typo in the calendar must not read as a night with no failed solves."""
    _write(tmp_path, "2026-10-14", [CAPPED])

    _run([str(tmp_path), "--moonless", "2026-10-14,2026-10-30"])

    assert "2026-10-30" in capsys.readouterr().err


def test_a_line_logged_before_the_readout_clause_still_parses(tmp_path):
    """Nights logged before the clause existed are the bulk of the record:
    their rows show dashes for the readout and are counted as such.

    RED under mutant "the readout is required" (the readout group of the
    pattern lost its trailing ``?``), observed verbatim:

        E   AssertionError: 0 judged frames on 0 nights in 1 file
        E   assert 1 == 0
    (the exit code: the line is listed as unreadable, and nothing is tabulated)
    """
    _write(tmp_path, "2026-10-14", [BEFORE_THE_READOUT])

    code, text = _run([str(tmp_path)])

    assert code == 0, text
    row = _row(text, "no_light")
    assert row[7:12] == ["-", "-", "-", "-", "-"], row
    assert "1 without a readout" in text, text


def test_a_verdict_with_no_median_is_a_row_of_dashes(tmp_path):
    _write(tmp_path, "2026-10-14", [NO_PIXELS])

    code, text = _run([str(tmp_path)])

    assert code == 0, text
    row = _row(text, "unknown")
    assert row[2:7] == ["-", "-", "-", "-", "-"], row


def test_only_light_check_log_events_are_read(tmp_path, capsys):
    """Other lines, other event types, an unrelated line that merely quotes
    the prefix, and a torn final write are skipped; the torn one is counted
    on stderr, since it may have been one of ours."""
    path = _write(tmp_path, "2026-10-14", ["plate solve failed: no stars", CAPPED],
                  other=(json.dumps({"type": "status",
                                     "data": {"message": CAPPED}}),
                         json.dumps({"type": "log", "data": {
                             "message": "see: " + CAPPED}}),
                         '{"type":"log","data":{"message":"failed solve, light check: med'))
    before = path.read_bytes()

    code, text = _run([str(tmp_path)])

    assert code == 0, text
    assert "1 judged frame on 1 night" in text, text
    assert "1 light-check line(s) were not JSON" in capsys.readouterr().err
    assert path.read_bytes() == before, "the audit wrote to the log"


def test_a_light_check_line_it_cannot_read_is_listed_and_exits_1(tmp_path,
                                                                capsys):
    """Format drift must not read as a quiet night: the unreadable line's
    file and number are named and the exit code says so, while the lines it
    can read are still tabulated."""
    path = _write(tmp_path, "2026-10-14", [
        CAPPED, "failed solve, light check: the verdict is now spelt in prose"])

    code, text = _run([str(tmp_path)])

    assert code == 1
    assert "1 judged frame on 1 night" in text
    assert f"{path}:2" in capsys.readouterr().err


def test_nothing_to_read_is_exit_2_and_never_a_quiet_success(tmp_path, capsys):
    assert _run([str(tmp_path)])[0] == 2
    assert _run([str(tmp_path / "no_such_dir")])[0] == 2
    err = capsys.readouterr().err
    assert "no *.jsonl" in err and "no such file or directory" in err


def test_with_no_path_it_reads_the_capture_logs(tmp_path, monkeypatch):
    assert audit.DEFAULT_LOGS == audit.REPO / "captures" / "logs"
    _write(tmp_path, "2026-10-14", [CAPPED])
    monkeypatch.setattr(audit, "DEFAULT_LOGS", tmp_path)

    code, text = _run([])

    assert code == 0 and "1 judged frame on 1 night" in text, text


def test_a_bad_moonless_night_is_a_usage_error():
    with pytest.raises(SystemExit) as stop:
        audit.main(["--moonless", "tuesday"], out=io.StringIO())
    assert stop.value.code == 2


def test_it_takes_no_site_and_has_nothing_that_could_compute_the_sky(tmp_path):
    """No flag can carry a site, and the module imports only the standard
    library's text, file and date handling: nothing that knows where a body
    is. The Moon is the operator's calendar, as ``--moonless``."""
    for flag in ("--lat", "--lon", "--site", "--latitude", "--longitude"):
        with pytest.raises(SystemExit) as stop:
            audit.main([flag, "1.0"], out=io.StringIO())
        assert stop.value.code == 2, flag

    tree = ast.parse(Path(audit.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "argparse", "dataclasses", "datetime",
                        "json", "math", "pathlib", "re", "sys", "typing"}, \
        sorted(imported)


def test_it_only_reads(tmp_path):
    """The logs are byte for byte what they were after a run."""
    _write(tmp_path, "2026-10-14", [CAPPED, OVERCAST])
    _write(tmp_path, "2026-10-15", [BEFORE_THE_READOUT])

    def digest() -> list[str]:
        return [hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(tmp_path.iterdir())]

    before = digest()
    _run([str(tmp_path), "--moonless", "2026-10-14"])

    assert digest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-10-14.jsonl", "2026-10-15.jsonl"]
