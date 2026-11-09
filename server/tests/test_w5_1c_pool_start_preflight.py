# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The start preflight keeps a pool's leniency and words it as a pool (WP 5.1c
fix round 1, #183; owner-side ruling of the coordinator).

A pool, like a mosaic, refuses a start only when EVERY member is below the
horizon now; one candidate below it starts and is named. The words say
"candidate of pool" where a mosaic's say "panel of mosaic", in the 409 and in
the night log. The harness, the made-up site and the polar declinations that
make the sky the same at any hour are test_group_start_guard.py's, and each
case runs through both start routes.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.models import SequencePlan, Target, TargetGroup
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_group_start_guard import (DOWN_DEC, ROUTES, UP_DEC,  # noqa: F401
                                    _horizon_lines, _premise, _start, _step,
                                    _won, sky)

POOL, NAME = "g-pool", "Pool of two"


def _candidate(name: str, *, down: bool) -> Target:
    return Target(name=name, ra_hours=1.0, dec_deg=DOWN_DEC if down else UP_DEC,
                  mosaic_group=POOL, steps=[_step()])


def _pool(*down: str) -> SequencePlan:
    names = ("Alpha", "Beta", "Gamma")
    return SequencePlan(
        name="pool", guide=False, dither_every=0, meridian_flip=False,
        targets=[_candidate(n, down=n in down) for n in names],
        groups=[TargetGroup(id=POOL, name=NAME, kind="pool")])


@pytest.mark.parametrize("route", ROUTES)
async def test_every_candidate_below_the_horizon_refuses_as_a_pool(
        sky, bus_lines, route):
    plan = _pool("Alpha", "Beta", "Gamma")
    _premise(plan, {"Alpha", "Beta", "Gamma"})
    r = await _start(sky, route, plan)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["detail"] == (
        f"every candidate of pool '{NAME}' is below the horizon now: "
        f"Alpha, Beta, Gamma"), r.json()
    assert _won(sky) == 0


@pytest.mark.parametrize("route", ROUTES)
async def test_one_candidate_below_the_horizon_starts_and_is_named(
        sky, bus_lines, route):
    plan = _pool("Beta")
    _premise(plan, {"Beta"})
    r = await _start(sky, route, plan)
    assert r.status_code == 200, r.text
    assert _won(sky) == 1
    assert _horizon_lines(bus_lines) == [(
        "info",
        f"pool '{NAME}': candidate Beta is below the horizon now; started, "
        f"since 2 of its 3 candidates are not", "sequence")]


@pytest.mark.parametrize("route", ROUTES)
async def test_a_forced_pool_with_every_candidate_down_says_so_as_a_pool(
        sky, bus_lines, route):
    plan = _pool("Alpha", "Beta", "Gamma")
    r = await _start(sky, route, plan, force=True)
    assert r.status_code == 200, r.text
    assert _horizon_lines(bus_lines) == [(
        "info",
        f"pool '{NAME}': all 3 candidates are below the horizon now "
        f"(Alpha, Beta, Gamma); started because the start was forced",
        "sequence")]
