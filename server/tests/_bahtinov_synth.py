"""Shared synthetic Bahtinov-pattern generator for the NOV-12 tests.

Identical geometry to test_bahtinov.py's inline ``_make_bahtinov`` — three bright
spikes crossing at the ROI center: the two OUTER spikes pass through the center
(vertex at origin) and the CENTRAL spike is shifted perpendicular by
``central_shift`` px (0 == in focus). Imported by the analyze / hub tests."""
import numpy as np


def make_bahtinov(size=257, phi_c_deg=45.0, alpha_deg=20.0, central_shift=0.0,
                  line_hw=1.3, amp=1200.0, star_sigma=3.0, star_amp=6000.0, bg=100.0):
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(float)
    x, y = xx - c, yy - c
    img = np.full((size, size), bg, float)
    img += star_amp * np.exp(-(x**2 + y**2) / (2 * star_sigma**2))

    def add_line(ridge_deg, shift):
        phi = np.deg2rad(ridge_deg + 90.0)          # normal angle
        d = x * np.cos(phi) + y * np.sin(phi) - shift
        return amp * np.exp(-(d**2) / (2 * line_hw**2))

    img += add_line(phi_c_deg, central_shift)       # central
    img += add_line(phi_c_deg - alpha_deg, 0.0)     # outer L
    img += add_line(phi_c_deg + alpha_deg, 0.0)     # outer R
    return img
