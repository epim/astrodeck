import math, unittest
import numpy as np
from sim.geometry import (sky_vector, sky_angles, look_basis, Camera, device_orientation,
                          basis_from_device_orientation, angle_between, wrap_deg)

class SkyVectors(unittest.TestCase):
    def test_cardinal_directions_are_hand_specified(self):
        np.testing.assert_allclose(sky_vector(0, 0), [0, 1, 0], atol=1e-12)
        np.testing.assert_allclose(sky_vector(90, 0), [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(sky_vector(180, 0), [0, -1, 0], atol=1e-12)
        np.testing.assert_allclose(sky_vector(0, 90), [0, 0, 1], atol=1e-12)
    def test_angles_round_trip(self):
        for az, alt in [(0, 0), (37.5, 12.25), (270, -9.9), (359.9, 89.5)]:
            a, h = sky_angles(sky_vector(az, alt))
            self.assertAlmostEqual((a - az + 180) % 360 - 180, 0, places=9)
            self.assertAlmostEqual(h, alt, places=9)

class LookBasis(unittest.TestCase):
    def test_north_level_basis(self):
        b = look_basis(0, 0)
        np.testing.assert_allclose(b.forward, [0, 1, 0], atol=1e-12)
        np.testing.assert_allclose(b.right, [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(b.up, [0, 0, 1], atol=1e-12)
    def test_proper_rotation(self):
        for az, alt, roll in [(0, 0, 0), (123, 45, 0), (200, -5, 30), (0, 89, 0)]:
            m = look_basis(az, alt, roll).matrix_cam_to_world()
            self.assertAlmostEqual(np.linalg.det(m), 1.0, places=9)
            np.testing.assert_allclose(m.T @ m, np.eye(3), atol=1e-9)

class Pinhole(unittest.TestCase):
    def test_hand_specified_projection_portrait_60(self):
        cam = Camera(480, 640, 60.0)
        self.assertAlmostEqual(cam.fx, 240 / math.tan(math.radians(30)), places=6)
        # 30 deg right of forward in the horizontal plane lands on the right edge.
        d = np.array([[math.sin(math.radians(30)), 0, math.cos(math.radians(30))]])
        u, v = cam.project(d)[0]
        self.assertAlmostEqual(u, 480.0, places=6); self.assertAlmostEqual(v, 320.0, places=6)
        # 20 deg up lands above the centre: v = 320 - fx * tan(20).
        d = np.array([[0, math.sin(math.radians(20)), math.cos(math.radians(20))]])
        u, v = cam.project(d)[0]
        self.assertAlmostEqual(u, 240.0, places=6)
        self.assertAlmostEqual(v, 320 - cam.fx * math.tan(math.radians(20)), places=6)
    def test_behind_the_camera_is_nan(self):
        cam = Camera(480, 640, 60.0)
        self.assertTrue(np.isnan(cam.project(np.array([[0, 0, -1.0]]))).all())
    def test_ray_inverts_project_at_pixel_centres(self):
        cam = Camera(480, 640, 70.0)
        for u, v in [(0.5, 0.5), (240.5, 320.5), (479.5, 639.5), (100.5, 17.5)]:
            d = cam.ray(u, v)
            np.testing.assert_allclose(cam.project(d[None, :])[0], [u, v], atol=1e-9)

class WorldToCamera(unittest.TestCase):
    # Handoff 14 section 4.2 asks for independently specified landmark tests on
    # this conversion, because the pinhole frame (z along forward) and the
    # renderer's matrix (z along -forward) are both proper rotations and a
    # swap between them passes every determinant and orthogonality check.
    def test_landmarks_seen_by_a_level_camera_looking_north(self):
        from sim.geometry import to_camera
        cam = Camera(480, 640, 60.0)
        b = look_basis(0, 0)
        # Due north is the centre pixel; 30 deg east is the right edge; 20 deg
        # up is above the centre by fx * tan(20); due south has no image.
        for az, alt, expected in [(0, 0, (240.0, 320.0)),
                                  (30, 0, (480.0, 320.0)),
                                  (0, 20, (240.0, 320 - cam.fx * math.tan(math.radians(20)))),
                                  (330, 0, (0.0, 320.0))]:
            uv = cam.project(to_camera(b, sky_vector(az, alt))[None, :])[0]
            np.testing.assert_allclose(uv, expected, atol=1e-9)
        self.assertTrue(np.isnan(cam.project(to_camera(b, sky_vector(180, 0))[None, :])).all())
    def test_to_world_inverts_to_camera_for_a_rolled_camera(self):
        from sim.geometry import to_camera, to_world
        b = look_basis(217.0, 34.0, -25.0)
        v = np.array([[0, 1.0, 0], [1.0, 0, 0], [0, 0, 1.0], [0.3, -0.5, 0.81]])
        np.testing.assert_allclose(to_world(b, to_camera(b, v)), v, atol=1e-12)
        # A direction along the aim is straight down the camera's +z axis.
        np.testing.assert_allclose(to_camera(b, b.forward), [0, 0, 1], atol=1e-12)
        np.testing.assert_allclose(to_camera(b, b.right), [1, 0, 0], atol=1e-12)

class DeviceOrientation(unittest.TestCase):
    def test_upright_phone_looking_north(self):
        # Screen faces south, camera looks north: alpha 0, beta 90, gamma 0.
        b = basis_from_device_orientation(0, 90, 0)
        np.testing.assert_allclose(b.forward, [0, 1, 0], atol=1e-12)
        np.testing.assert_allclose(b.right, [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(b.up, [0, 0, 1], atol=1e-12)
    def test_alpha_270_looks_east(self):
        np.testing.assert_allclose(basis_from_device_orientation(270, 90, 0).forward, [1, 0, 0], atol=1e-12)
    def test_beta_110_tilts_the_camera_20_degrees_up(self):
        az, alt = sky_angles(basis_from_device_orientation(0, 110, 0).forward)
        self.assertAlmostEqual(alt, 20.0, places=9); self.assertAlmostEqual(az % 360, 0.0, places=9)
    def test_round_trip_over_a_grid_including_the_upright_singularity(self):
        for az in range(0, 360, 45):
            for alt in (-10, 0, 20, 60, 85):
                for roll in (0, -15, 15):
                    b = look_basis(az, alt, roll)
                    a, be, g = device_orientation(b)
                    self.assertTrue(0 <= a < 360); self.assertTrue(-180 <= be < 180); self.assertTrue(-90 <= g < 90)
                    c = basis_from_device_orientation(a, be, g)
                    self.assertLess(angle_between(b.forward, c.forward), 1e-7)
                    self.assertLess(angle_between(b.up, c.up), 1e-7)
    def test_change_of_0_1_degree_is_visible_in_the_euler_angles(self):
        a0 = device_orientation(look_basis(10.0, 20.0))
        a1 = device_orientation(look_basis(10.1, 20.0))
        self.assertGreaterEqual(max(abs(wrap_deg(x - y)) for x, y in zip(a0, a1)), 0.09)
    def test_round_trip_at_a_rotated_screen_angle(self):
        # Nothing above exercises screen_angle_deg, and the two conversions have
        # to be mutual inverses at every screen angle the phone can report.
        for screen in (90, 180, 270):
            for az, alt, roll in [(0, 0, 0), (37, 15, 0), (200, -5, 12), (300, 60, -20)]:
                b = look_basis(az, alt, roll)
                a, be, g = device_orientation(b, screen)
                c = basis_from_device_orientation(a, be, g, screen)
                self.assertLess(angle_between(b.forward, c.forward), 1e-7)
                self.assertLess(angle_between(b.up, c.up), 1e-7)
                self.assertLess(angle_between(b.right, c.right), 1e-7)
