# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The stacking bundle keeps a mosaic's panels as separate stacks (#188 WP-2).

A mosaic's panels are shot to be STITCHED, never co-added: a stack built from
two panels is a smear. ``build_bundle`` grouped on ``(target, filter,
exposure, gain, binning)``, so the bundle could not tell a panel of one mosaic
from a panel of another that happened to carry the same target name, and put
both in one folder a stacker would open as one stack. The group key now
includes the frame's ``mosaic``, and two groups of one folder name but
different mosaics are told apart in the FOLDER as well as in the key (a
distinct key with the same ``dir`` would merge on disk, which is the very
thing the key exists to prevent).

Mutants (each run from a byte backup of bundle.py, restored byte-identical
and grepped gone; the failure is as pytest printed it):

* ``group key without mosaic``: the key at the grouping loop ends in ``None``
  where it ended in ``fr.mosaic or None``. Reds
  ``test_two_mosaics_with_the_same_panel_names_build_separate_groups``::

      AssertionError: the North and South mosaics' 'Panel 1-1' are two stacks, not one
      At index 0 diff: ('North', '1-1', 3) != ('North', '1-1', 2)
      Right contains one more item: ('South', '1-1', 1)

* ``dir without mosaic``: the shared-folder test is ``gdir in ()``, so no
  folder is ever put under its mosaic. Reds
  ``test_two_mosaics_with_the_same_panel_names_get_separate_folders``::

      AssertionError: two groups share a folder: ['Panel 1-1/Ha/300s_g100_bin1',
      'Panel 1-1/Ha/300s_g100_bin1', 'Panel 1-2/Ha/300s_g100_bin1']

* ``externalize drops mosaic``: ``externalize_bundle`` rebuilds ``Group`` with
  ``mosaic=None, panel=None``. Reds ``test_the_labels_survive_externalizing``::

      assert [(None, None)... (None, None)] == [('North', '1...outh', '1-1')]
      At index 0 diff: (None, None) != ('North', '1-1')

