# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 4.3 (#1113 part b; #779, #172 part B): the assembled mosaic preview.

Each panel's live stack is placed by WCS on one tangent plane and drawn as one
picture (``imaging/mosaic_preview.py``), served by
``GET /api/sequence/stack/mosaic.jpg``. The assembly is a RENDER, never a
stack: nothing here may fold one panel into another's accumulators.

What is graded, and why a test of it can fail:

* PLACEMENT BY WCS. A 2x2 mosaic whose panels are handed over in a scrambled
  order: each corner of the picture must hold the panel the sky puts there
  (east to the left, north up, the way an image is read). A placement by panel
  order puts the panels where their list positions say and the corners swap.
* THE OVERLAP IS FEATHERED, not cut: between two panels' levels there are
  intermediate values, monotonic across the strip.
* THE SIZE IS BOUNDED by the asked-for long edge, and never finer than the
  finest panel's own grid.
* A GRID OF ITS OWN. Panels whose stacks sit on different grids (the budget
  coarsens a restarted panel) are each laid on their own footprint.
* THE ANGLE turns the panel; pa 0 is the image as the stacker holds it.
* A HOLE IS OUTLINED. A panel with no stack, or released by the memory budget
  and not restarted, is a black footprint with an outline, and the picture
  holds none of the pixels the stacker has let go.
* ONE SHARED STRETCH: a panel ten times brighter than its neighbour is drawn
  brighter, not stretched back to the same look.
* THE ROUTE is permissioned like its neighbours (the pixels of the sky:
  ``view.preview``), answers a JPEG, and names no sky coordinate.

Mutant (a byte backup in the scratch directory, restored byte-identically):

