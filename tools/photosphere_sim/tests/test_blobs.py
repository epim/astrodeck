"""``blobs.find`` against hand-built rasters.

Every image here is small enough to reason about pixel by pixel, so each
expected centroid and pixel count is written out rather than derived from the
code under test.
"""
import unittest

import numpy as np

from sim import blobs

RED = (255, 0, 0)
CYAN = (0, 128, 255)
BACKGROUND = (30, 30, 30)


def canvas(width=20, height=10):
    """A background-coloured RGBA raster, fully opaque."""
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    rgb[:, :] = BACKGROUND
    alpha = np.full((height, width), 255, dtype=np.uint8)
    return rgb, alpha


def paint_disc(rgb, alpha, cx, cy, colour, radius=1.5, opacity=255):
    """Paint the pixels within ``radius`` of ``(cx, cy)``, wrapping in x.

    At radius 1.5 that is the 3 x 3 block centred on the pixel, nine pixels
    whose centroid is exactly ``(cx, cy)``.
    """
    height, width = alpha.shape
    ys, xs = np.mgrid[0:height, 0:width]
    dx = (xs - cx + width / 2) % width - width / 2
    inside = dx ** 2 + (ys - cy) ** 2 <= radius ** 2
    rgb[inside] = colour
    alpha[inside] = opacity
    return int(inside.sum())


class FindBlobs(unittest.TestCase):
    def test_two_discs_of_one_colour_and_one_of_another(self):
        rgb, alpha = canvas()
        self.assertEqual(paint_disc(rgb, alpha, 3, 3, RED), 9)
        self.assertEqual(paint_disc(rgb, alpha, 15, 6, RED), 9)
        self.assertEqual(paint_disc(rgb, alpha, 9, 2, CYAN), 9)

        red = blobs.find(rgb, alpha, RED)
        cyan = blobs.find(rgb, alpha, CYAN)
        self.assertEqual(len(red) + len(cyan), 3)
        self.assertEqual(
            sorted((b.cx, b.cy, b.pixels) for b in red),
            [(3.0, 3.0, 9), (15.0, 6.0, 9)],
        )
        self.assertEqual([(b.cx, b.cy, b.pixels) for b in cyan], [(9.0, 2.0, 9)])

    def test_a_transparent_disc_is_not_found(self):
        rgb, alpha = canvas()
        paint_disc(rgb, alpha, 3, 3, RED)
        paint_disc(rgb, alpha, 15, 6, RED, opacity=0)
        found = blobs.find(rgb, alpha, RED)
        self.assertEqual([(b.cx, b.cy, b.pixels) for b in found], [(3.0, 3.0, 9)])

    def test_two_touching_discs_are_one_blob(self):
        rgb, alpha = canvas()
        paint_disc(rgb, alpha, 3, 3, RED)
        paint_disc(rgb, alpha, 6, 3, RED)
        found = blobs.find(rgb, alpha, RED)
        self.assertEqual(len(found), 1)
        # Columns 2..7, rows 2..4: eighteen pixels centred on (4.5, 3).
        self.assertEqual(found[0].pixels, 18)
        self.assertAlmostEqual(found[0].cx, 4.5)
        self.assertAlmostEqual(found[0].cy, 3.0)

    def test_a_disc_across_the_seam_is_one_blob(self):
        """The panorama is cyclic in azimuth, so column 0 touches column W-1.

        Two of the chart yard's own landmarks straddle azimuth 0; without the
        wrap each would decode as two blobs and be reported as a duplicate.
        """
        rgb, alpha = canvas()
        self.assertEqual(paint_disc(rgb, alpha, 0, 4, RED), 9)
        wrapped = blobs.find(rgb, alpha, RED)
        self.assertEqual(len(wrapped), 1)
        self.assertEqual(wrapped[0].pixels, 9)
        self.assertAlmostEqual(wrapped[0].cx, 0.0)
        self.assertAlmostEqual(wrapped[0].cy, 4.0)

        cut = blobs.find(rgb, alpha, RED, min_pixels=1, wrap_x=False)
        self.assertEqual(sorted(b.pixels for b in cut), [3, 6])

    def test_tolerance_is_the_largest_per_channel_difference(self):
        rgb, alpha = canvas()
        paint_disc(rgb, alpha, 3, 3, (215, 40, 40))   # every channel 40 away
        paint_disc(rgb, alpha, 15, 6, (214, 41, 41))  # 41 away, one channel
        found = blobs.find(rgb, alpha, RED)
        self.assertEqual([(b.cx, b.cy, b.pixels) for b in found], [(3.0, 3.0, 9)])

    def test_min_pixels_drops_the_smaller_blob(self):
        rgb, alpha = canvas()
        paint_disc(rgb, alpha, 3, 3, RED)             # nine pixels
        paint_disc(rgb, alpha, 15, 6, RED, radius=0)  # one pixel
        self.assertEqual(len(blobs.find(rgb, alpha, RED, min_pixels=1)), 2)
        self.assertEqual(len(blobs.find(rgb, alpha, RED)), 1)


if __name__ == "__main__":
    unittest.main()
