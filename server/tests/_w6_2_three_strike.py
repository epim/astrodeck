# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The group driver as it ran before owner ruling D-E (WP 6.2, #180 part B):
a panel that fails ``max_failed_visits`` visits in a row is SET ASIDE.

Since D-E a mosaic's panels rotate instead (``GroupRun(rotate=True)``, which
`SequenceEngine._build_groups` passes for ``group.kind == "mosaic"``), but the
set-aside is not gone: a POOL's candidates still take it, and so does a panel
an earlier build left set aside in tonight's session record. Everything that
set-aside feeds is still live code with its own tests: the 45-minute centring
expiry, the ``for_now`` flag in the published group, the operator's retry,
the restart lines, the report's "skipped" line. Those tests reach it through
the harness's mosaic night, which no longer sets a panel aside for its own
failures, so they call :func:`three_strike` to run the night's groups the
old way. It changes ONLY the ``rotate`` argument of the one `GroupRun` the
engine builds per group; no other engine code is replaced.

The rotation itself, and the choice of ``rotate`` for a mosaic, are graded in
test_w6_2_rotation_rules.py and test_w6_2_failed_panel_rotates.py, whose
mutants fail when either is lost.
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence.group_rules import GroupRun


def three_strike(monkeypatch) -> None:
    """Build every group's `GroupRun` with ``rotate`` off for this test."""
    def build(members, **kw):
        return GroupRun(members, **{**kw, "rotate": False})

    monkeypatch.setattr(engine_mod, "GroupRun", build)