``M1-place-by-panel-order``  ``_canvas_corners`` lays each panel's outline on
    the grid cell its list position names (column ``i % 2``, row ``i // 2``)
    instead of projecting its WCS corners.
    ``test_each_corner_of_a_2x2_comes_from_the_panel_the_sky_puts_there``
    fails: the corner cells hold the panels in list order.
"""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import astrodeck.api.app as app_module
from astrodeck.auth.rbac import _dependency_caps, iter_app_routes
from astrodeck.catalog import framing
from astrodeck.config import ConfigStore
from astrodeck.imaging import mosaic_preview
from astrodeck.imaging.mosaic_preview import Placement
from astrodeck.imaging.sessionstack import PanelPlanes, SessionStacker
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target, TargetGroup
from astrodeck.sequence.session import Session, session_store

# A made-up mosaic centre: not round, away from the poles and the RA seam.
RA0, DEC0 = 7.1234, 21.4321
FOV_X, FOV_Y = 2.0, 1.5
#: Panel offsets from the centre, degrees: a 20 percent overlap each way.
XI, ETA = 0.8, 0.6
#: Grid of every panel's stack, rows x columns: 0.01 degree a pixel.
GRID = (150, 200)
LEVELS = {"NE": 1000.0, "NW": 2000.0, "SE": 3000.0, "SW": 4000.0}
#: Panels handed over in an order that is NOT the sky's, so a placement by list
#: position cannot agree with a placement by WCS.
SCRAMBLED = ("SW", "NE", "SE", "NW")
OFFSETS = {"NE": (XI, ETA), "NW": (-XI, ETA), "SE": (XI, -ETA),
           "SW": (-XI, -ETA)}
RUN = "run-1"


def placement(key: str, xi: float, eta: float, *, pa: float = 0.0,
              fov=(FOV_X, FOV_Y)) -> Placement:
    ra, dec = framing.deproject(xi, eta, RA0, DEC0)
    return Placement(key=key, name=key, ra_hours=ra, dec_deg=dec, pa_deg=pa,
                     fov_x=fov[0], fov_y=fov[1])


def four_placements(order=SCRAMBLED) -> list[Placement]:
    return [placement(k, *OFFSETS[k]) for k in order]


def flat(key: str, level: float, shape=GRID) -> PanelPlanes:
    """A panel whose stack is one flat level in L."""
    return PanelPlanes(key=key, target=key, frames=3,
                       planes={"L": np.full(shape, level, dtype=np.uint16)})


def four_flat_panels() -> dict[str, PanelPlanes]:
    return {k: flat(k, v) for k, v in LEVELS.items()}


def decoded(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


# --------------------------------------------------------------- placement
def test_each_corner_of_a_2x2_comes_from_the_panel_the_sky_puts_there():
    got = mosaic_preview.place_panels(four_placements(), four_flat_panels(),
                                      size=800)
    plane = got.planes["L"]
    h, w = plane.shape
    inset = 25
    corners = {"NE": (inset, inset),                  # east is left, north up
               "NW": (inset, w - 1 - inset),
               "SE": (h - 1 - inset, inset),
               "SW": (h - 1 - inset, w - 1 - inset)}
    seen = {k: float(plane[r, c]) for k, (r, c) in corners.items()}
    assert seen == pytest.approx(LEVELS, rel=1e-4), \
        "each corner of the picture holds the panel the sky puts there"


def test_the_overlap_is_feathered_not_cut():
    got = mosaic_preview.place_panels(four_placements(), four_flat_panels(),
                                      size=800)
    plane = got.planes["L"]
    h, w = plane.shape
    row = plane[int(h * 0.2)]               # a row through NE | NW's seam
    left, right = LEVELS["NE"], LEVELS["NW"]
    between = np.flatnonzero((row > left + 1) & (row < right - 1))
    assert between.size >= 5, "no intermediate value across the seam: a hard cut"
    assert np.all(np.diff(between) == 1), "the blend is one strip"
    ramp = row[between]
    assert np.unique(np.round(ramp)).size >= 5,         "one level across the seam is an average of two panels, not a feather"
    assert np.all(np.diff(ramp) >= -1e-3), "the blend runs one way across the seam"
    assert left < ramp.mean() < right


def test_the_size_is_bounded_by_the_long_edge_asked_for():
    shape = (1500, 2000)                    # 0.001 degree a pixel
    panels = {k: flat(k, v, shape) for k, v in LEVELS.items()}
    got = mosaic_preview.place_panels(four_placements(), panels, size=600)
    h, w = got.planes["L"].shape
    assert max(h, w) <= 600 and max(h, w) >= 550, (h, w)


def test_the_picture_is_never_finer_than_the_panels_own_grid():
    got = mosaic_preview.place_panels(four_placements(), four_flat_panels(),
                                      size=4096)
    h, w = got.planes["L"].shape
    # 3.6 degrees across at 0.01 degree a pixel is 360 pixels, plus the margin.
    assert max(h, w) <= 380, (h, w)


def test_a_panel_on_a_coarser_grid_still_covers_its_own_footprint():
    panels = four_flat_panels()
    panels["SE"] = flat("SE", LEVELS["SE"], (75, 100))     # half the pixels
    got = mosaic_preview.place_panels(four_placements(), panels, size=800)
    plane = got.planes["L"]
    h, w = plane.shape
    assert float(plane[h - 1 - 25, 25]) == pytest.approx(LEVELS["SE"], rel=1e-4)
    assert float(plane[h - 1 - 25, w - 1 - 25]) == pytest.approx(
        LEVELS["SW"], rel=1e-4)


def test_a_grid_finer_than_the_picture_is_averaged_down_not_sampled():
    shape = (1500, 2000)                    # 0.001 degree a pixel
    checker = np.indices(shape).sum(axis=0) % 2 * 4000
    panels = {"A": PanelPlanes("A", "A", 1, {"L": checker.astype(np.uint16)})}
    got = mosaic_preview.place_panels([placement("A", 0.0, 0.0)], panels,
                                      size=400)
    plane = got.planes["L"]
    h, w = plane.shape
    middle = plane[h // 3:2 * h // 3, w // 3:2 * w // 3]
    assert abs(float(middle.mean()) - 2000.0) < 100.0
    assert float(middle.std()) < 300.0,         "the picture sampled one pixel in five instead of averaging them"


@pytest.mark.parametrize("pa,rises_to_the_right", [(0.0, True), (180.0, False)])
def test_the_angle_turns_the_panel(pa, rises_to_the_right):
    ramp = np.tile(np.linspace(100.0, 4000.0, GRID[1], dtype=np.float32),
                   (GRID[0], 1)).astype(np.uint16)
    panels = {"A": PanelPlanes("A", "A", 1, {"L": ramp})}
    got = mosaic_preview.place_panels([placement("A", 0.0, 0.0, pa=pa)],
                                      panels, size=800)
    plane = got.planes["L"]
    row = plane[plane.shape[0] // 2]
    covered = row[row > 0]
    assert bool(covered[-5:].mean() > covered[:5].mean()) is rises_to_the_right


def test_a_quarter_turn_makes_a_wide_panel_tall():
    panels = {"A": flat("A", 2000.0)}
    got = mosaic_preview.place_panels(
        [placement("A", 0.0, 0.0, pa=90.0)], panels, size=800)
    h, w = got.planes["L"].shape
    assert h > w * 1.2, (h, w)


def test_a_panels_first_row_is_the_pictures_bottom_edge():
    """The stack grid is the saved FITS array, and a plate solve's WCS reads it
    in FITS order: row 0 is y = 1, and a standard solve (CD2_2 > 0) has y
    running north. So rows rise toward the north, which is UP in a north-up
    picture: the array's first row is the picture's bottom edge."""
    ramp = np.tile(np.linspace(100.0, 4000.0, GRID[0], dtype=np.float32)[:, None],
                   (1, GRID[1])).astype(np.uint16)
    panels = {"A": PanelPlanes("A", "A", 1, {"L": ramp})}
    got = mosaic_preview.place_panels([placement("A", 0.0, 0.0)], panels,
                                      size=800)
    column = got.planes["L"][:, got.planes["L"].shape[1] // 2]
    covered = column[column > 0]
    assert covered[:5].mean() > covered[-5:].mean(),         "the picture shows the grid's last row at the bottom"


# ------------------------------------------------------------------- holes
def test_a_panel_with_no_stack_is_an_outlined_hole_not_a_picture():
    panels = four_flat_panels()
    del panels["SE"]
    got = mosaic_preview.place_panels(four_placements(), panels, size=800)
    assert [p.key for p in got.holes] == ["SE"]
    jpeg, meta = mosaic_preview.render(got)
    states = {p["key"]: p["state"] for p in meta["panels"]}
    assert states == {"NE": "stacked", "NW": "stacked", "SE": "hole",
                      "SW": "stacked"}
    assert meta["holes"] == 1 and meta["stacked"] == 3
    img = decoded(jpeg)
    outline = got.outlines["SE"]
    # The outline walks the ring 0, 1, 3, 2; its third and fourth points are
    # the panel's east edge, which for the south-east panel is the picture's
    # left edge and is clear of every neighbour. Its midpoint is on the line.
    (x0, y0), (x1, y1) = outline[2], outline[3]
    edge = img[int((y0 + y1) / 2), int((x0 + x1) / 2)]
    assert int(edge.max()) >= 150, "the hole's footprint is not outlined"
    h, w = got.planes["L"].shape
    inside = img[h - 1 - 60, 60]
    assert int(inside.max()) <= 30, "a hole shows pixels it does not have"


def test_every_panel_missing_is_four_outlines_on_black():
    got = mosaic_preview.place_panels(four_placements(), {}, size=800)
    assert len(got.holes) == 4 and got.planes == {}
    jpeg, meta = mosaic_preview.render(got)
    img = decoded(jpeg)
    assert meta["stacked"] == 0 and meta["holes"] == 4
    assert int(img.max()) >= 150 and float(img.mean()) < 40


def test_nothing_to_place_is_none():
    assert mosaic_preview.place_panels([], four_flat_panels(), size=800) is None
    assert mosaic_preview.assemble([], four_flat_panels(), size=800) is None


def test_a_released_panel_is_a_hole_and_its_pixels_are_not_drawn():
    """The stacker's own budget releases a panel; what the stacker no longer
    holds cannot be in the picture."""
    from test_w3_3_session_stack_budget import (MIN_SIDE, feed,
                                                one_panel_bytes)
    one = one_panel_bytes()
    s = SessionStacker(max_bytes=int(one * 1.5), min_side=MIN_SIDE)
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    assert s.status()["evicted"] == ["A"]
    panels = s.panel_planes()
    assert list(panels) == ["B"]
    places = [placement("A", -0.8, 0.0, fov=(1.6, 1.2)),
              placement("B", 0.8, 0.0, fov=(1.6, 1.2))]
    got = mosaic_preview.place_panels(places, panels, size=800)
    assert [p.key for p in got.holes] == ["A"]
    _jpeg, meta = mosaic_preview.render(got)
    assert {p["key"]: p["state"] for p in meta["panels"]} == {
        "A": "hole", "B": "stacked"}


# ----------------------------------------------------------------- stretch
def test_one_stretch_is_shared_so_a_brighter_panel_is_drawn_brighter():
    shape = GRID
    x = np.linspace(0.5, 1.5, shape[1], dtype=np.float32)
    dim = (1000.0 * x * np.ones((shape[0], 1), np.float32)).astype(np.uint16)
    bright = (10000.0 * x * np.ones((shape[0], 1), np.float32)).astype(np.uint16)
    panels = {"A": PanelPlanes("A", "A", 1, {"L": dim}),
              "B": PanelPlanes("B", "B", 1, {"L": bright})}
    places = [placement("A", -0.8, 0.0, fov=(1.6, 1.2)),
              placement("B", 0.8, 0.0, fov=(1.6, 1.2))]
    jpeg, _meta = mosaic_preview.assemble(places, panels, size=800)
    img = decoded(jpeg).astype(float)
    h, w, _ = img.shape
    east = img[h // 2, 15:w // 4].mean()        # B is east, so on the left
    west = img[h // 2, 3 * w // 4:w - 15].mean()
    assert east > west + 40, \
        "a panel ten times brighter was stretched back to its neighbour's look"


# ---------------------------------------------------------- the accessor
def test_the_accessor_reads_and_changes_nothing():
    from test_w3_3_session_stack_budget import feed
    s = SessionStacker(min_side=50)
    s.start()
    feed(s, "A", 0)
    feed(s, "B", 0)
    before = (s.seq, s.held_bytes(), [p["seq"] for p in s.status()["panels"]],
              s.status()["panels"])
    got = s.panel_planes()
    assert list(got) == ["A", "B"]
    assert got["A"].frames == 1 and got["A"].target == "A"
    assert set(got["A"].planes) == {"R"}
    assert got["A"].planes["R"].dtype == np.uint16
    after = (s.seq, s.held_bytes(), [p["seq"] for p in s.status()["panels"]],
             s.status()["panels"])
    assert before == after


def test_the_accessor_holds_nothing_once_the_stack_is_reset():
    from test_w3_3_session_stack_budget import feed
    s = SessionStacker(min_side=50)
    s.start()
    feed(s, "A", 0)
    s.reset()
    assert s.panel_planes() == {}


# ------------------------------------------------------ plan -> placements
def _group_plan(*, pa=None, names=("A", "B", "C", "D")) -> SequencePlan:
    targets = []
    for name, (xi, eta) in zip(names, OFFSETS.values()):
        ra, dec = framing.deproject(xi, eta, RA0, DEC0)
        targets.append(Target(id=f"t-{name.lower()}", name=name, ra_hours=ra,
                              dec_deg=dec, mosaic_group="g1",
                              steps=[ExposureStep(filter="L", exposure_s=60,
                                                  count=3)]))
    group = TargetGroup(id="g1", name="M", pa_deg=pa,
                        geometry={"rows": 2, "cols": 2, "fov_x": FOV_X,
                                  "fov_y": FOV_Y, "overlap": 0.2})
    return SequencePlan(name="mosaic", targets=targets, groups=[group])


def test_placements_come_from_the_group_and_name_every_member():
    got = mosaic_preview.placements_for(_group_plan(pa=30.0), {"t-a", "t-b"})
    assert [p.key for p in got] == ["t-a", "t-b", "t-c", "t-d"]
    assert [p.name for p in got] == ["A", "B", "C", "D"]
    assert {p.pa_deg for p in got} == {30.0}
    assert {(p.fov_x, p.fov_y) for p in got} == {(FOV_X, FOV_Y)}


def test_a_plan_with_no_mosaic_places_nothing():
    plan = SequencePlan(name="one", targets=[
        Target(name="M42", ra_hours=5.5, dec_deg=-5.4)])
    assert mosaic_preview.placements_for(plan, {"x"}) == []


def test_the_group_the_stack_holds_panels_of_is_the_one_placed():
    plan = _group_plan()
    other = _group_plan(names=("E", "F", "G", "H"))
    for t in other.targets:
        t.mosaic_group = "g2"
        t.id = t.id + "-2"
    other.groups[0].id = "g2"
    plan.targets += other.targets
    plan.groups.append(other.groups[0])
    got = mosaic_preview.placements_for(plan, {"t-e-2", "t-f-2", "t-a"})
    assert [p.key for p in got] == ["t-e-2", "t-f-2", "t-g-2", "t-h-2"]


def test_the_session_of_a_run_is_found_by_its_night():
    live = Session(plan=_group_plan(), nights=["other"])
    old = Session(plan=_group_plan(), nights=["n-1", RUN])
    assert mosaic_preview.session_of_run(RUN, live, lambda: [old]) is live
    assert mosaic_preview.session_of_run(RUN, None, lambda: [old]) is old
    assert mosaic_preview.session_of_run("nope", None, lambda: [old]) is None
    assert mosaic_preview.session_of_run("", None, lambda: [old]) is None


# ------------------------------------------------------------------- route
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")
    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            app_module.hub.stop_session_stack()


def _stack_two(client) -> None:
    from test_w3_3_session_stack_budget import field
    assert client.post("/api/sequence/stack/start").status_code == 200
    st = app_module.hub.session_stack
    assert st.add(field(seed=1), "L", 60.0, target="A", target_id="t-a",
                  session=RUN)
    assert st.add(field(30.0, 10.0, seed=2), "L", 60.0, target="B",
                  target_id="t-b", session=RUN)


def test_the_route_is_gated_like_the_other_stack_pixel_routes(client):
    routes = {r.path: r for r in iter_app_routes(client.app)
              if getattr(r, "path", None)}
    assert _dependency_caps(routes["/api/sequence/stack/mosaic.jpg"]) == \
        _dependency_caps(routes["/api/sequence/stack/preview.jpg"]) == \
        frozenset({"view.preview"})


def test_the_route_answers_a_jpeg_of_the_run_s_mosaic(client):
    _stack_two(client)
    session_store.save(Session(plan=_group_plan(), nights=[RUN]))
    r = client.get("/api/sequence/stack/mosaic.jpg?size=700")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-stack-panels"] == "2"
    assert r.headers["x-stack-holes"] == "2"
    assert r.headers["x-stack-seq"] == str(app_module.hub.session_stack.seq)
    img = Image.open(io.BytesIO(r.content))
    assert max(img.size) <= 700


def test_the_route_says_what_is_missing(client):
    # No stack at all: nothing to assemble.
    r = client.get("/api/sequence/stack/mosaic.jpg")
    assert r.status_code == 404
    assert "nothing stacked" in r.json()["detail"]
    # Panels but no plan that owns the run: there is nothing to place them by.
    _stack_two(client)
    r = client.get("/api/sequence/stack/mosaic.jpg")
    assert r.status_code == 404
    assert "mosaic" in r.json()["detail"]


def test_the_route_names_no_sky_coordinate(client):
    _stack_two(client)
    session_store.save(Session(plan=_group_plan(), nights=[RUN]))
    r = client.get("/api/sequence/stack/mosaic.jpg")
    blob = " ".join(f"{k}={v}" for k, v in r.headers.items())
    for ra_dec in (f"{RA0:.2f}", f"{DEC0:.2f}"):
        assert ra_dec not in blob

# ----------------------------------------------- one sim frame, end to end
# what the sim camera renders and its solver stamps
from astrodeck.devices.sim import (  # noqa: E402
    SIM_PIXEL_SCALE_ARCSEC as SIM_SCALE_ARCSEC)


def _sim_star_frame(east_deg: float, north_deg: float):
    """A frame from the sim camera's own renderer holding ONE star at a known
    sky offset (east and north of the pointing, degrees), and the sky position
    the sim's renderer means by it."""
    import math
    from astrodeck.devices.sim import SimCamera, SimRig
    rig = SimRig()
    rig._ra_hours, rig.dec_deg = RA0, DEC0
    cam = SimCamera(rig)
    star_ra = RA0 * 15.0 + east_deg / math.cos(math.radians(DEC0))
    star_dec = DEC0 + north_deg
    tile = (math.floor(star_ra / 0.5), math.floor((star_dec + 90.0) / 0.5))
    mine = np.array([[star_ra, star_dec, 8.0]])
    cam._stars_for_tile = lambda tx, ty: mine if (tx, ty) == tile         else np.empty((0, 3))
    field = cam._render_stars(cam.sensor_height, cam.sensor_width, 60.0, 100, 1,
                              np.random.default_rng(1))
    return np.clip(field + 100.0, 0, 65535).astype(np.uint16), cam


def _stamped_sky_offset(frame_path, row: int, col: int) -> tuple[float, float]:
    """Where the WCS the simulator's solver stamps on a saved light puts the
    array pixel ``(row, col)``, as tangent-plane degrees ``(xi, eta)`` from the
    pointing: the FITS rule (pixel 1-based, offset from CRPIX, through CD)."""
    from astropy.io import fits
    from astrodeck.imaging.fitsio import stamp_wcs
    from astrodeck.solve.simsolver import _sim_wcs
    assert stamp_wcs(frame_path, _sim_wcs(frame_path, RA0, DEC0, 0.0,
                                          SIM_SCALE_ARCSEC))
    hdr = fits.getheader(frame_path)
    dx, dy = col + 1 - hdr["CRPIX1"], row + 1 - hdr["CRPIX2"]
    return (hdr["CD1_1"] * dx + hdr["CD1_2"] * dy,
            hdr["CD2_1"] * dx + hdr["CD2_2"] * dy)


def _saved_stacked_assembled(tmp_path, data):
    """The path a light takes: the camera's frame is written with
    ``save_fits`` (the file the plate solve reads), the same array is added to
    the stacker, and the stack is assembled. Returns the saved path, the
    canvas's L plane, the canvas scale (degrees a pixel) and the stack grid's
    own peak position."""
    from astropy.io import fits
    from astrodeck.devices.base import CameraFrame
    from astrodeck.imaging.fitsio import save_fits
    frame = CameraFrame(data=data, exposure_s=60.0, gain=100, offset=30,
                        binning=1, bayer_pattern=None, temperature_c=0.0,
                        timestamp=0.0)
    path = save_fits(frame, tmp_path / "sim.fits")
    assert np.array_equal(fits.getdata(path), data),         "the FITS write or read moved a row or a column"
    stacker = SessionStacker()
    stacker.start()
    assert stacker.add(data, "L", 60.0, target="A", target_id="A", session=RUN)
    panels = stacker.panel_planes()
    grid = panels["A"].planes["L"]
    fov_x = data.shape[1] * SIM_SCALE_ARCSEC / 3600.0
    fov_y = data.shape[0] * SIM_SCALE_ARCSEC / 3600.0
    got = mosaic_preview.place_panels(
        [Placement("A", "A", RA0, DEC0, 0.0, fov_x, fov_y)], panels, size=900)
    canvas = got.planes["L"]
    return path, canvas, fov_x / grid.shape[1], np.unravel_index(
        int(np.argmax(grid)), grid.shape), data.shape[1] // grid.shape[1]


def test_a_sim_frames_star_lands_where_its_stamped_wcs_puts_it(tmp_path):
    """The orientation a real plate solve gives the stack grid, end to end on
    the sim's frame: the saved light's stamped WCS places the star at a sky
    offset, and the assembled canvas (north up, east left) holds it on that
    side, at that distance."""
    data, _cam = _sim_star_frame(east_deg=0.2, north_deg=0.15)
    path, canvas, scale, (grow, gcol), factor = _saved_stacked_assembled(
        tmp_path, data)
    xi, eta = _stamped_sky_offset(path, grow * factor + factor // 2,
                                  gcol * factor + factor // 2)
    assert abs(xi) > 0.05 and abs(eta) > 0.05, "the star is on an axis"
    v, u = np.unravel_index(int(np.argmax(canvas)), canvas.shape)
    h, w = canvas.shape
    assert (u - w / 2.0) * scale == pytest.approx(-xi, abs=4 * scale),         "east is not to the left of the picture's centre (or the distance is off)"
    assert (v - h / 2.0) * scale == pytest.approx(-eta, abs=4 * scale),         "north is not above the picture's centre (or the distance is off)"


def test_the_sim_camera_and_the_sim_solver_agree_on_which_way_is_east(tmp_path):
    data, _cam = _sim_star_frame(east_deg=0.2, north_deg=0.15)
    path, _canvas, _scale, (grow, gcol), factor = _saved_stacked_assembled(
        tmp_path, data)
    xi, eta = _stamped_sky_offset(path, grow * factor + factor // 2,
                                  gcol * factor + factor // 2)
    assert xi > 0 and eta > 0,         "the solver's WCS puts the star the camera drew north-east at "         f"(xi {xi:+.3f}, eta {eta:+.3f})"
