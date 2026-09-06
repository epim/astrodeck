"""GN-09: a mount whose unguided tracking cannot hold a sub of ordinary length.

At 03:04 on 2026-09-06 the flow was switched to UNGUIDED because the guider was
misbehaving. On the ZWO AM5 -- a harmonic drive with large periodic error --
every unguided 60 s sub afterward trailed by ~15 px (fixture
server/tests/fixtures/ngc604_20260906/pedrift_L60.fits.gz, header VERDICT
"trailed"). The doctor's existing rule (`flows/doctor.py` ~130) only warns
about a capture stage with no GUIDE upstream once its longest sub reaches
120 s, so a 60 s unguided cycle on THIS mount passed clean.

The fix is a capability flag, ``Telescope.needs_guiding``, read by the doctor
through a new ``mount=`` keyword -- keyword and optional, like GN-04's
``standards=``, so every existing caller sees exactly what it saw before.
"""
from __future__ import annotations

from astrodeck.devices.backends.zwo_am5 import ZwoAm5Telescope
from astrodeck.devices.base import Telescope
from astrodeck.devices.sim import SimTelescope
from astrodeck.flows.doctor import check
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode


def _n(nid: str, ntype: str, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


class FakeMount:
    """A stand-in for a connected ``Telescope`` -- the doctor only ever reads
    ``needs_guiding`` and ``name`` off whatever ``mount=`` is handed, so a
    real driver instance would be exercising nothing this test doesn't."""

    def __init__(self, needs_guiding: bool = False, name: str = "this mount"):
        self.needs_guiding = needs_guiding
        self.name = name


def _capture_graph(exposure: int) -> FlowGraph:
    """One CAPTURE stage, no wires at all -- so no GUIDE upstream."""
    return FlowGraph(nodes=[_n("c", "capture", exposure=exposure)], edges=[])


def _capture_graph_with_guide(exposure: int) -> FlowGraph:
    return FlowGraph(
        nodes=[_n("g", "guide"), _n("c", "capture", exposure=exposure)],
        edges=[_e("g", "guiding", "c", "run")])


def _needs_guiding_issues(issues) -> list[str]:
    return [i.text for i in issues if "needs guiding" in i.text]


def _trail_at_any_focal_length_issues(issues) -> list[str]:
    return [i.text for i in issues if "any real focal length" in i.text]


# ------------------------------------------------------------------- part a

def test_unguided_60s_stage_is_quiet_with_no_mount():
    graph = _capture_graph(60)
    assert _needs_guiding_issues(check(graph)) == []


def test_unguided_60s_stage_warns_on_a_needs_guiding_mount():
    graph = _capture_graph(60)
    mount = FakeMount(needs_guiding=True, name="AM5N")
    said = _needs_guiding_issues(check(graph, mount=mount))
    assert len(said) == 1, said
    assert "needs guiding" in said[0]
    assert "AM5N" in said[0]
    assert "60" in said[0]


def test_a_wired_guide_node_clears_the_warning():
    graph = _capture_graph_with_guide(60)
    mount = FakeMount(needs_guiding=True, name="AM5N")
    assert _needs_guiding_issues(check(graph, mount=mount)) == []


# ------------------------------------------------------------------- part b

def test_the_needs_guiding_line_replaces_rule_2_not_doubles_it():
    graph = _capture_graph(300)
    mount = FakeMount(needs_guiding=True, name="AM5N")
    issues = check(graph, mount=mount)
    assert len(_needs_guiding_issues(issues)) == 1
    assert _trail_at_any_focal_length_issues(issues) == []


def test_rule_2_still_fires_alone_with_no_mount():
    graph = _capture_graph(300)
    issues = check(graph)
    assert _needs_guiding_issues(issues) == []
    assert len(_trail_at_any_focal_length_issues(issues)) == 1


def test_a_mount_that_does_not_need_guiding_leaves_rule_2_alone():
    graph = _capture_graph(300)
    mount = FakeMount(needs_guiding=False, name="EQ6-R")
    issues = check(graph, mount=mount)
    assert _needs_guiding_issues(issues) == []
    assert len(_trail_at_any_focal_length_issues(issues)) == 1


# ------------------------------------------------------------------- part c

def test_the_capability_flag_defaults_off_and_is_set_on_the_two_mounts_that_need_it():
    assert Telescope.needs_guiding is False
    assert ZwoAm5Telescope.needs_guiding is True
    assert SimTelescope.needs_guiding is True
