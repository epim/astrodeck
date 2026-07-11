"""Table-driven tests for astrodeck.rotation — direct transcription checks of
docs/native-parity/algorithms/nina-platesolving.md §11.2/§11.4. The UI mirror
(ui/src/lib/rotation.ts) asserts the SAME vectors — keep the tables in sync."""
import pytest

from astrodeck.rotation import (
    angle_equals,
    angle_equals_mod180,
    map_sky_target,
    mod360,
    shortest_rotation,
    target_mechanical_position,
)


def test_mod360_is_euclidean():
    assert mod360(0.0) == 0.0
    assert mod360(360.0) == 0.0
    assert mod360(-10.0) == 350.0
    assert mod360(725.0) == 5.0


# (a, b, tol, equal) — §11.4: d = |mod(a,360)-mod(b,360)|; equal when
# d <= tol or (360-d) <= tol (with the 1e-13 slack).
ANGLE_EQ = [
    (0.0, 0.0, 1.0, True),
    (0.5, 0.0, 1.0, True),
    (1.5, 0.0, 1.0, False),
    (359.5, 0.0, 1.0, True),      # wrap
    (180.0, 0.0, 1.0, False),
    (365.0, 5.0, 0.5, True),      # mod-360 collapse
]


@pytest.mark.parametrize("a,b,tol,eq", ANGLE_EQ)
def test_angle_equals(a, b, tol, eq):
    assert angle_equals(a, b, tol) is eq


MOD180_EQ = [
    (180.0, 0.0, 1.0, True),      # the whole point: 180° apart IS equal
    (179.5, 0.0, 1.0, True),
    (90.0, 0.0, 1.0, False),
    (359.5, 180.0, 1.0, True),
]


@pytest.mark.parametrize("a,b,tol,eq", MOD180_EQ)
def test_angle_equals_mod180(a, b, tol, eq):
    assert angle_equals_mod180(a, b, tol) is eq


# (p, range_type, range_start, expected) — §11.2 get_target_mechanical_position.
# d = mod360(p - range_start); HALF: p if d<180 else p+180;
# QUARTER: p / p+270 / p+180 / p+90 for d<90 / d<180 / d<270 / else.
RANGE_MAP = [
    (10.0, "full", 0.0, 10.0),
    (350.0, "full", 245.0, 350.0),
    # HALF, start 0: allowed [0,180)
    (10.0, "half", 0.0, 10.0),        # d=10  < 180 → p
    (190.0, "half", 0.0, 10.0),       # d=190 ≥ 180 → p+180 = 370 → 10
    # HALF, start 245: allowed [245, 65)
    (30.0, "half", 245.0, 30.0),      # d=mod360(30-245)=145 < 180 → p
    (100.0, "half", 245.0, 280.0),    # d=215 ≥ 180 → p+180 = 280
    # QUARTER, start 0: allowed [0,90)
    (10.0, "quarter", 0.0, 10.0),     # d=10  < 90  → p
    (100.0, "quarter", 0.0, 10.0),    # d=100 < 180 → p+270 = 370 → 10
    (200.0, "quarter", 0.0, 20.0),    # d=200 < 270 → p+180 = 380 → 20
    (300.0, "quarter", 0.0, 30.0),    # d=300 ≥ 270 → p+90  = 390 → 30
    # QUARTER, start 245
    (250.0, "quarter", 245.0, 250.0),  # d=5   < 90  → p
    (340.0, "quarter", 245.0, 250.0),  # d=95  < 180 → p+270 = 610 → 250
    (100.0, "quarter", 245.0, 280.0),  # d=215 < 270 → p+180 = 280
    (160.0, "quarter", 245.0, 250.0),  # d=275 ≥ 270 → p+90  = 250
]


@pytest.mark.parametrize("p,rt,start,expected", RANGE_MAP)
def test_target_mechanical_position(p, rt, start, expected):
    assert target_mechanical_position(p, rt, start) == pytest.approx(expected)


def test_map_sky_target_full_range_is_identity():
    # FULL: mech target == mech, so the sky target comes back unchanged.
    assert map_sky_target(120.0, 78.5, 30.0, "full", 0.0) == pytest.approx(120.0)


def test_map_sky_target_composes_offset_and_range():
    # §11.2: mech = mod360(sky + offset); mech_tgt = range_map(mech);
    # back = mod360(mech_tgt - offset). sky=100, offset=40 → mech=140;
    # HALF start 245 → d=mod360(140-245)=255 ≥ 180 → mech_tgt=320;
    # back = 320-40 = 280.
    assert map_sky_target(100.0, 0.0, 40.0, "half", 245.0) == pytest.approx(280.0)


def test_shortest_rotation_full_prefers_180_flip():
    # §11.3 FULL branch: distance 170° → the 180°-flipped frame is only −10 away.
    d = shortest_rotation(170.0, 0.0, "full")
    assert d == pytest.approx(-10.0)
    # distance 10 stays 10
    assert shortest_rotation(10.0, 0.0, "full") == pytest.approx(10.0)
    # distance 100: mod180=100, m2=-80 → -80 is shorter
    assert shortest_rotation(100.0, 0.0, "full") == pytest.approx(-80.0)


def test_shortest_rotation_limited_range_keeps_full_signed():
    # HALF/QUARTER: the target was already range-mapped — do NOT collapse
    # mod-180 (that could command the out-of-range twin). Just normalize
    # to (-180, 180].
    assert shortest_rotation(350.0, 0.0, "half") == pytest.approx(-10.0)
    assert shortest_rotation(190.0, 0.0, "quarter") == pytest.approx(-170.0)
