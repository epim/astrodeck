# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Rule 14 pin for WP 6.1 (#187): a flow that does not choose "Measure at the
first solve" compiles to the plan it compiled to before the choice existed.

The hashes below were taken on origin/main 8f0da771, the tree this work was
branched from, BEFORE any edit: the sha256 of the canonical JSON (sorted
keys, instruction ids blanked, floats rounded to 9 decimals with no negative
zero, as ``_rounded`` in test_w5_1b2_chain_phases.py does, because a mosaic's
geometry differs in its last bit between the Windows and the Linux C library)
of ``compile_plan`` and of ``to_sequence_plan(..., flow_id=...)``.

It covers a single target at any angle, a stage-only flow, a camera-fixed
grid with a skipped panel, a rotating grid with a loop wire, a grid whose
stored anchor carried a nudge, and every shipped example.
"""
from __future__ import annotations

import hashlib
import json

from astrodeck.flows import save_rules
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import create_params
from astrodeck.flows.to_plan import to_sequence_plan

WHEN = 1788313689.0

TARGET = {"name": "M33 - Triangulum", "ra": "01h 33m 51s",
          "dec": "+30 39 37", "rotation": -1}

GRID = {"rows": 3, "cols": 2, "overlap": 25, "fovX": 2.0, "fovY": 1.33}


def _graph(nodes, edges, over) -> FlowGraph:
    return FlowGraph(
        nodes=[FlowNode(id=i, type=t, x=float(x), y=float(y),
                        params={**create_params(t), **over.get(i, {})})
               for i, t, x, y in nodes],
        edges=[FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})
               for a, ap, b, bp in edges])


def _lane(target_params: dict, *, loop: bool = False) -> FlowGraph:
    edges = [("n2", "target", "n7", "run")]
    if loop:
        edges.append(("n7", "pass", "n2", "next"))
    return _graph([("n2", "target", 30, 100), ("n7", "cycle", 300, 100)],
                  edges,
                  {"n2": {**TARGET, **target_params},
                   "n7": {"plan": "L 60, R 60", "cycles": 3}})


def _anchored_nudge() -> FlowGraph:
    """A grid saved at 30 deg whose stored anchor then carried a 1 deg nudge:
    the ids are the anchor's, the layout is the nudged one."""
    params = {**TARGET, **GRID, "angle": "Camera fixed at PA",
              "rotation": 30, "passes": 1}
    anchor, _at = save_rules.current_anchor(
        {**create_params("target"), **params})
    return _lane({**params, "rotation": 31, "frameAnchor": anchor})


def _rounded(value):
    if isinstance(value, float):
        return round(value, 9) + 0.0
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def _hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(_rounded(obj), sort_keys=True).encode()).hexdigest()


def digest(g: FlowGraph, flow_id: str) -> dict[str, str]:
    compiled = compile_plan(g, "x")
    plan, _ = to_sequence_plan(compiled, g, flow_id=flow_id, when=WHEN)
    dumped = plan.model_dump(mode="json")
    for ins in dumped.get("instructions", []):
        ins["id"] = ""
    return {"compile": _hash(compiled), "plan": _hash(dumped)}


def graphs() -> dict[str, FlowGraph]:
    out = {
        "single_any": _lane({}),
        "single_fixed": _lane({"angle": "Camera fixed at PA",
                               "rotation": 12}),
        "grid_fixed_skip": _lane({**GRID, "angle": "Camera fixed at PA",
                                  "rotation": 37, "skip": "3-2",
                                  "passes": 1}),
        "grid_rotate_loop": _lane({**GRID, "angle": "Rotate to PA",
                                   "rotation": 25, "passes": 2}, loop=True),
        "grid_anchored_nudge": _anchored_nudge(),
    }
    for ex in examples():
        out[f"example-{ex.id}"] = ex.graph
    return out