* ``csv labels not last``: the two columns are inserted before ``dest``. Reds
  ``test_the_csv_carries_mosaic_and_panel_as_its_last_columns``::

      assert ['mosaic', 'p...'dest', 'src'] == ['dest', 'src...aic', 'panel']
      At index 0 diff: 'mosaic' != 'dest'
"""
from __future__ import annotations

import csv
import io

from astrodeck.sequence.bundle import (NullMasterLibrary, build_bundle,
                                       build_script, bundle_summary,
                                       externalize_bundle, manifest_json,
                                       readme_text, weights_csv)
from astrodeck.sequence.report import FrameRecord, SessionReport


def _fr(ts, target, mosaic=None, panel=None, name=None):
    return FrameRecord(ts=ts, target=target, filter="Ha", frame_type="Light",
                       exposure_s=300.0, gain=100, binning=1, hfr=2.0,
                       saved_path=f"/cap/{name or f'{target}-{ts:g}'}.fits",
                       mosaic=mosaic, panel=panel)


def _bundle(frames, layout="grouped"):
    rep = SessionReport(id="r1", plan_name="P", frames=frames)
    return build_bundle(rep, NullMasterLibrary(), is_local=lambda p: True,
                        layout=layout)


def _two_mosaics_same_panel_names():
    """Two mosaics whose panels carry the same target names, which a plan can
    produce by naming its panels by hand. Same filter, exposure, gain and
    binning: only ``mosaic`` tells the stacks apart."""
    return [_fr(1, "Panel 1-1", "North", "1-1"),
            _fr(2, "Panel 1-1", "South", "1-1"),
            _fr(3, "Panel 1-1", "North", "1-1"),
            _fr(4, "Panel 1-2", "North", "1-2")]


# ---------------------------------------------------------------- the grouping

def test_two_mosaics_with_the_same_panel_names_build_separate_groups():
    b = _bundle(_two_mosaics_same_panel_names())
    got = sorted((g.mosaic, g.panel, len(g.lights)) for g in b.groups)
    assert got == [("North", "1-1", 2), ("North", "1-2", 1),
                   ("South", "1-1", 1)], (
        "the North and South mosaics' 'Panel 1-1' are two stacks, not one")


def test_two_mosaics_with_the_same_panel_names_get_separate_folders():
    """A distinct key is not enough if both groups land in one folder: the
    build script copies a group's lights into its ``lights/``, so equal
    ``dir`` merges the stacks on disk."""
    b = _bundle(_two_mosaics_same_panel_names())
    dirs = [g.dir for g in b.groups]
    assert len(set(dirs)) == len(dirs), f"two groups share a folder: {dirs}"
    dests = [l.dest for g in b.groups for l in g.lights]
    assert len(set(dests)) == len(dests)
    north = next(g for g in b.groups if g.mosaic == "North" and g.panel == "1-1")
    south = next(g for g in b.groups if g.mosaic == "South")
    assert north.dir.startswith("North/") and south.dir.startswith("South/")
    # No clash, no prefix: a mosaic whose panels are named apart keeps the
    # folder layout every existing bundle has.
    other = next(g for g in b.groups if g.panel == "1-2")
    assert other.dir == "Panel 1-2/Ha/300s_g100_bin1"


def test_a_clash_with_a_non_member_keeps_the_non_member_folder_plain():
    b = _bundle([_fr(1, "Panel 1-1", "North", "1-1"), _fr(2, "Panel 1-1")])
    plain = next(g for g in b.groups if g.mosaic is None)
    member = next(g for g in b.groups if g.mosaic == "North")
    assert plain.dir == "Panel 1-1/Ha/300s_g100_bin1"
    assert member.dir == "North/Panel 1-1/Ha/300s_g100_bin1"


def test_a_frame_without_labels_groups_as_a_plain_target_as_before():
    b = _bundle([_fr(1, "M42"), _fr(2, "M42")])
    (g,) = b.groups
    assert (g.mosaic, g.panel) == (None, None)
    assert g.dir == "M42/Ha/300s_g100_bin1" and len(g.lights) == 2


# ------------------------------------------------------------- the serializers

def test_the_manifest_and_the_summary_carry_the_labels():
    b = _bundle(_two_mosaics_same_panel_names())
    for rows in (manifest_json(b)["groups"], bundle_summary(b)["groups"]):
        got = sorted((r["mosaic"], r["panel"]) for r in rows)
        assert got == [("North", "1-1"), ("North", "1-2"), ("South", "1-1")]
    plain = _bundle([_fr(1, "M42")])
    assert manifest_json(plain)["groups"][0]["mosaic"] is None
    assert bundle_summary(plain)["groups"][0]["panel"] is None


def test_the_labels_survive_externalizing():
    """Every bundle that leaves the process is externalized, which rebuilds
    each ``Group``: a field it does not copy is gone from the download."""
    b = externalize_bundle(_bundle(_two_mosaics_same_panel_names()),
                           lambda p: p.rsplit("/", 1)[-1])
    got = sorted((g.mosaic, g.panel) for g in b.groups)
    assert got == [("North", "1-1"), ("North", "1-2"), ("South", "1-1")]
    assert sorted((r["mosaic"], r["panel"])
                  for r in manifest_json(b)["groups"]) == got


def test_the_csv_carries_mosaic_and_panel_as_its_last_columns():
    """Appended LAST, so a consumer that reads the columns by position keeps
    reading what it read (``dest`` and ``src`` stay where they were)."""
    rows = list(csv.reader(io.StringIO(weights_csv(
        _bundle(_two_mosaics_same_panel_names())))))
    assert rows[0][-4:] == ["dest", "src", "mosaic", "panel"]
    by = [dict(zip(rows[0], r)) for r in rows[1:]]
    assert sorted((r["mosaic"], r["panel"]) for r in by) == [
        ("North", "1-1"), ("North", "1-1"), ("North", "1-2"),
        ("South", "1-1")]
    plain = list(csv.reader(io.StringIO(weights_csv(_bundle([_fr(1, "M42")])))))
    assert plain[1][-2:] == ["", ""], "None is a blank, as every other column"


def test_the_readme_says_panels_are_separate_stacks_only_when_there_are_any():
    mosaic = readme_text(_bundle(_two_mosaics_same_panel_names()))
    assert "separate stacks" in mosaic and "never co-add" in mosaic
    assert "mosaic South panel 1-1" in mosaic, "the Groups list names them"
    plain = readme_text(_bundle([_fr(1, "M42")]))
    assert "mosaic" not in plain.lower() and "co-add" not in plain


def test_the_build_script_still_lays_each_panel_in_its_own_folder():
    script = build_script(_bundle(_two_mosaics_same_panel_names()), "sh")
    assert "North/Panel 1-1" in script and "South/Panel 1-1" in script
