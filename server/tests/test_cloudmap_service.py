"""The live cloud-occlusion service, stage 6a -- the poller and what it holds.

Stages 1-5 are a library nothing calls. This is the thing that calls them on a
timer, and every test here pins one specific way a timed network service can
hurt a rig: a poll that goes out while the feature is off, a failure that gets
cached, a log line carrying the observing site's coordinates to whoever reads
the journal, a poller that dies of one bad tick, a second connection to
Open-Meteo when weather.py already has one, and -- the one that matters most --
a motion vector kept past the pair that measured it.

NOTHING HERE TOUCHES THE NETWORK OR HDF5. ``latest_ref``, ``ensure_cached`` and
``read_window`` are replaced by in-process fakes and the httpx client by a
recorder, so the whole module runs with no socket, no NOAA outage and no
``cloudmap`` extra installed. The one test that must prove the disabled path
makes no request replaces the client with a stand-in whose CONSTRUCTION fails
the test -- the ``_Boom`` pattern weather.py's suite already uses, because a
test that only counts requests passes a build that opens a connection and then
decides not to use it.

THE SYNTHETIC FIELD IS BROADBAND, copied from the stage 5 suite along with its
reasoning: phase correlation whitens every frequency, so a band-limited "looks
like clouds" field has an empty high-frequency band that whitening promotes to
full-weight noise, and a known roll comes back as a plausible wrong answer
rather than as a refusal.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pydantic
import pytest

from astrodeck.cloudmap.abi_grid import GridSpec
from astrodeck.cloudmap.granule import CloudmapUnavailable, GranuleWindow
from astrodeck.cloudmap.source import GranuleRef
from astrodeck.config import CloudmapConfig, ConfigStore

import astrodeck.cloudmap.service as service_mod
import astrodeck.weather as weather_mod
from astrodeck.cloudmap.service import (
    HEIGHT_PRODUCT,
    MASK_PRODUCT,
    STALE_POLLS,
    CloudmapService,
)


# --------------------------------------------------------------- the fixtures
#
# The grids, the site and the cell sizes under it are the stage 5 suite's, kept
# byte-for-byte so a failure here and a failure there mean the same thing.

ACMC = GridSpec(
    sat_height_m=42164160.0,
    r_eq_m=6378137.0,
    r_pol_m=6356752.31414,
    lon_origin_deg=-137.0,
    sweep_axis="x",
    x_offset_rad=-0.06997200101613998,
    x_scale_rad=5.6000000768108293e-05,
    n_cols=2500,
    y_offset_rad=0.12821200489997864,
    y_scale_rad=-5.6000000768108293e-05,
    n_rows=1500,
)
ACHAC = GridSpec(
    sat_height_m=42164160.0,
    r_eq_m=6378137.0,
    r_pol_m=6356752.31414,
    lon_origin_deg=-137.0,
    sweep_axis="x",
    x_offset_rad=-0.06985999643802643,
    x_scale_rad=0.0002800000074785203,
    n_cols=500,
    y_offset_rad=0.12809999287128448,
    y_scale_rad=-0.0002800000074785203,
    n_rows=300,
)

SITE_LAT, SITE_LON = 37.3, -121.9
SITE_CELL = (454, 1880)
HEIGHT_CELL = (90, 376)
#: ``pixel_size_km(ACMC, 454, 1880)``, pinned rather than re-derived.
NS_KM = 2.944790427532874
EW_KM = 2.2076858729929976

_BIG = 301
_SPAN = 281
_BIG_ROW0 = SITE_CELL[0] - _BIG // 2
_BIG_COL0 = SITE_CELL[1] - _BIG // 2
_H_SPAN = 61
_H_ROW0 = HEIGHT_CELL[0] - _H_SPAN // 2
_H_COL0 = HEIGHT_CELL[1] - _H_SPAN // 2

#: One GOES CONUS refresh.
GRANULE_S = 300.0
#: A whole-hour anchor, so the hourly wind series a test primes covers ``now``.
BASE = 1_755_000_000.0 - (1_755_000_000.0 % 3600.0)

#: The shift the synthetic pair carries, in cells: one row south, two columns
#: west. Through the PINNED cell sizes above that is 63.7 km/h toward 236 deg.
SHIFT = (1.0, -2.0)
SHIFT_SPEED_KMH = math.hypot(
    SHIFT[0] * NS_KM / GRANULE_S * 3600.0,
    SHIFT[1] * EW_KM / GRANULE_S * 3600.0,
)


def _clouds(seed: int, shape: tuple[int, int], beta: float = 1.8) -> np.ndarray:
    """A cloud-like probability field: power-law noise, normalised to 0..1."""
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft2(rng.normal(size=shape))
    ky = np.fft.fftfreq(shape[0])[:, None]
    kx = np.fft.rfftfreq(shape[1])[None, :]
    k = np.sqrt(ky * ky + kx * kx)
    k[0, 0] = 1.0
    spectrum *= k ** (-beta / 2.0)
    field = np.fft.irfft2(spectrum, s=shape)
    field -= field.min()
    field /= field.max()
    return field


def _shifted(field: np.ndarray, rows: float, cols: float) -> np.ndarray:
    """``field`` moved by a possibly fractional number of cells, bilinear."""
    row_int, col_int = int(math.floor(rows)), int(math.floor(cols))
    row_frac, col_frac = rows - row_int, cols - col_int
    out = np.zeros_like(field)
    for d_row, w_row in ((0, 1.0 - row_frac), (1, row_frac)):
        for d_col, w_col in ((0, 1.0 - col_frac), (1, col_frac)):
            if w_row * w_col == 0.0:
                continue
            out = out + w_row * w_col * np.roll(
                field, (row_int + d_row, col_int + d_col), axis=(0, 1)
            )
    return out


_FIELD = _clouds(20260821, (_BIG, _BIG))
_MOVED = _shifted(_FIELD, SHIFT[0], SHIFT[1])


def _mask_window(observed_at: datetime, *, moved: bool) -> GranuleWindow:
    """A cloud-mask window over the site, before or after the shift."""
    field = (_MOVED if moved else _FIELD)[0:_SPAN, 0:_SPAN].copy()
    return GranuleWindow(
        spec=ACMC,
        observed_at=observed_at,
        product=MASK_PRODUCT,
        platform="G18",
        row0=_BIG_ROW0,
        col0=_BIG_COL0,
        data={
            "Cloud_Probabilities": field,
            "DQF": np.zeros((_SPAN, _SPAN)),
        },
    )


def _height_window(observed_at: datetime) -> GranuleWindow:
    """A cloud-top window: a flat 4 km deck, so every ray crosses something."""
    return GranuleWindow(
        spec=ACHAC,
        observed_at=observed_at,
        product=HEIGHT_PRODUCT,
        platform="G18",
        row0=_H_ROW0,
        col0=_H_COL0,
        data={"HT": np.full((_H_SPAN, _H_SPAN), 4000.0)},
    )


def _ref(product: str, scan_start: datetime) -> GranuleRef:
    return GranuleRef(
        bucket="noaa-goes18",
        key=product + "/" + scan_start.strftime("%Y/%j/%H") + "/OR_" + product
        + "-M6_G18_s" + scan_start.strftime("%Y%j%H%M%S") + "0_e_c.nc",
        product=product,
        platform="G18",
        scan_start=scan_start,
        size_bytes=4_400_000,
    )


# ------------------------------------------------------------------ the doubles

class _RecordingClient:
    """The half of ``httpx.AsyncClient`` this service uses, recording URLs.

    Every URL any instance is asked for lands in the class-level ``urls`` list,
    which is what test 10 reads to prove the wind column did not arrive by a
    second connection to Open-Meteo.
    """

    urls: list[str] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        type(self).urls.append(str(url))
        raise AssertionError("the service made an unfaked request to " + str(url))

    def stream(self, method, url):
        type(self).urls.append(str(url))
        raise AssertionError("the service made an unfaked request to " + str(url))


class _BoomError(BaseException):
    """Deliberately NOT an ``Exception``.

    The service catches ``Exception`` broadly on every fetch path, because the
    poller must never die -- which means an ``AssertionError`` raised from a
    client constructor is swallowed, logged as an outcome, and the test that
    raised it passes a build that opened the socket. Measured: with the enabled
    gate removed entirely, the ``AssertionError`` version of this test still
    went green. Inheriting from ``BaseException`` puts the failure outside
    every catch in the module, which is the only way this assertion can be the
    thing it claims to be.
    """


class _Boom:
    """A client whose CONSTRUCTION fails the test (weather.py's §16 pattern)."""

    def __init__(self, *a, **kw):
        raise _BoomError(
            "an httpx client was constructed while the cloud map is disabled")


class _BusRecorder:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.logs: list[tuple[str, str, str]] = []

    def publish(self, type: str, **data):
        self.published.append((type, data))

    def log(self, level: str, message: str, source: str = "hub"):
        self.logs.append((level, message, source))


class _Upstream:
    """NOAA, in process: which granule each product is offering right now.

    ``advance`` is what a real 5-minute refresh looks like from the poller's
    side, and leaving it alone is what an upstream that has stopped producing
    looks like -- which is the distinction test 7 is about.
    """

    def __init__(self, start: datetime):
        self.mask_scan = start
        self.height_scan = start
        self.observed_at = start
        self.moved = False
        self.fail = None
        self.list_calls = 0
        self.read_calls = 0

    def advance(self, seconds: float, *, moved: bool = True) -> None:
        step = timedelta(seconds=seconds)
        self.mask_scan += step
        self.height_scan += step
        self.observed_at += step
        self.moved = moved

    async def latest_ref(self, client, bucket, product, now):
        self.list_calls += 1
        if self.fail is not None:
            raise self.fail
        scan = self.mask_scan if product == MASK_PRODUCT else self.height_scan
        return _ref(product, scan)

    async def ensure_cached(self, client, ref, cache_dir):
        if self.fail is not None:
            raise self.fail
        from pathlib import Path
        return Path(cache_dir) / ref.product / ref.key.rsplit("/", 1)[-1]

    def read_window(self, path, variables, **kw):
        self.read_calls += 1
        if str(path).find(HEIGHT_PRODUCT) >= 0:
            return _height_window(self.observed_at)
        return _mask_window(self.observed_at, moved=self.moved)


class _NoWeather:
    """A weather service holding no forecast: an empty column, every time."""

    def wind_column(self, now: float) -> list[dict]:
        return []


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """An isolated service: temp config (enabled, real site), injected clock,
    recorder bus, in-process NOAA, and a weather service with no column."""
    import astrodeck.hub as hub_mod

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    site = store.cfg().site
    site.latitude, site.longitude = SITE_LAT, SITE_LON
    site.elevation_m, site.is_default = 0.0, False
    store.cfg().cloudmap.enabled = True
    monkeypatch.setattr(service_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    rec = _BusRecorder()
    monkeypatch.setattr(service_mod, "bus", rec)
    monkeypatch.setattr(service_mod.httpx, "AsyncClient", _RecordingClient)
    _RecordingClient.urls = []

    upstream = _Upstream(
        datetime.fromtimestamp(BASE - 60.0, tz=timezone.utc))
    monkeypatch.setattr(service_mod, "latest_ref", upstream.latest_ref)
    monkeypatch.setattr(service_mod, "ensure_cached", upstream.ensure_cached)
    monkeypatch.setattr(service_mod, "read_window", upstream.read_window)
    monkeypatch.setattr(service_mod, "evict", lambda *a, **kw: 0)
    # THE READ IS FAKED, SO THE EXTRA GENUINELY IS NOT NEEDED -- and the tick
    # refuses to fetch without it, so the fixture has to say so. Set here
    # rather than left to whatever the runner happens to have installed: CI
    # installs `.[dev]` and not `.[cloudmap]`, so on the machine that grades
    # this suite `HAVE_H5PY` is False, and every test in this file would
    # silently become a test of the refusal.
    monkeypatch.setattr(service_mod, "HAVE_H5PY", True)

    now = {"t": BASE}
    service = CloudmapService(clock=lambda: now["t"], weather=_NoWeather())
    return service, now, store, rec, upstream


# ============================================================= 1. zero traffic

async def test_disabled_fetches_nothing(svc, monkeypatch):
    """A full tick with the feature off constructs no client at all.

    Counting requests is not enough: a build that opens a connection and then
    decides not to use it has already paid for the socket, and on a metered
    link it has already cost the operator something.
    """
    service, now, store, rec, upstream = svc
    store.cfg().cloudmap.enabled = False
    monkeypatch.setattr(service_mod.httpx, "AsyncClient", _Boom)
    await service.tick()
    now["t"] += 3600.0
    await service.tick()
    assert upstream.list_calls == 0
    assert service.state(now["t"]).enabled is False
    # The second detector, independent of the first: a tick that did nothing
    # has nothing to say. A build that reached the client and had its failure
    # swallowed by the never-die catch would leave a warning behind here.
    assert rec.logs == [], "a disabled tick reported an outcome: " + repr(rec.logs)
    assert service.state(now["t"]).last_error is None

    # A default site is the second half of the same gate: coordinates nobody
    # has set cannot name a place to fetch for.
    store.cfg().cloudmap.enabled = True
    store.cfg().site.is_default = True
    now["t"] += 3600.0
    await service.tick()
    assert upstream.list_calls == 0

    # ...and the moment both hold, one tick is all it takes -- no restart.
    store.cfg().site.is_default = False
    monkeypatch.setattr(service_mod.httpx, "AsyncClient", _RecordingClient)
    now["t"] += 3600.0
    await service.tick()
    assert upstream.list_calls == 2          # one listing per product


# ================================================== 3. promotion + motion

async def test_a_tick_promotes_the_previous_mask_and_estimates_motion(svc):
    """The reason this is a service and not a function.

    Motion needs two mask windows an interval apart. Holding the previous one
    makes it free on every tick after the first; a stateless endpoint would
    have to fetch two granules per request.
    """
    service, now, store, rec, upstream = svc
    await service.tick()
    first = service.state(now["t"])
    assert first.mask is not None and first.previous_mask is None
    assert first.motion is None, "one granule cannot measure a motion"

    upstream.advance(GRANULE_S)
    now["t"] += store.cfg().cloudmap.poll_minutes * 60.0
    await service.tick()
    second = service.state(now["t"])
    assert second.previous_mask is first.mask, "the old mask was not promoted"
    assert second.mask is not first.mask
    motion = second.motion
    assert motion is not None
    assert motion.dt_s == pytest.approx(GRANULE_S)
    # Inverted back through the PINNED cell sizes, so a broken correlator and a
    # broken cell size fail differently.
    hours = motion.dt_s / 3600.0
    assert -motion.north_kmh * hours / NS_KM == pytest.approx(SHIFT[0], abs=0.02)
    assert motion.east_kmh * hours / EW_KM == pytest.approx(SHIFT[1], abs=0.02)


# ================================== 4. a stale vector is worse than none

async def test_a_gap_too_long_clears_the_motion_rather_than_keeping_the_old_one(
        svc):
    """The rule of design section 4, and the one that is easy to get wrong.

    Keeping the last vector when the new pair cannot be measured looks like
    resilience and is a confident claim about a sky that has since changed. The
    honest answer is no answer.
    """
    service, now, store, rec, upstream = svc
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()
    assert service.state(now["t"]).motion is not None, "no vector to lose"

    # The previous fetch failed, say, so the next pair is twenty-five minutes
    # apart -- outside MAX_SEPARATION_S.
    upstream.advance(1500.0)
    now["t"] += 1500.0
    await service.tick()
    assert service.state(now["t"]).mask is not None, "the windows are still good"
    assert service.state(now["t"]).motion is None, (
        "the service kept a motion vector measured from a pair that is gone")

    # ...AND WHERE THE BAND ACTUALLY ENDS, which is not where design section 4
    # says it does. That section illustrates an out-of-band gap with "the
    # previous fetch failed, say, so the gap is 20 minutes" -- but stage 5's
    # check is `MIN_SEPARATION_S <= dt_s <= MAX_SEPARATION_S` with
    # MAX_SEPARATION_S = 1200.0, so exactly 20 minutes is IN band and one
    # missed ten-minute poll still yields a vector. The jump to 1500 s above
    # never touched the edge. Pinned in both directions so the constant and
    # the prose cannot drift apart again unnoticed, and so nobody "fixes" the
    # inclusive comparison to match the document: MAX_SEPARATION_S is shipped
    # stage 5 and other suites depend on it.
    upstream.advance(1200.0, moved=False)
    now["t"] += 1200.0
    await service.tick()
    edge = service.state(now["t"]).motion
    assert edge is not None, (
        "a pair exactly MAX_SEPARATION_S apart -- one missed poll -- was "
        "refused, though stage 5's band includes its endpoint")
    assert edge.dt_s == pytest.approx(1200.0)

    upstream.advance(1201.0)
    now["t"] += 1201.0
    await service.tick()
    assert service.state(now["t"]).motion is None, (
        "one second past MAX_SEPARATION_S is out of band and must clear the "
        "vector, not keep the one measured at the edge")


# ======================================== 5-6. failures are never cached

async def test_a_failed_tick_keeps_the_previous_windows(svc):
    service, now, store, rec, upstream = svc
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()
    good = service.state(now["t"])
    assert good.motion is not None

    upstream.fail = CloudmapUnavailable(
        "listing " + MASK_PRODUCT + " in noaa-goes18 failed: ConnectError")
    now["t"] += 600.0
    await service.tick()
    after = service.state(now["t"])
    assert after.mask is good.mask, "a failed fetch threw the last sky away"
    assert after.previous_mask is good.previous_mask
    assert after.height is good.height
    assert after.motion is good.motion, (
        "the motion measured from windows that are still held was discarded")
    assert after.observed_at == good.observed_at
    assert after.last_error == "CloudmapUnavailable"

    # ...and the next tick that works clears the error rather than latching it.
    upstream.fail = None
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()
    assert service.state(now["t"]).last_error is None


async def test_a_failed_tick_logs_no_url_and_no_coordinate(svc):
    """A latitude in a journal is a geolocation leak that outlives the process.

    The pierce point is within 30 km of the rig and today's S3 URL is a query
    string that will carry a site coordinate the day somebody adds a point
    query, so neither the URL nor the coordinates are ever echoed -- only the
    exception's TYPE.
    """
    service, now, store, rec, upstream = svc
    upstream.fail = CloudmapUnavailable(
        "fetching " + MASK_PRODUCT + " from https://noaa-goes18.s3.amazonaws.com/"
        "?prefix=lat" + str(SITE_LAT) + " failed: ConnectError")
    await service.tick()

    assert rec.logs, "a failed fetch said nothing at all"
    blob = " ".join(message for _level, message, _source in rec.logs)
    for forbidden in ("http", "://", "s3.amazonaws", "?prefix",
                      str(SITE_LAT), str(SITE_LON), str(abs(SITE_LON))):
        assert forbidden not in blob, (
            "the failure log carries " + repr(forbidden) + ": " + blob)
    assert "CloudmapUnavailable" in blob, (
        "the log has to say what went wrong, and the type is what it may say")
    assert service.state(now["t"]).last_error == "CloudmapUnavailable"


# ============================================ 7. staleness is about the sky

async def test_staleness_follows_the_observation_not_the_fetch(svc):
    """An upstream that has stopped producing must go stale, however well the
    polling is going. Derive staleness from ``fetched_at`` and a service that
    successfully re-lists a dead product every ten minutes reports a fresh sky
    forever."""
    service, now, store, rec, upstream = svc
    poll_s = store.cfg().cloudmap.poll_minutes * 60.0
    await service.tick()
    assert service.state(now["t"]).stale is False

    # Two more polls, both successful, both finding the SAME granule: NOAA has
    # not published since. Nothing is promoted and nothing is re-read.
    reads = upstream.read_calls
    for _ in range(2):
        now["t"] += poll_s
        await service.tick()
    fresh = service.state(now["t"])
    assert upstream.read_calls == reads, (
        "an unchanged granule was read again, which would promote it over "
        "itself and destroy the pair a motion was measured from")
    assert fresh.fetched_at == now["t"], "the poll itself did succeed"
    assert fresh.stale is False, "two polls in is not yet three"

    now["t"] += poll_s * 2
    await service.tick()
    old = service.state(now["t"])
    assert old.fetched_at == now["t"]
    assert old.stale is True, (
        "the observation is over " + repr(STALE_POLLS)
        + " polls old and the service still calls it fresh")


# ====================== 7b-7c. what the switch and the satellite leave behind
#
# Both of these were written after a mutation sweep: with the two ``_clear``
# calls in ``tick`` replaced by ``pass``, the whole suite still passed. Nothing
# above reaches the state the service goes on holding, because ``state`` hides
# it -- which is exactly why it needed its own test rather than an assertion
# bolted onto one of the tests above.

async def test_switching_the_feature_off_lets_go_of_the_sky(svc):
    """Off means the windows are dropped, not merely hidden by ``state``.

    ``state`` answers a disabled service with nulls, so one that went on
    holding two 201-cell arrays, a motion vector and an observation looks
    identical from the outside -- until the switch comes back on, when the
    granule read before the pause is paired with the one read after it and the
    motion between them spans however long the feature was off.
    """
    service, now, store, rec, upstream = svc
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()
    assert service.state(now["t"]).motion is not None, "no sky to let go of"

    store.cfg().cloudmap.enabled = False
    now["t"] += 600.0
    await service.tick()
    held = {name: getattr(service, name) for name in
            ("_mask", "_height", "_previous_mask", "_motion", "_mask_key",
             "_height_key", "_fetched_at", "_last_error")}
    assert all(value is None for value in held.values()), (
        "the disabled service is still holding " + repr(
            {k: type(v).__name__ for k, v in held.items() if v is not None}))

    # ...and coming back on is a fresh start rather than a resumed one.
    store.cfg().cloudmap.enabled = True
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()
    back = service.state(now["t"])
    assert back.mask is not None, "the switch came back on and nothing polled"
    assert back.previous_mask is None and back.motion is None, (
        "a window held across the off switch was paired with one read after "
        "it, and a motion measured over a pause is not a motion")


async def test_changing_the_platform_drops_the_other_satellites_grid(svc):
    """A DIFFERENT SATELLITE IS A DIFFERENT GRID, and the failed-fetch path is
    where that matters.

    G18's cells are not G19's. A window kept across the switch is served under
    the new platform's name and the new platform's credit URL -- the payload
    says G19 and the pixels are G18's, which is a wrong provenance claim rather
    than a stale one -- and every ray is walked over the wrong projection. If
    the first fetch on the new platform fails, that goes on for as long as the
    outage lasts, because nothing later re-examines it.
    """
    service, now, store, rec, upstream = svc
    await service.tick()
    assert service.state(now["t"]).mask is not None

    store.cfg().cloudmap.platform = "G19"
    upstream.fail = CloudmapUnavailable(
        "listing " + MASK_PRODUCT + " in noaa-goes19 failed: ConnectError")
    now["t"] += 600.0
    await service.tick()

    st = service.state(now["t"])
    assert st.platform == "G19", "the switch did not take"
    assert st.mask is None and st.height is None, (
        "a G18 window is being answered for as G19, under the G19 credit URL")
    assert st.previous_mask is None and st.motion is None
    payload = service.payload(now["t"])
    assert payload["observed_at"] is None and payload["age_s"] is None
    assert "goes19" in payload["credit"]["url"]


# ============================ 7d. and what MOVING THE RIG leaves behind

async def test_moving_the_site_drops_the_sky_over_the_old_one(svc):
    """A different place is a different window, for different reasons than a
    different satellite is a different grid.

    The projection is unchanged when the rig moves, so nothing refuses the
    held windows outright -- they are simply a rectangle cut around somewhere
    else. Measured before the site was compared: after moving 37.3/-121.9 to
    45.0/-112.0 (still inside the CONUS sector), ``/at`` answered 200 with
    ``no_data`` and a pierce point "22 rows and 21 columns outside the
    window", and ``/dome`` answered 200 with 0 of 162 cells filled and
    ``cell_km`` of [3.773, 2.506] -- ``pixel_size_km`` evaluated at the NEW
    site against the OLD window's indices, a distance belonging to neither
    place. It degraded honestly. It had no reason to degrade at all.
    """
    service, now, store, rec, upstream = svc
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()
    assert service.state(now["t"]).motion is not None, "no sky to lose"

    store.cfg().site.latitude, store.cfg().site.longitude = 45.0, -112.0
    # The ordinary case: the first fetch at the new site has not landed yet.
    upstream.fail = CloudmapUnavailable(
        "listing " + MASK_PRODUCT + " in noaa-goes18 failed: ConnectError")
    now["t"] += 600.0
    await service.tick()

    st = service.state(now["t"])
    still_held = [name for name, value in (
        ("mask", st.mask), ("height", st.height),
        ("previous_mask", st.previous_mask), ("motion", st.motion),
        ("observed_at", st.observed_at)) if value is not None]
    assert not still_held, (
        "the rig moved and the service is still holding the sky over where it "
        "used to be: " + repr(still_held))

    payload = await service.dome_payload(
        alt_step_deg=10.0, az_step_deg=20.0, now=now["t"])
    assert payload["rows"] is None
    assert payload["cell_km"] is None, (
        "the dome reported a cell size measured at the new site against the "
        "old site's window: " + repr(payload["cell_km"]))


# ================================================== 8. the poller never dies

async def test_the_poller_survives_an_exception_in_a_tick(svc, monkeypatch):
    service, now, store, rec, upstream = svc
    monkeypatch.setattr(service_mod, "CHECK_INTERVAL_S", 0.001)
    ticks = {"n": 0}

    async def _explode():
        ticks["n"] += 1
        raise RuntimeError("the sky fell")

    monkeypatch.setattr(service, "tick", _explode)
    service.start()
    try:
        for _ in range(200):
            await asyncio.sleep(0.001)
            if ticks["n"] >= 3:
                break
    finally:
        await service.stop()
    assert ticks["n"] >= 3, "the loop stopped after its first bad tick"
    assert any("RuntimeError" in message or "the sky fell" in message
               for _level, message, _source in rec.logs)


# ============= 8b-8c. what the never-die handler may say, and what may
#                      still get past it
#
# Both of these are about the SAME four lines: the catch-all in ``_run``. The
# test above proves the loop outlives a bad tick; neither of these was covered
# by it, and each is a way the one handler that exists to keep the poller alive
# was itself the hole.

async def test_the_pollers_catch_all_logs_a_type_and_never_the_text(
        svc, monkeypatch):
    """The catch-all was the one handler in the file echoing ``{e}``.

    It is also the handler most likely to see an exception nobody in this
    module wrote -- and httpx puts the whole request URL in its message. Every
    other handler here logs ``type(exc).__name__``; measured across eleven
    forced failure paths, ten logged a bare type name and this one line
    reproduced a poisoned URL-and-coordinate string verbatim into the night
    log, where it outlives the process.
    """
    service, now, store, rec, upstream = svc
    monkeypatch.setattr(service_mod, "CHECK_INTERVAL_S", 0.001)
    poison = ("https://noaa-goes18.s3.amazonaws.com/?prefix=x&latitude="
              + str(SITE_LAT) + "&longitude=" + str(SITE_LON))
    ticks = {"n": 0}

    async def _explode():
        ticks["n"] += 1
        raise RuntimeError(poison)

    monkeypatch.setattr(service, "tick", _explode)
    service.start()
    try:
        for _ in range(200):
            await asyncio.sleep(0.001)
            if ticks["n"] >= 2:
                break
    finally:
        await service.stop()
    assert ticks["n"] >= 2
    blob = " ".join(message for _level, message, _source in rec.logs)
    assert "RuntimeError" in blob, "the log has to say what went wrong"
    for forbidden in ("http", "://", "s3.amazonaws", "?prefix",
                      str(SITE_LAT), str(SITE_LON), str(abs(SITE_LON))):
        assert forbidden not in blob, (
            "the poller's catch-all echoed " + repr(forbidden) + ": " + blob)


async def test_the_poller_outlives_a_bus_that_cannot_take_the_log(
        svc, monkeypatch):
    """Section 8 says the poller never dies. One call could still kill it.

    Of thirteen failure modes armed against a beating loop, twelve were
    survived and the thirteenth was a bus whose ``log`` raises -- the handler
    that exists to keep the loop alive being the one call that escapes it. Not
    reachable through the shipped ``EventBus`` (``publish`` only does guarded
    queue operations and ``NightLog.append`` swallows and latches), which is
    exactly why nothing noticed: the invariant is absolute and the proof of it
    rested on a detail of a collaborator.
    """
    service, now, store, rec, upstream = svc
    monkeypatch.setattr(service_mod, "CHECK_INTERVAL_S", 0.001)

    class _FullJournal:
        def publish(self, type, **data):
            raise OSError("the journal is full")

        def log(self, level, message, source="hub"):
            raise OSError("the journal is full")

    monkeypatch.setattr(service_mod, "bus", _FullJournal())
    ticks = {"n": 0}

    async def _explode():
        ticks["n"] += 1
        raise RuntimeError("the sky fell")

    monkeypatch.setattr(service, "tick", _explode)
    service.start()
    try:
        for _ in range(400):
            await asyncio.sleep(0.001)
            if ticks["n"] >= 3:
                break
        alive = service._task is not None and not service._task.done()
        assert ticks["n"] >= 3, (
            "the poller died on the log line of the handler that is supposed "
            "to keep it alive, after " + repr(ticks["n"]) + " tick(s)")
        assert alive, "the poller task is finished"
    finally:
        await service.stop()


# ================================================== 9. the cadence floor

def test_the_poll_floor_is_five_minutes():
    """Below five minutes cannot produce fresher data -- the products update
    every five -- and only wastes bandwidth on a rig that is also carrying the
    relay tunnel and, on an imaging night, uploading frames."""
    assert CloudmapConfig().poll_minutes == 10
    assert CloudmapConfig().enabled is False
    assert CloudmapConfig().platform == "G18"
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(poll_minutes=4)
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(poll_minutes=61)
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(platform="G16")
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(half_px=15)
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(half_px=401)


def test_old_config_without_a_cloudmap_key_loads_with_defaults(tmp_path):
    path = tmp_path / "astrodeck.json"
    path.write_text('{"version": 7}')
    store = ConfigStore(path=path)
    assert store.cfg().cloudmap.enabled is False
    assert store.cfg().version == 7


# ============ 9b-9c. the cache is bounded, and nothing is fetched that
#                     nothing can read
#
# The granule cache lives under CAPTURE_DIR -- the same volume the frames are
# written to -- so "the cloud map filled the disk" and "the night stopped
# recording" are the same incident. These two are the halves of that: the trim
# must not be conditional on the read having worked, and a build that cannot
# read a granule must not download one.

async def test_a_download_that_cannot_be_read_still_trims_the_cache(
        svc, monkeypatch):
    """``ensure_cached`` writes 4.4 MB BEFORE ``read_window`` gets a look.

    Every failure that lands after the download used to return past the only
    call to ``evict`` in the tree -- an absent h5py extra, a granule that will
    not parse, a site outside the sector, which is what an operator east of
    about 100 W gets by leaving ``platform`` at its G18 default. Measured with
    the real ``ensure_cached`` and the real ``evict`` on a real filesystem, 40
    polls each: healthy settled at 12 files, two products at
    KEEP_PER_PRODUCT; with the read failing, 40 files, exactly one added per
    poll and nothing ever trimmed -- 144 granules and 634 MB a day, for ever.

    The real ``evict`` is used here. A stub would only prove that a function
    was called, and the thing that has to be true is that the directory stops
    growing.
    """
    import astrodeck.cloudmap.source as source_mod

    service, now, store, rec, upstream = svc
    monkeypatch.setattr(service_mod, "evict", source_mod.evict)

    async def _download(client, ref, cache_dir):
        dest = Path(cache_dir) / ref.product / Path(ref.key).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\0" * 64)      # granule-shaped, not granule-sized
        return dest

    def _unreadable(*a, **kw):
        raise CloudmapUnavailable(
            "reading a GOES granule needs h5py, which ships as the optional "
            "'cloudmap' extra: pip install 'astrodeck[cloudmap]'")

    monkeypatch.setattr(service_mod, "ensure_cached", _download)
    monkeypatch.setattr(service_mod, "read_window", _unreadable)

    polls = 20
    for _ in range(polls):
        await service.tick()
        upstream.advance(GRANULE_S)
        now["t"] += 600.0

    root = source_mod.cache_dir()
    held = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert held, "the test never exercised a download at all"
    assert len(held) <= 2 * service_mod.KEEP_PER_PRODUCT, (
        "after " + repr(polls) + " failed polls the cache holds "
        + repr(len(held)) + " granules; the trim is still conditional on the "
        "read that failed having worked: " + repr(held))
    assert service.state(now["t"]).last_error == "CloudmapUnavailable"


async def test_a_cache_trim_that_fails_is_not_a_failed_poll(svc, monkeypatch):
    """The trim runs in a ``finally``, AHEAD of the promotion. That is the
    price of trimming unconditionally, and this is the guard on it.

    Anything escaping ``_evict`` now takes a granule that was read cleanly
    down with it: the mask never promoted, the motion never measured, and a
    housekeeping fault reported as a sky one. So the handler is every
    Exception rather than the OSError a full disk raises -- eviction may cost
    a log line and it may never cost a poll.
    """
    service, now, store, rec, upstream = svc

    def _cannot(*a, **kw):
        raise RuntimeError("the cache directory is a file")

    monkeypatch.setattr(service_mod, "evict", _cannot)
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()

    st = service.state(now["t"])
    assert st.mask is not None, "a failed trim threw away a granule that read"
    assert st.motion is not None, "...and the pair it would have measured"
    assert st.last_error is None, (
        "a housekeeping fault was reported to the route as a fetch failure")
    assert any("eviction failed" in message and "RuntimeError" in message
               for _level, message, _source in rec.logs), (
        "the trim failed silently: " + repr(rec.logs))


async def test_switched_on_without_the_h5py_extra_fetches_nothing(
        svc, monkeypatch):
    """``enabled`` does not imply readable, and a fetch nobody can read is
    pure cost: bandwidth on a metered link and 4.4 MB of CAPTURE_DIR a poll.

    h5py is the optional ``cloudmap`` extra -- CI installs ``.[dev]`` and not
    ``.[cloudmap]`` -- so a release built without it plus one operator toggle
    is the whole trigger. Refused before a client is CONSTRUCTED, which is
    what ``_Boom`` here proves, and said once per enable rather than once a
    minute.
    """
    service, now, store, rec, upstream = svc
    monkeypatch.setattr(service_mod, "HAVE_H5PY", False)
    monkeypatch.setattr(service_mod.httpx, "AsyncClient", _Boom)

    for _ in range(3):
        await service.tick()
        now["t"] += 3600.0
    assert upstream.list_calls == 0, "a granule was listed with nothing to read it"

    said = [message for _level, message, _source in rec.logs]
    assert len(said) == 1, (
        "one static fact, once per enable -- not once every sixty seconds: "
        + repr(said))
    assert "h5py" in said[0] and "cloudmap" in said[0], said[0]
    assert service.state(now["t"]).last_error == "CloudmapUnavailable", (
        "the route has nothing to tell the operator about why it is empty")

    # Off and on again is a new fact worth one new line.
    store.cfg().cloudmap.enabled = False
    await service.tick()
    store.cfg().cloudmap.enabled = True
    await service.tick()
    assert len([m for m in (msg for _l, msg, _s in rec.logs)
                if "h5py" in m]) == 2


# ============================================ 10-11. one connection, not two

def _hourly_payload(*, start: float, hours: int = 6) -> dict:
    """An Open-Meteo response carrying the pressure-level wind column.

    Only the 300 hPa level is anywhere near the synthetic pair's 64 km/h toward
    236 deg; every other level is 56 degrees off it, which is outside stage 5's
    bearing gate. So a corroboration naming 300 hPa can only have come from
    reading this column.

    ``wind_direction`` is the METEOROLOGICAL convention -- where the wind comes
    FROM -- which is 180 degrees from the direction stage 5's ``WindLevel``
    wants. 56 here is 236 there.
    """
    times = [
        datetime.fromtimestamp(start + i * 3600.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M")
        for i in range(hours)
    ]
    block: dict = {"time": times}
    speeds = {850: 10.0, 700: 15.0, 500: 25.0, 400: 40.0, 300: 60.0,
              250: 90.0, 200: 120.0}
    heights = {850: 1500.0, 700: 3000.0, 500: 5600.0, 400: 7200.0,
               300: 9200.0, 250: 10400.0, 200: 11800.0}
    for level, speed in speeds.items():
        block["wind_speed_%dhPa" % level] = [speed] * hours
        block["wind_direction_%dhPa" % level] = [
            56.0 if level == 300 else 0.0] * hours
        block["geopotential_height_%dhPa" % level] = [heights[level]] * hours
    return {"hourly": block}


def _om_payload_with_wind(start: float) -> dict:
    """The full Open-Meteo body: the minutely_15 block weather.py already
    parses, plus the hourly wind column stage 6a asked it to fetch."""
    n = 8
    minutely = {
        "time": [
            datetime.fromtimestamp(start + i * 900.0, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M")
            for i in range(n)],
        "cloud_cover": [0] * n,
        "cloud_cover_low": [0] * n,
        "cloud_cover_mid": [0] * n,
        "cloud_cover_high": [0] * n,
        "precipitation": [0.0] * n,
    }
    out = {"minutely_15": minutely}
    out.update(_hourly_payload(start=start))
    return out


class _WeatherJsonResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return self

    def json(self):
        return self._payload


class _OpenMeteoClient:
    """The weather service's OWN connection, and the only one allowed to be
    Open-Meteo. Counts its calls so test 10 can prove the cloud map added none.
    """

    calls = 0
    payload: dict = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        type(self).calls += 1
        type(self).last_params = dict(params or {})
        return _WeatherJsonResp(type(self).payload)

    async def post(self, url, json=None):
        raise AssertionError("the weather service posted while under test")


@pytest.fixture
def wx(tmp_path, monkeypatch):
    """A real WeatherService with a real Open-Meteo response already in it.

    Real rather than a stub on purpose: the claim under test is that the wind
    column comes out of the connection weather.py already has, so the column
    has to be produced by the code that owns that connection.
    """
    import astrodeck.weather as weather_mod

    store = ConfigStore(path=tmp_path / "weather.json")
    site = store.cfg().site
    site.latitude, site.longitude = SITE_LAT, SITE_LON
    site.is_default = False
    store.cfg().weather.enabled = True
    monkeypatch.setattr(weather_mod, "config_store", store)
    monkeypatch.setattr(weather_mod, "bus", _BusRecorder())
    monkeypatch.setattr(weather_mod.httpx, "AsyncClient", _OpenMeteoClient)
    monkeypatch.setattr(weather_mod.WeatherService, "_tonight",
                        lambda self, t: None)
    _OpenMeteoClient.calls = 0
    _OpenMeteoClient.last_params = {}
    _OpenMeteoClient.payload = _om_payload_with_wind(BASE - 3600.0)
    return weather_mod.WeatherService(clock=lambda: BASE)


async def test_the_wind_column_comes_from_the_weather_service_not_a_second_fetch(
        svc, wx):
    """Design section 5: do not open a second connection.

    Open-Meteo is already reached once every fifteen minutes for the forecast.
    A second client for the pressure-level winds would double the traffic, the
    failure modes and the number of places a site coordinate leaves the rig.
    """
    service, now, store, rec, upstream = svc
    await wx.tick()
    assert _OpenMeteoClient.calls == 1, "the weather service did not fetch"
    column = wx.wind_column(BASE)
    assert [row["label"] for row in column] == [
        "850 hPa", "700 hPa", "500 hPa", "400 hPa", "300 hPa", "250 hPa",
        "200 hPa"]
    assert column[4]["speed_kmh"] == pytest.approx(60.0)
    assert column[4]["from_deg"] == pytest.approx(56.0)
    assert column[4]["height_km"] == pytest.approx(9.2)

    service._weather = wx
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()

    motion = service.state(now["t"]).motion
    assert motion is not None
    assert motion.speed_kmh == pytest.approx(SHIFT_SPEED_KMH, rel=0.02)
    assert motion.column_consulted is True
    assert motion.corroborated is True
    assert motion.matched_level == "300 hPa", (
        "the level that matched is the only one in the column that could")

    assert _OpenMeteoClient.calls == 1, (
        "the cloud map opened a second Open-Meteo connection")
    assert not [u for u in _RecordingClient.urls if "open-meteo" in u.lower()], (
        "the cloud map's own client reached Open-Meteo: "
        + repr(_RecordingClient.urls))


async def test_an_absent_wind_column_is_not_a_refutation(svc):
    """A sonde that did not launch is not evidence about the sky.

    ``column_consulted`` is the field that keeps "unconfirmed" and
    "contradicted" apart, and 6b draws them differently.
    """
    service, now, store, rec, upstream = svc
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()

    motion = service.state(now["t"]).motion
    assert motion is not None, "no column is not a reason to lose the motion"
    assert motion.corroborated is False
    assert motion.column_consulted is False
    assert "unconfirmed rather than contradicted" in motion.reason


# ============================ 10b-10c. the column has to be asked for, and
#                                       it has to be about now
#
# Both written after a mutation sweep found the pair above blind to them: the
# request can stop asking for the winds, and the answer can come from a sample
# hours away from the question, and every assertion in this file still passed.

async def test_the_forecast_request_itself_asks_for_the_wind_column(wx):
    """The column has to be ASKED for, not merely parsed when it turns up.

    ``_OpenMeteoClient`` answers with a canned body carrying the hourly block
    whether or not the request wanted it, so reading ``wind_column`` back
    proves the parser and nothing about the query string. Measured: with
    ``hourly=`` dropped from the Open-Meteo params entirely, every other test
    in this file went green -- and on the real endpoint that build corroborates
    nothing, for ever, with no error in any log, because an absent column is
    deliberately not a refutation.
    """
    await wx.tick()
    hourly = _OpenMeteoClient.last_params.get("hourly", "")
    for level in weather_mod.WIND_LEVELS_HPA:
        for field in ("wind_speed", "wind_direction", "geopotential_height"):
            assert "%s_%dhPa" % (field, level) in hourly, (
                "the forecast request stopped asking for " + field + " at "
                + str(level) + " hPa: " + repr(hourly))
    # ...on the request that was already being made, not a second one.
    assert _OpenMeteoClient.calls == 1
    assert "minutely_15" in _OpenMeteoClient.last_params, (
        "the wind fields replaced the cloud forecast instead of riding it")


async def test_a_wind_column_that_does_not_cover_now_is_no_column(wx):
    """An hours-old sounding is not evidence about this hour's sky.

    The series is hourly, so one covering ``now`` is never more than half an
    hour away; past an hour there is no sample for this moment. Handing the
    nearest one over anyway is worse than answering empty, because an empty
    column reads as "unconfirmed" and an old one sets ``column_consulted``
    True -- so yesterday's wind gets to corroborate, or contradict, a
    measurement made from this morning's granules.
    """
    await wx.tick()
    assert wx.wind_column(BASE), "the primed column has to be there to lose"
    # The primed series ends four hours after BASE; six hours out, the nearest
    # sample is two hours from the question.
    assert wx.wind_column(BASE + 6 * 3600.0) == [], (
        "a sample hours away from the question was answered as this hour's "
        "wind")
    assert wx.wind_column(BASE - 6 * 3600.0) == [], (
        "and the same going backwards, which is what a rig that lost the "
        "network at dusk asks for at midnight")


async def test_an_unreadable_wind_block_does_not_cost_the_forecast(
        wx, monkeypatch):
    """The wind column rides the request the RAIN VETO depends on.

    ``_parse_wind_column`` is total today -- fuzzed with 23 adversarial hourly
    blocks, none raised -- and its own docstring promises an unusable block
    produces an empty column. Nothing enforced that, and the call site sat
    outside every handler in the refresh: a raise there leaves
    ``_om_fetched_ts`` at None with a perfectly good minutely forecast already
    parsed, so ``_evaluate_night_warning`` never runs, no ``weather`` event is
    published, and the rain veto goes stale in silence. Demonstrated by making
    the function raise -- which without the guard takes the whole tick with
    it.
    """
    def _cannot(js):
        raise TypeError("string indices must be integers")

    monkeypatch.setattr(weather_mod, "_parse_wind_column", _cannot)
    await wx.tick()

    assert wx._om_fetched_ts == BASE, (
        "an unusable wind block cost the cloud forecast that parsed cleanly "
        "out of the same body")
    published = [t for t, _data in weather_mod.bus.published]
    assert "weather" in published, (
        "no weather event was published, so nothing downstream -- the rain "
        "veto included -- ever heard about this forecast")
    assert wx.payload(BASE)["stale"] is False
    assert wx.wind_column(BASE) == [], (
        "an unusable block has to produce an empty column, which is not a "
        "refutation of anything")
    assert any("wind column unusable" in message and "TypeError" in message
               for _level, message, _source in weather_mod.bus.logs), (
        "the column vanished with nothing in any log to say why: "
        + repr(weather_mod.bus.logs))


# =============== 17-18. two ways this route takes a core off a guiding rig
#
# Neither is a wrong answer. Both are the cloud map -- a picture, nothing that
# gates anything -- competing with the guide loop for the interpreter on a box
# that is imaging.

async def test_the_motion_estimate_does_not_run_on_the_event_loop(
        svc, monkeypatch):
    """The two expensive calls either side of it were deliberately moved off
    the loop; the correlation between them was not.

    Measured, best of five: 3.3 ms at the default ``half_px`` of 100, 20.3 ms
    at 200 and 61.6 ms at 400 -- the configured maximum, and a hard stall of
    the guide loop, the WS heartbeat and the frame pipeline once per poll, on
    a box several times faster per core than the Orange Pi appliance.
    """
    service, now, store, rec, upstream = svc
    loop_thread = threading.get_ident()
    ran_on: list[int] = []
    real = service_mod.estimate_motion

    def _watching(*args, **kwargs):
        ran_on.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(service_mod, "estimate_motion", _watching)
    await service.tick()
    upstream.advance(GRANULE_S)
    now["t"] += 600.0
    await service.tick()

    assert ran_on, "the motion was never estimated, so nothing was measured"
    assert loop_thread not in ran_on, (
        "the phase correlation ran on the event loop thread")
    assert service.state(now["t"]).motion is not None, (
        "moving it off the loop must not change the answer")


async def test_two_dome_requests_do_not_walk_the_sky_at_once(svc, monkeypatch):
    """MAX_DOME_RAYS bounds one request. Nothing bounded eight tabs.

    ``dome`` is a pure-Python double loop, so ``asyncio.to_thread`` moves it
    off the loop without releasing the GIL: parallel requests do not finish
    any sooner, they take the interpreter away from the guide loop while they
    argue over it. Measured on a 24-core box, eight default grids at once --
    1.86 s of wall and a worst-case loop stall of 1354 ms, against 1.88 s and
    30 ms once serialised. Same wall clock, two orders of magnitude less
    damage to everything else on the loop.
    """
    service, now, store, rec, upstream = svc
    await service.tick()

    live = {"now": 0, "most": 0}
    lock = threading.Lock()
    real = service_mod.dome

    def _counting(*args, **kwargs):
        with lock:
            live["now"] += 1
            live["most"] = max(live["most"], live["now"])
        try:
            time.sleep(0.05)             # long enough for the others to pile in
            return real(*args, **kwargs)
        finally:
            with lock:
                live["now"] -= 1

    monkeypatch.setattr(service_mod, "dome", _counting)
    payloads = await asyncio.gather(*[
        service.dome_payload(alt_step_deg=45.0, az_step_deg=45.0,
                             now=now["t"])
        for _ in range(6)])

    assert live["most"] == 1, (
        "six concurrent /dome requests put " + repr(live["most"])
        + " threads on the sky at once")
    assert all(p["rows"] is not None for p in payloads), (
        "serialising them must not lose any of the answers")