#: Taken on 8f0da771 before any edit of WP 6.1.
BASE_HASHES: dict[str, dict[str, str]] = {
    "example-example-campaign": {
        "compile": "e1a4c3cd7a61831f9d5b50da5e849713106866d04ce9e61176ac1faa9f5884d9",
        "plan": "4b56393dc85e8188722200fed3ccfc2cf726adc6a11d5fb8dc719c3671488273"},
    "example-example-cycle": {
        "compile": "8ca5a6cb37c853918d464b72bb808110f8f852787516e7f3cb09deb0db485769",
        "plan": "6b2d73325c79cd8680e37ecdaec501c6ffcfd3dc680958c75c2edea32e138e49"},
    "example-example-eaa": {
        "compile": "d0c37907546b85f655dcbaebaf1b9cc1169eeac76f398e66277d886e69ed50a4",
        "plan": "0afee6d6ff7b7106660cfc5141a69dfe378dfe7a8240acf1ececa5279bce41bb"},
    "example-example-m16": {
        "compile": "189606a68961c78ba9066f16758574f472dd1e40a4ba92a73ef2f6a82942c8cd",
        "plan": "44b5e5d34455e0d0753ed8d984e708e690c5570e2c103d3b31658277143e2bfa"},
    "example-example-m31": {
        "compile": "2fd647d2f7cfc9552583a56cc9bdb4a7cb7910d9d76ef90abe0ee221a31e919a",
        "plan": "08c29a83b72c0ef24904a02a7cbc7fd696ee68dfda4f87a9b879e8854be37c26"},
    "example-example-m31-mosaic": {
        "compile": "43f6b6fb59ad83b8b20bafaafa8a769eda8298fe480cbe9a34af3f6f2d3a2fc3",
        "plan": "c66388d678c9a4fe652e31ab18dff2fd0bd6c653f2e7151f6dfce1cc05abdd94"},
    "example-example-nb": {
        "compile": "d7ae264de5bda85618fa5ea3369f8d2f182e6a12d1cb9ac9dffc1c5fc7e9e3e7",
        "plan": "733c09c70813735506d6e56db9d99e3c8d427b8b05cb79ba38d6e1c180d405cc"},
    "example-example-pool": {
        "compile": "28d8f55789fba03c8e08c750149bf8cd9f533bd5dbec0cd1628ed85b8e67fd4a",
        "plan": "dd602fa207b295e2db1e53e5e0e2bf1ead4791e40ebd94158adb3cf5865abeca"},
    "grid_anchored_nudge": {
        "compile": "e78b55909a276ab3bf5b0df5055e036d24aaf76885fc2b98f3089fab8d042a01",
        "plan": "c96b6e0e8cf7eb021931678e324a129baa2a469333ec3468b8fb13f6e9aa8481"},
    "grid_fixed_skip": {
        "compile": "14ab1217e598a3deb1da3964ec206103349c95d48b1244b110496c071c202514",
        "plan": "655f2e6a5f78d839dc2a78df0a0e3975d7b7e8b482af1893980b0ca1f7d4ef6c"},
    "grid_rotate_loop": {
        "compile": "719eb9ec5af4f86ce734c632a16fd44f11e61094e82978e433e736351cabecf3",
        "plan": "aba0bb22370ded1d40215a6b900372fda81d106a99fd66e7d705288292336eb8"},
    "single_any": {
        "compile": "e604c2cbaaff4e6d4deb7852a59da74715a860acb8757f6d0ff1849a74e8e8db",
        "plan": "74606cf1cc4ef33ffb0d88afa9dbdcb2f9b31ea9a01d53d624a4132bd2c82390"},
    "single_fixed": {
        "compile": "ec18d69de5e8dfa3c9c535963b73dbb7701c0854935228ebfa703865fbd29741",
        "plan": "af3054a00be9bd116baaf5ad3a2d46b2c5c0323e3024248c41601a1a058d7acc"},
}


def test_flows_that_do_not_choose_the_measure_angle_compile_as_before():
    got = {name: digest(g, f"f-{name}") for name, g in graphs().items()}
    assert got == BASE_HASHES


if __name__ == "__main__":
    print(json.dumps({n: digest(g, f"f-{n}") for n, g in graphs().items()},
                     indent=4, sort_keys=True))
