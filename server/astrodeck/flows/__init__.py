# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""AstroDeck Flows — the node-graph automation surface (design handoff).

A flow is a graph of sources, equipment, rig ops, logic and actions that
COMPILES TO WHAT THE ENGINE ALREADY RUNS: a ``SequencePlan`` (targets → steps)
plus ``Instruction`` when/then rules. It cannot express a capability the server
lacks, which is the property that stops a canvas from promising a night the rig
cannot deliver.

Layout:
  nodes.py   the closed node vocabulary — ports, kinds, missing-key defaults,
             the Created-as column, and the legacy types the palette hides
  models.py  the persisted graph + library record (graph is the source of
             truth), and the flow-level settings table
  rig.py     the rig facts a route hands a pure rule (field, hop, rotator)
  doctor.py  the graph checks, authoritative (the UI renders what it returns)
"""
from .doctor import Issue, check
from .models import (
    EXAMPLES_FOLDER, FLOW_SETTINGS, MY_FLOWS_FOLDER, FlowEdge, FlowGraph, FlowNode,
    FlowRecord, resolve_setting)
from .nodes import (
    CATEGORY_TOKEN, LEGACY_TYPES, NODE_DEFS, PALETTE_GROUPS, create_params,
    default_params, port_kind)
from .rig import RigFacts

__all__ = [
    "CATEGORY_TOKEN", "EXAMPLES_FOLDER", "FLOW_SETTINGS", "FlowEdge", "FlowGraph",
    "FlowNode", "FlowRecord", "Issue", "LEGACY_TYPES", "MY_FLOWS_FOLDER",
    "NODE_DEFS", "PALETTE_GROUPS", "RigFacts", "check", "create_params",
    "default_params", "port_kind", "resolve_setting",
]
