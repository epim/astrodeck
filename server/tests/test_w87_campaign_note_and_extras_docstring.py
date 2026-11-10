# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 8.7 leftovers: the campaign note's level (#829) and the NGC extras
docstring's pinned source (#1158).

A campaign (a pool flow with Automatic resume On) loses nothing: the run goes
dormant, comes back and picks up from the ledger, and ``until=pool_complete``
ends it. Its entry in the unmapped list describes what the run will do, so it
is a ``note``. At ``warn`` it counted in ``losses`` and /start refused until the
operator accepted a loss that is not one.

NAMED MUTANT "the campaign note is a warn again" (``to_plan._automation``: drop
the ``"note"`` level argument of the campaign ``_note``). Observed red:
``assert 'warn' == 'note'`` in ``test_a_campaign_note_is_not_a_loss``.
"""
from __future__ import annotations

from pathlib import Path

from astrodeck.catalog import build_ngc_extras
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.to_plan import losses, to_sequence_plan


def _campaign_rows(example_id: str) -> tuple[list[dict], list[dict]]:
    ex = next(e for e in examples() if e.id == example_id)
    _, un = to_sequence_plan(compile_plan(ex.graph, ex.name), ex.graph)
    return un, [u for u in un if u["key"] == "campaign"]


def test_a_campaign_note_is_not_a_loss():
    for example_id in ("example-campaign", "example-pool"):
        un, rows = _campaign_rows(example_id)
        assert len(rows) == 1, (example_id, [u["key"] for u in un])
        assert rows[0]["level"] == "note"
        assert rows[0] not in losses(un), (
            f"{example_id}: the campaign note would block /start")


def test_a_campaign_note_still_says_what_the_run_will_do():
    _, rows = _campaign_rows("example-pool")
    detail = rows[0]["detail"]
    assert "comes back the next night" in detail
    assert "every target reaches its requested frame count" in detail


def test_the_extras_docstring_does_not_send_anyone_to_master():
    doc = build_ngc_extras.__doc__ or ""
    assert "/master/" not in doc and "curl" not in doc, (
        "the docstring still describes the unpinned manual fetch")
    assert "tools/build_ngc_catalog.py" in doc
    assert Path(build_ngc_extras.__file__).name == "build_ngc_extras.py"
