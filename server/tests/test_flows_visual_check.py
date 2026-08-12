"""The blank-page guard in the Flows visual-parity harness.

``scripts/flows_visual_check.py`` is what the handoff's "Verify by looking"
protocol runs, and its fourth gate decodes the captured PNG and measures it.
That gate is the only one that can catch an SVG label which measures fine in the
DOM and paints nothing — so if its decoder is wrong, the harness reports parity
on a blank page and every later screen is verified against nothing.

The decoder is hand-written (there is no Pillow in the interpreter that has
Playwright, and the repo's UI harness is deliberately dependency-free), which is
exactly why it needs a test rather than trust. Every PNG row filter gets
exercised here, not just the one Playwright happens to emit today.

Imported by path because the script lives at the repo root, outside the
``astrodeck`` package — the same reach-outside that ``test_routes_have_callers``
makes into ``ui/``.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "flows_visual_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("flows_visual_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolves its annotations through sys.modules, so a module
    # loaded by spec alone raises AttributeError on the first @dataclass.
    sys.modules["flows_visual_check"] = mod
    spec.loader.exec_module(mod)
    return mod


fvc = _load()


def _png(w: int, h: int, pixel, filter_type: int = 0) -> bytes:
    """An 8-bit RGB PNG built with a chosen row filter."""
    raw = bytearray()
    prev = bytearray(w * 3)
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row += bytes(pixel(x, y))
        if filter_type == 0:
            enc = row
        elif filter_type == 1:
            enc = bytearray((row[i] - (row[i - 3] if i >= 3 else 0)) & 0xFF
                            for i in range(len(row)))
        elif filter_type == 2:
            enc = bytearray((row[i] - prev[i]) & 0xFF for i in range(len(row)))
        elif filter_type == 3:
            enc = bytearray(
                (row[i] - (((row[i - 3] if i >= 3 else 0) + prev[i]) >> 1)) & 0xFF
                for i in range(len(row)))
        else:
            enc = bytearray()
            for i in range(len(row)):
                a = row[i - 3] if i >= 3 else 0
                c = prev[i - 3] if i >= 3 else 0
                enc.append((row[i] - fvc._paeth(a, prev[i], c)) & 0xFF)
        raw += bytes([filter_type]) + enc
        prev = row

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


def _gradient(x: int, y: int) -> tuple[int, int, int]:
    return ((x * 7) % 256, (y * 11) % 256, (x * y) % 256)


class TestTheDecoder:
    @pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
    def test_every_png_row_filter_decodes_to_the_source_pixels(self, filter_type):
        """Playwright emits whichever filter zlib picks per row, so a decoder
        that only handled None would work until the day a screenshot happened to
        compress differently — and then report a garbage image as blank."""
        w, h = 23, 17
        gw, gh, ch, buf = fvc.decode_png(_png(w, h, _gradient, filter_type))
        assert (gw, gh, ch) == (w, h, 3)
        expected = bytes(b for y in range(h) for x in range(w)
                         for b in _gradient(x, y))
        assert bytes(buf) == expected

    def test_a_format_it_cannot_read_RAISES(self):
        """Rather than returning something plausible — a decoder that quietly
        mishandled a format would make the blankness verdict report whatever the
        bug produced, which is worse than not checking at all."""
        with pytest.raises(ValueError):
            fvc.decode_png(b"\xff\xd8\xff\xe0 not a png")


class TestTheBlankVerdict:
    def test_a_flat_background_is_refused(self):
        px = fvc.measure(_png(120, 80, lambda x, y: (6, 7, 11)))
        assert px.verdict() is not None

    def test_a_white_flash_is_refused(self):
        px = fvc.measure(_png(120, 80, lambda x, y: (255, 255, 255)))
        assert px.verdict() is not None

    def test_a_page_with_real_content_passes(self):
        px = fvc.measure(_png(120, 80, _gradient))
        assert px.verdict() is None

    def test_a_page_that_is_almost_all_background_is_refused(self):
        """The shape of "the panel rendered but its contents did not"."""
        def sparse(x, y):
            return (232, 236, 247) if (x == 5 and y == 5) else (6, 7, 11)
        assert fvc.measure(_png(200, 120, sparse)).verdict() is not None


class TestReferenceMetadata:
    def test_the_bundled_references_are_measured_not_assumed(self):
        """Nine of the thirteen files in screenshots/ are JPEGs with a .png
        extension, and the set is 924x540 while this harness captures at
        1440x900 / 820x1180 / 390x844. Both facts are recorded so nobody writes
        a numeric diff that could only ever produce noise."""
        refs = sorted((REPO / "design_handoff_astrodeck_flows" /
                       "screenshots").glob("*.png"))
        assert refs, "the reference bundle is missing"
        formats = {fvc.image_info(p.read_bytes())[0] for p in refs}
        assert formats <= {"PNG", "JPEG"}
        for p in refs:
            fmt, w, h = fvc.image_info(p.read_bytes())
            assert w > 0 and h > 0, f"{p.name} has no readable dimensions"


class TestTheStateTable:
    def test_every_state_names_a_reference_that_exists(self):
        for s in fvc.STATES:
            assert (REPO / "design_handoff_astrodeck_flows" / "screenshots"
                    / s.reference).exists(), f"{s.name} -> {s.reference}"

    def test_every_state_has_a_marker_to_prove_it_arrived(self):
        """Gate 1. Without it a capture can be a perfectly-rendered photograph
        of the login screen."""
        for s in fvc.STATES:
            assert s.marker and s.marker.strip(), s.name

    def test_all_three_required_breakpoints_are_covered(self):
        sizes = {s.viewport for s in fvc.STATES}
        assert fvc.DESKTOP in sizes and fvc.TABLET in sizes and fvc.PHONE in sizes

    def test_state_names_are_unique(self):
        names = [s.name for s in fvc.STATES]
        assert len(names) == len(set(names))
