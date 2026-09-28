"""``_compile_payload``'s docstring says what its answer carries (#189 S4 item
14).

The docstring is the only description of the compile route's answer: the
route declares no response model, so a reader learns the keys from it or
from a network tab. It said "``{plan, structural, issues, unmapped}``" and
"FOUR lists" after the answer's first key had long been the compile dict,
and "the doctor's ten advisory rules" while the doctor ran 28. A docstring
that miscounts is how the mosaic design review nearly scheduled work on a
copy of the rules that does not exist (#169).

So this file holds the docstring to the code, both ways:

* THE KEYS: the names in the docstring's first line and the names its
  bullets describe are each exactly the keys the route answers, for a
  runnable graph and for one the compile refuses (the refusal path builds
  the answer with the same return, and must not lose a key).
* THE RULE COUNT: the numbers the docstring gives (the total, the
  prototype's, the mosaic rules' and L1) are the ones the doctor's source
  marks. The doctor has no rule registry; each rule is introduced by a
  numbered comment ("# 1.", "# 2-4.", "# M6 and M15 ..."), and the removed
  11 and 12 say REMOVED. Those markers are what is counted.

Each mutation was applied to a byte-for-byte copy of ``api/app.py`` in a
private copy of ``server/`` (scratchpad ``s4-routes-mut``), never in the
shared tree; failures are quoted as observed.
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.flows import doctor
from astrodeck.flows.store import FlowStore

#: A runnable graph (a TARGET feeding one capture) and one the compile
#: refuses (a mosaic at "Any angle", M2), so both returns are asked.
RUNNABLE = {"nodes": [
    {"id": "t", "type": "target", "x": 0, "y": 0,
     "params": {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}},
    {"id": "c", "type": "capture", "x": 0, "y": 0,
     "params": {"filter": "L", "exposure": 60, "gain": 100, "bin": "1",
                "count": 3, "goal": 0}}],
    "edges": [{"from": "t", "fromPort": "target", "to": "c", "toPort": "run"}]}
REFUSED = {"nodes": [
    {"id": "t", "type": "target", "x": 0, "y": 0,
     "params": {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
                "rows": 2, "cols": 2, "overlap": 25, "fovX": 2.0,
                "fovY": 1.33, "angle": "Any angle", "rotation": -1}},
    {"id": "c", "type": "capture", "x": 0, "y": 0,
     "params": {"filter": "L", "exposure": 60, "gain": 100, "bin": "1",
                "count": 3, "goal": 0}}],
    "edges": [{"from": "t", "fromPort": "target", "to": "c", "toPort": "run"}]}


def _docstring() -> str:
    """``_compile_payload`` is nested in ``create_app``, so it is read from
    the parsed module rather than an attribute."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(app_module)))
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef)
             and n.name == "_compile_payload"]
    assert len(found) == 1, "premise: one _compile_payload"
    doc = ast.get_docstring(found[0])
    assert doc, "premise: it has a docstring"
    return doc


def _braced(doc: str) -> set[str]:
    """The names in the first line's ``{a, b, ...}``."""
    m = re.match(r"``\{([^}]*)\}``", doc.splitlines()[0].strip())
    assert m, f"premise: the first line braces the keys: {doc.splitlines()[0]}"
    return {k.strip() for k in m.group(1).split(",") if k.strip()}


def _bulleted(doc: str) -> set[str]:
    """The names the bullets describe: each ``* ``name`` - ...`` line."""
    return set(re.findall(r"^\s*\* ``(\w+)``", doc, flags=re.M))


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.setattr(app_module.engine, "_event_costs", {})
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("graph", [RUNNABLE, REFUSED],
                         ids=["runnable", "refused"])
def test_the_docstring_names_every_key_the_answer_carries(client, graph):
    """The first line's names, the bullets' names and the answer's keys are
    one set, for a runnable graph and a refused one.

    RED under mutation "add an answer key without the docstring" (``"notes":
    []`` added to the answer's return), for both graphs, observed:

        AssertionError: the answer carries keys the docstring's first line
        does not name
        assert {'issues', 'n...uctural', ...} == {'issues', 'p...',
        'unmapped'}
          Extra items in the left set:
          'notes'
    """
    r = client.post("/api/flows/compile", json={"graph": graph, "name": "m"})
    assert r.status_code == 200, r.text
    answer = r.json()
    if graph is REFUSED:
        assert answer["unmapped"][0]["key"] == "plan", (
            "premise: the compile refused this graph")
    doc = _docstring()
    assert set(answer) == _braced(doc), (
        "the answer carries keys the docstring's first line does not name")
    assert set(answer) == _bulleted(doc), (
        "the docstring's bullets do not describe exactly the answer's keys")


def _doctor_markers() -> tuple[set[int], set[str], set[str]]:
    """``(prototype rule numbers, M rules, L rules)`` as the doctor's source
    marks them: a numbered comment at the start of a line, ``# 7-8.`` a
    range, one that says REMOVED left out."""
    check = inspect.getsource(doctor.check)
    proto: set[int] = set()
    for line in check.splitlines():
        m = re.match(r"\s*# (\d+)(?:-(\d+))?\. ", line)
        if m and "REMOVED" not in line:
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            proto.update(range(lo, hi + 1))
    mosaic = set(re.findall(r"^\s*# (M\d+)\b",
                            inspect.getsource(doctor._mosaic_rules),
                            flags=re.M))
    late = set(re.findall(r"^\s*# (L\d+)\b", check, flags=re.M))
    return proto, mosaic, late


def test_the_docstring_counts_the_doctors_rules_truthfully():
    """The docstring's numbers are the doctor's: the total, the prototype's
    and which numbers they are, the mosaic rules and their range, and L1.

    RED under mutation "the old count" (the ``issues`` bullet restored to
    "the doctor's ten advisory rules"), observed:

        AssertionError: the docstring does not say "the doctor's 28 rules"
        assert "the doctor's 28 rules" in '``{plan, structural, issues,
        unmapped, readouts, rig}``. ``flow_id`` is the stored flow\\'s id,
        "" for an unsaved draf...n two sentences about one field, and the
        RUN section\\'s hop the one M10 weighed, never two reads taken a
        moment apart.'
    """
    proto, mosaic, late = _doctor_markers()
    assert proto == set(range(1, 11)) | {13, 14}, (
        f"premise: the prototype's markers read {sorted(proto)}")
    m_numbers = sorted(int(m[1:]) for m in mosaic)
    assert m_numbers == list(range(1, len(m_numbers) + 1)), (
        f"premise: the mosaic rules are M1 to Mn, read {m_numbers}")
    total = len(proto) + len(mosaic) + len(late)
    doc = " ".join(_docstring().split())
    for said in (f"the doctor's {total} rules",
                 f"{len(proto)} from the prototype (1 to 10, 13 and 14",
                 f"the {len(mosaic)} mosaic rules M1 to M{m_numbers[-1]}",
                 f"and {', '.join(sorted(late))}"):
        assert said in doc, f'the docstring does not say "{said}"'
