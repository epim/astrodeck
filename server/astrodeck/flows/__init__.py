"""AstroDeck Flows — the node-graph automation surface (design handoff).

A flow is a graph of sources, equipment, rig ops, logic and actions that
COMPILES TO WHAT THE ENGINE ALREADY RUNS: a ``SequencePlan`` (targets → steps)
plus ``Instruction`` when/then rules. It cannot express a capability the server
lacks, which is the property that stops a canvas from promising a night the rig
cannot deliver.

Layout:
  nodes.py   the closed node vocabulary — ports, kinds, default params
  models.py  the persisted graph + library record (graph is the source of truth)
  doctor.py  the ten graph checks, authoritative (the UI keeps a copy for latency)
"""
from .doctor import Issue, check
from .models import EXAMPLES_FOLDER, MY_FLOWS_FOLDER, FlowEdge, FlowGraph, FlowNode, FlowRecord
from .nodes import CATEGORY_TOKEN, NODE_DEFS, PALETTE_GROUPS, default_params, port_kind

__all__ = [
    "CATEGORY_TOKEN", "EXAMPLES_FOLDER", "FlowEdge", "FlowGraph", "FlowNode",
    "FlowRecord", "Issue", "MY_FLOWS_FOLDER", "NODE_DEFS", "PALETTE_GROUPS",
    "check", "default_params", "port_kind",
]
