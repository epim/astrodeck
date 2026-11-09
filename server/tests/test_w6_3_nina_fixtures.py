# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 6.3 (#766): the NINA import fixtures hold what the design says they hold.

There is no importer yet (ruling D-L: a design and a fixture set first, no
code until the owner has read it), so these tests grade the fixtures against
the code that exists:

* every ``*.expected.json`` is a body ``POST /api/flows`` accepts
  (``FlowSaveBody``), a graph with no structural error, one the doctor finds
  no danger in beyond what the README lists, and one the compile and the plan
  builder take;
* every ``*.nina.json`` parses, is plain ASCII with LF endings, resolves each
  of its ``$ref`` values, carries no site field, and has its expected file;
* every warning an expected file lists uses a code and a level the design's
  catalogue (``docs/flows/nina-import.md``, section 9) declares;
* the README lists each pair, and only the pairs that exist.

Each failure names the file, so a fixture that drifts is found by name.

NAMED MUTANT (run from a byte backup in the scratch directory, restored and
sha256-compared): change one node's ``type`` in
``single_dso_lrgb.expected.json`` to one that does not exist. Observed:

    Failed: single_dso_lrgb.expected.json: the flow library refuses it: ...
    Value error, unknown node type 'telescope'
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from astrodeck.api.app import FlowSaveBody
from astrodeck.flows import check
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nina"
SPEC = Path(__file__).resolve().parents[2] / "docs" / "flows" / "nina-import.md"

#: Keys of a NINA file that would carry the operator's site. The design
#: (section 8) says the importer never reads or echoes them; the fixtures must
#: not hold one.
SITE_KEYS = ("latitude", "longitude", "elevation")

#: What a warning in an expected file may carry.
WARNING_KEYS = {"code", "level", "type", "path"}

#: The class a NINA root or container `$type` names, before the assembly.
NINA_CONTAINER = "NINA.Sequencer.Container."


def _names(suffix: str) -> list[str]:
    return sorted(p.name[: -len(suffix)] for p in FIXTURES.glob(f"*{suffix}"))


NINA_NAMES = _names(".nina.json")
EXPECTED_NAMES = _names(".expected.json")


def _bytes(path: Path) -> bytes:
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name}: has a byte order mark"
    assert b"\r" not in data, f"{path.name}: has a carriage return, LF only"
    try:
        data.decode("ascii")
    except UnicodeDecodeError as e:
        pytest.fail(f"{path.name}: not ASCII at byte {e.start}")
    return data


def _json(path: Path):
    return json.loads(_bytes(path))


def _walk(value):
    """Every dict in a JSON tree."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def _catalogue() -> dict[str, str]:
    """``{code: level}`` from the design's warning catalogue (section 9)."""
    rows = re.findall(r"^\| `([a-z_]+)` \| (note|warn) \|", SPEC.read_text(encoding="utf-8"),
                      re.M)
    assert rows, f"{SPEC.name}: no warning catalogue rows found"
    return dict(rows)


def _readme_rows() -> dict[str, str]:
    """``{pair: doctor dangers cell}`` from the README's table of pairs."""
    text = (FIXTURES / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([a-z0-9_]+)` \|.*\| ([^|]+?) \|$", text, re.M)
    assert rows, "README.md: no rows in the table of pairs"
    return dict(rows)


def _allowed_dangers(name: str) -> list[str]:
    cell = _readme_rows()[name].strip()
    return [] if cell == "none" else [part.strip() for part in cell.split(";")]


def test_there_are_fixture_pairs():
    assert len(NINA_NAMES) >= 5, NINA_NAMES
    assert NINA_NAMES == EXPECTED_NAMES, (
        f"every NINA file needs an expected file and the other way round: "
        f"{sorted(set(NINA_NAMES) ^ set(EXPECTED_NAMES))}")


@pytest.mark.parametrize("name", NINA_NAMES)
def test_a_nina_file_parses_resolves_and_has_an_expected_file(name):
    path = FIXTURES / f"{name}.nina.json"
    root = _json(path)
    assert str(root.get("$type", "")).startswith(NINA_CONTAINER), (
        f"{path.name}: the root is not a NINA container: {root.get('$type')!r}")
    ids = [d["$id"] for d in _walk(root) if "$id" in d]
    assert len(ids) == len(set(ids)), f"{path.name}: a duplicate $id"
    refs = [d["$ref"] for d in _walk(root) if "$ref" in d]
    assert refs, f"{path.name}: no $ref at all, so it is not in NINA's shape"
    dangling = sorted(set(refs) - set(ids))
    assert not dangling, f"{path.name}: $ref {dangling} names no $id"
    for d in _walk(root):
        for key in d:
            assert not any(s in key.lower() for s in SITE_KEYS), (
                f"{path.name}: holds a site field {key!r}")
    assert (FIXTURES / f"{name}.expected.json").is_file(), (
        f"{path.name}: has no {name}.expected.json")


def _saved(name: str) -> FlowSaveBody:
    """The expected file as the body ``POST /api/flows`` takes, or a failure
    that names the file."""
    path = FIXTURES / f"{name}.expected.json"
    try:
        return FlowSaveBody.model_validate(_json(path))
    except ValidationError as e:
        pytest.fail(f"{path.name}: the flow library refuses it: {e}")


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_an_expected_file_is_a_flow_the_library_accepts(name):
    path = FIXTURES / f"{name}.expected.json"
    errors = _saved(name).flow.graph.validation_errors()
    assert not errors, f"{path.name}: the graph is not valid: {errors}"
    assert (FIXTURES / f"{name}.nina.json").is_file(), (
        f"{path.name}: has no {name}.nina.json")


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_an_expected_flow_compiles_with_no_danger_the_readme_does_not_list(name):
    path = FIXTURES / f"{name}.expected.json"
    flow = _saved(name).flow
    allowed = _allowed_dangers(name)
    dangers = [i.text for i in check(flow.graph) if i.level == "danger"]
    unexpected = [t for t in dangers if not any(t.startswith(a) for a in allowed)]
    assert not unexpected, (
        f"{path.name}: the doctor finds a danger the README does not list: {unexpected}")
    compiled = compile_plan(flow.graph, flow.name)
    try:
        plan, _unmapped = to_sequence_plan(compiled, flow.graph, flow_id=f"nina-{name}")
    except GraphNotRunnable as e:
        pytest.fail(f"{path.name}: the flow cannot become a plan: {e}")
    assert plan.targets, f"{path.name}: the plan has no target"


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_an_expected_warning_uses_a_catalogue_code_at_its_level(name):
    path = FIXTURES / f"{name}.expected.json"
    warnings = _json(path).get("warnings")
    assert isinstance(warnings, list) and warnings, f"{path.name}: lists no warnings"
    catalogue = _catalogue()
    for w in warnings:
        assert set(w) <= WARNING_KEYS and {"code", "level"} <= set(w), (
            f"{path.name}: a warning has keys {sorted(w)}")
        assert w["code"] in catalogue, (
            f"{path.name}: {w['code']!r} is not in the design's catalogue")
        assert w["level"] == catalogue[w["code"]], (
            f"{path.name}: {w['code']} is {catalogue[w['code']]} in the design, "
            f"{w['level']} here")


def test_the_readme_lists_each_pair_and_only_the_pairs_that_exist():
    listed = sorted(_readme_rows())
    assert listed == NINA_NAMES, (
        f"README.md lists {listed}; the pairs on disk are {NINA_NAMES}")


def test_the_readme_is_plain_ascii_with_lf():
    _bytes(FIXTURES / "README.md")
