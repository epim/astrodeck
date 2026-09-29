"""One overlap constant: the UI's ``DEFAULT_OVERLAP`` is the server's
``framing.DEFAULT_OVERLAP``, and every UI reader the spec names reads it
(spec 2026-09-23 flows mosaic, 2.4 GRID: "one server constant, exported by
``framing.py`` and replacing both the 0.25 in ``store.ts`` and the 0.15 in
``next/hubs/sky/frame/mosaic.ts``"; section 8 S6, "One overlap constant";
#196).

Before S6 there were three numbers. ``store.openFraming`` seeded 0.25, the
Sky hub's FRAME mode re-set every session to 0.15 (``mosaic.ts``'s
``OVERLAP``, "not a default, a correction", so that the framing card's copy
"Panels overlap 15%" and its pitch agreed), and the Target modal read a
missing overlap as 25%. A Sky framing sent to Send to Flow Wizard then
arrived at 15% while the same object framed in the Atlas arrived at 25%.

What this file holds, reading the UI's sources as text (the UI has no
Python to import):

* ``ui/src/lib/framing.ts`` declares ``export const DEFAULT_OVERLAP =
  <number>;`` exactly once, and the number is the server's.
* Each reader the acceptance names imports that constant from
  ``lib/framing`` and writes no overlap number of its own: the store's seed
  (``openFraming``), ``mosaic.ts`` (whose ``OVERLAP`` is the constant
  re-exported, and whose defaults read it), ``SkyHub.tsx``'s FRAME resets,
  ``next/lib/fov.ts``'s ``mosaicPitch`` default and the modal's missing-key
  reading (``framingModel.layoutOf``).

The UI's own tests hold the same readers by behaviour
(``src/__tests__/doorsConverge.test.tsx``,
``next/hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts``); this file is the
one that can see the server's number.

Each matcher is checked against a known-good and a known-bad line first, so
none can pass by matching nothing.

Every named mutation was run in a private copy of ``server/`` and ``ui/``
(the session scratchpad's ``S6-DOORS-mut``, never the shared tree, #254),
output verbatim:

    MUTANT "mosaic.ts keeps 0.15" (the acceptance's named mutant; its own
    `export const OVERLAP = 0.15;` and defaults reading it):
        FAILED tests/test_overlap_constant_one.py::test_each_reader_imports_the_constant_and_writes_no_number[next/hubs/sky/frame/mosaic.ts]
        FAILED tests/test_overlap_constant_one.py::test_each_reader_uses_the_constant_where_the_spec_says
        E       AssertionError: next/hubs/sky/frame/mosaic.ts (the Sky hub's constant and its defaults) writes an overlap number of its own: ['OVERLAP = 0.15']
        E       AssertionError: mosaic.ts's OVERLAP is not DEFAULT_OVERLAP re-exported: the Sky keeps a number of its own
        2 failed, 6 passed

    MUTANT "SkyHub keeps 0.15" (enterFrame's reset):
        E       AssertionError: next/hubs/sky/SkyHub.tsx (the Sky hub's FRAME resets) writes an overlap number of its own: ['overlap: 0.15']
        E       AssertionError: SkyHub.tsx's FRAME resets do not all read DEFAULT_OVERLAP: ['0.15', 'DEFAULT_OVERLAP', 'DEFAULT_OVERLAP']
        2 failed, 6 passed

    MUTANT "store keeps its own 0.25" (the same value, a second constant; the UI
    tests stay green, doorsConverge 8/8):
        E       AssertionError: store.ts (the store's openFraming seed) writes an overlap number of its own: ['overlap: 0.25']
        E       AssertionError: store.openFraming does not seed the mosaic at DEFAULT_OVERLAP
        2 failed, 6 passed

    MUTANT "fov.mosaicPitch keeps the README's 0.15":
        E       AssertionError: next/lib/fov.ts (fov.mosaicPitch's default) writes an overlap number of its own: ['overlap = 0.15']
        E       AssertionError: fov.mosaicPitch's default overlap is not DEFAULT_OVERLAP
        2 failed, 6 passed

    MUTANT "the modal keeps its own missing-key 25" (the same value; UI green):
        E       AssertionError: components/flows/framing/framingModel.ts (the modal's missing-key overlap) writes an overlap number of its own: ['? pct : 25']
        E       AssertionError: framingModel.layoutOf does not read a missing overlap as DEFAULT_OVERLAP
        2 failed, 6 passed

    MUTANT "the UI constant drifts" (lib/framing.ts 0.25 -> 0.2):
        E       AssertionError: the UI's DEFAULT_OVERLAP is 0.2, the server's is 0.25: a framing would arrive at one overlap and be laid out at another
        FAILED tests/test_overlap_constant_one.py::test_the_ui_constant_is_the_server_constant
        1 failed, 7 passed
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from astrodeck.catalog import framing

UI_SRC = Path(__file__).resolve().parents[2] / "ui" / "src"
FRAMING_TS = UI_SRC / "lib" / "framing.ts"

#: The one declaration, whole-line, as lib/framing.ts's own comment promises
#: to keep it.
DECLARATION = re.compile(
    r"^export const DEFAULT_OVERLAP = (?P<value>\d+(?:\.\d+)?|\.\d+);[ \t]*\r?$",
    re.MULTILINE)

#: An import of the constant from lib/framing, however deep the relative path.
IMPORTS_CONSTANT = re.compile(
    r"import\s*\{[^}]*\bDEFAULT_OVERLAP\b[^}]*\}\s*from\s*\"(?:\./|(?:\.\./)+)lib/framing\"")

#: An overlap written as a number: `overlap: 0.15`, `overlap = 0.25` (a
#: parameter default), `OVERLAP = 0.15`, or the modal's old `: 25)` fallback
#: after the overlap's own finiteness test.
OWN_NUMBER = re.compile(
    r"\b(?:overlap|OVERLAP)\s*[:=]\s*\d+(?:\.\d+)?|\?\s*pct\s*:\s*\d+(?:\.\d+)?")

#: The readers the acceptance names, relative to ui/src.
READERS = {
    "store.ts": "the store's openFraming seed",
    "next/hubs/sky/frame/mosaic.ts": "the Sky hub's constant and its defaults",
    "next/hubs/sky/SkyHub.tsx": "the Sky hub's FRAME resets",
    "next/lib/fov.ts": "fov.mosaicPitch's default",
    "components/flows/framing/framingModel.ts": "the modal's missing-key overlap",
}


def _code(src: str) -> str:
    """The source with its comments removed (block, then line; a `//` right
    after a colon or a quote is a URL, kept), so a comment that NAMES the old
    numbers, as the history in these files does, is not read as code."""
    src = re.sub(r"/\*[\s\S]*?\*/", " ", src)
    return re.sub(r"(?<![:\"'\\])//[^\n]*", "", src)


def _read(rel: str) -> str:
    return (UI_SRC / rel).read_text(encoding="utf-8")


def test_the_matchers_see_what_they_are_for():
    """Known-good and known-bad lines, so no matcher passes on nothing."""
    assert DECLARATION.search("export const DEFAULT_OVERLAP = 0.25;\n")
    assert DECLARATION.search("export const DEFAULT_OVERLAP = 0.25;\r\n")
    assert not DECLARATION.search("export const DEFAULT_OVERLAP = 25 / 100;\n")
    assert IMPORTS_CONSTANT.search('import { DEFAULT_OVERLAP } from "./lib/framing";')
    assert IMPORTS_CONSTANT.search(
        'import { DEFAULT_OVERLAP, mosaicGrid } from "../../../../lib/framing";')
    assert not IMPORTS_CONSTANT.search('import { DEFAULT_OVERLAP } from "./lib/other";')
    for bad in ("mosaic: { rows: 1, cols: 1, overlap: 0.25 },",
                "export const OVERLAP = 0.15;",
                "  overlap = 0.15,",
                "(Number.isFinite(pct) ? pct : 25) / 100"):
        assert OWN_NUMBER.search(bad), bad
    for good in ("mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP },",
                 "  overlap = DEFAULT_OVERLAP,",
                 "overlap: framing.mosaic.overlap",
                 "Number.isFinite(pct) ? pct / 100 : DEFAULT_OVERLAP"):
        assert not OWN_NUMBER.search(good), good
    assert "0.15" not in _code("const a = 1; // was 0.15\n/* 0.25 */ const b = 2;\n")
    assert "http://x" in _code('const u = "http://x";\n')


def test_the_ui_constant_is_the_server_constant():
    """lib/framing.ts declares DEFAULT_OVERLAP once, as the server's number.

    Mutant "UI constant drifts" (lib/framing.ts: 0.25 -> 0.2) is recorded in
    the module docstring."""
    src = FRAMING_TS.read_text(encoding="utf-8")
    found = DECLARATION.findall(src)
    assert len(found) == 1, (
        f"lib/framing.ts must declare `export const DEFAULT_OVERLAP = <number>;` "
        f"exactly once; found {len(found)}")
    assert float(found[0]) == pytest.approx(framing.DEFAULT_OVERLAP, abs=0), (
        f"the UI's DEFAULT_OVERLAP is {found[0]}, the server's is "
        f"{framing.DEFAULT_OVERLAP}: a framing would arrive at one overlap and "
        f"be laid out at another")


@pytest.mark.parametrize("rel", sorted(READERS))
def test_each_reader_imports_the_constant_and_writes_no_number(rel):
    """Every reader the acceptance names imports DEFAULT_OVERLAP from
    lib/framing and writes no overlap number of its own."""
    code = _code(_read(rel))
    assert IMPORTS_CONSTANT.search(code), (
        f"{rel} ({READERS[rel]}) does not import DEFAULT_OVERLAP from lib/framing")
    own = [m.group(0) for m in OWN_NUMBER.finditer(code)]
    assert own == [], (
        f"{rel} ({READERS[rel]}) writes an overlap number of its own: {own}")


def test_each_reader_uses_the_constant_where_the_spec_says():
    """The specific places, so an import left unused cannot pass."""
    store = _code(_read("store.ts"))
    open_framing = store[store.index("openFraming: (e) =>"):store.index("setFraming: (patch) =>")]
    assert "mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP }" in open_framing, (
        "store.openFraming does not seed the mosaic at DEFAULT_OVERLAP")

    mosaic = _code(_read("next/hubs/sky/frame/mosaic.ts"))
    assert "export { DEFAULT_OVERLAP as OVERLAP }" in mosaic, (
        "mosaic.ts's OVERLAP is not DEFAULT_OVERLAP re-exported: the Sky keeps a "
        "number of its own")
    assert mosaic.count("overlap = DEFAULT_OVERLAP") == 2, (
        "framingMeta's and panelRects's defaults do not both read DEFAULT_OVERLAP")

    sky = _code(_read("next/hubs/sky/SkyHub.tsx"))
    resets = re.findall(r"mosaic: \{ rows: 1, cols: 1, overlap: ([^}]+?) \}", sky)
    assert resets and all(r == "DEFAULT_OVERLAP" for r in resets), (
        f"SkyHub.tsx's FRAME resets do not all read DEFAULT_OVERLAP: {resets}")
    assert len(resets) == 3, f"expected SkyHub's three resets, found {len(resets)}"

    fov = _code(_read("next/lib/fov.ts"))
    assert re.search(r"mosaicPitch\([^)]*overlap = DEFAULT_OVERLAP\)", fov), (
        "fov.mosaicPitch's default overlap is not DEFAULT_OVERLAP")

    model = _code(_read("components/flows/framing/framingModel.ts"))
    assert "Number.isFinite(pct) ? pct / 100 : DEFAULT_OVERLAP" in model, (
        "framingModel.layoutOf does not read a missing overlap as DEFAULT_OVERLAP")
