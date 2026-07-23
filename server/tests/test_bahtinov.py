"""NOV-12 Bahtinov core: the synthetic three-spike pattern IS the spec.

A pure-numpy Radon + spike-fit + signed-offset routine over a generated Bahtinov
pattern — central_shift=0 reads in-focus (offset ~0), central_shift=N recovers
offset ~N with a sign that flips across focus, and a blank/starless frame reports
invalid without raising. No camera, no scipy."""
import numpy as np

from astrodeck.imaging.bahtinov import bahtinov_offset, _intersect


def _make_bahtinov(size=257, phi_c_deg=45.0, alpha_deg=20.0, central_shift=0.0,
                   line_hw=1.3, amp=1200.0, star_sigma=3.0, star_amp=6000.0, bg=100.0):
    """Three bright spikes crossing at the ROI center. The two OUTER spikes pass
    through the center (vertex at origin); the CENTRAL spike is shifted
    perpendicular by ``central_shift`` px (0 == in focus)."""
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


def test_intersect_basic():
    # x-axis line (n=(0,1),rho=0) and y-axis line (n=(1,0),rho=0) cross at origin
    p = _intersect((0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    assert p is not None and abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9


def test_in_focus_offset_near_zero():
    img = _make_bahtinov(central_shift=0.0)
    c = (img.shape[0] - 1) / 2.0
    off, angles, valid, _ = bahtinov_offset(img, c, c)
    assert valid and off is not None
    assert abs(off) < 0.6, off
    assert len(angles) == 3


def test_defocus_offset_magnitude_and_sign():
    c = (257 - 1) / 2.0
    off_p, _, ok_p, _ = bahtinov_offset(_make_bahtinov(central_shift=+6.0), c, c)
    off_n, _, ok_n, _ = bahtinov_offset(_make_bahtinov(central_shift=-6.0), c, c)
    assert ok_p and ok_n
    assert abs(abs(off_p) - 6.0) < 0.7, off_p          # magnitude recovered
    assert (off_p > 0) != (off_n > 0), (off_p, off_n)  # sign flips with defocus dir


def test_recovers_spike_angles():
    c = (257 - 1) / 2.0
    _, angles, valid, _ = bahtinov_offset(_make_bahtinov(phi_c_deg=45, alpha_deg=20), c, c)
    assert valid
    got = sorted(a % 180 for a in angles)
    # normal-angle triple {μ-α, μ, μ+α} with μ = 45+90 = 135
    for want in (115.0, 135.0, 155.0):
        assert min(abs(g - want) for g in got) < 1.6, (got, want)


def test_blank_frame_is_invalid():
    off, angles, valid, reason = bahtinov_offset(np.full((257, 257), 100.0), 128.0, 128.0)
    assert not valid and off is None and reason
