"""The live cloud-occlusion service (stage 6a) -- the poller and what it holds.

Stages 1 to 5 are a library nothing calls. This module makes them live: one
service class owning fetch, cache and state, a module-level singleton wired
into the app lifespan, and three read-only answers about the sky above this
rig. It is shaped after ``astrodeck.weather``, which is this codebase's worked
example of a service that talks to the internet on a timer without hurting
anything, and it keeps that module's four disciplines:

- **Zero outbound requests when disabled.** The tick no-ops unless
  ``cfg.cloudmap.enabled`` AND the site has been set, so a runtime toggle takes
  effect within one tick with no restart -- and no httpx client is CONSTRUCTED
  on the disabled path, which is what the ``_Boom`` test in the suite pins. A
  build that opens a socket and then decides not to use it has already cost the
  operator something on a metered link. Nor is a granule fetched when h5py --
  the optional ``cloudmap`` extra -- is absent, because nothing here could
  read one.
- **Failures are never cached.** On a failed fetch the previous windows are
  kept, ``last_error`` records the exception TYPE, and staleness goes on being
  derived from the observation's age. What IS on disk is trimmed on every
  poll whether or not that poll worked: the granule cache lives under
  CAPTURE_DIR, so "the cloud map filled the disk" and "the night stopped
  recording" are the same incident.
- **Logging is outcome-only**: the exception type and the product. Never a URL,
  never a coordinate. Stages 3 and 4 both enforce this and the reason is
  concrete -- a pierce point is within 30 km of the rig, and a latitude in a
  journal is a geolocation leak that outlives the process.
- **The poller never dies.** An exception in a tick is caught, logged, and the
  next tick runs.

WHY THIS IS A SERVICE AND NOT A FUNCTION: ``previous_mask``. Motion needs two
mask windows separated by 120 to 1200 seconds. Holding the previous one makes
motion free on every tick after the first; a stateless endpoint would have to
fetch two granules per request, which is 8.8 MB a click.

A STALE MOTION VECTOR IS WORSE THAN NONE. When a promotion happens the vector
is recomputed from the new pair and assigned unconditionally -- including when
the answer is ``None`` because the separation fell outside the band, which is
what a skipped poll or a failed one leaves behind. Keeping the last vector
would look like resilience and would be a confident claim about a sky that has
since changed.

THE CLOUD MODEL HAS NO VOTE. Nothing in the sequence engine, the safety gate or
auto-resume consults any of this, and a named test in
``test_cloudmap_routes.py`` greps those files and asserts it. That is not a
style preference: this codebase's weather gate refused two consecutive clear
nights to a forecast that had a vote, and the fix was to take the vote away. A
2 km satellite mask is a better instrument than a 10 km forecast and it is
still not the camera. If that ever changes it is a separate design with its own
argument, and the argument had better be better than the one those two nights
made.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..config import config_store
from ..events import bus
from ..weather import weather_service
from .abi_grid import lonlat_to_index, pixel_size_km
from .geometry import Site
from .granule import (
    HAVE_H5PY,
    CloudmapUnavailable,
    GranuleWindow,
    read_window,
)
from .motion import Motion, WindLevel, corroborate, estimate_motion, forecast_at
from .occlusion import (
    CLOUD_TOP,
    MASK_PROBABILITY,
    MASK_QUALITY,
    MIN_USABLE_ALT_DEG,
    _require_altitude,
    _require_step,
    dome,
    occlusion_at,
)
from .source import PRODUCTS, bucket_for, cache_dir, ensure_cached, evict, latest_ref

__all__ = [
    "CHECK_INTERVAL_S",
    "CREDIT_SOURCE",
    "CREDIT_URLS",
    "HEIGHT_PRODUCT",
    "MASK_PRODUCT",
    "STALE_POLLS",
    "CloudmapState",
    "CloudmapService",
    "credit",
    "cloudmap_service",
]

#: The loop's own beat, not the poll cadence. The tick is cheap and re-reads
#: config every time, so a cadence change or an enable takes effect within a
#: minute rather than within a poll interval -- weather.py's arrangement, for
#: weather.py's reason.
CHECK_INTERVAL_S = 60.0

#: The 2 km clear-sky mask and the 10 km cloud-top height, unpacked from stage
#: 3's tuple so the two names cannot drift from the two it fetches:
#: ``ABI-L2-ACMC`` and ``ABI-L2-ACHAC``.
MASK_PRODUCT, HEIGHT_PRODUCT = PRODUCTS

#: How many poll intervals an observation may age before it is called stale.
#: Three: one missed poll is a network blip, two is a bad ten minutes, three is
#: a product that has stopped.
STALE_POLLS = 3

#: Granules kept per product. Two are in use (the current mask and the previous
#: one); the rest is slack for a restart, at ~4.4 MB a cycle.
KEEP_PER_PRODUCT = 6

_TIMEOUT_S = 30.0
_USER_AGENT = "AstroDeck/0.1"

#: The most rays one ``/dome`` request may ask for. ADDITION TO DESIGN SECTION
#: 6, which bounds each step to ``(0, 45]`` and nothing else.
#:
#: A step has no lower bound in stage 4, and it is a float off a query string.
#: ``?alt_step=0.01&az_step=0.01`` is 8501 by 36000 -- three hundred million
#: rays of up to 75 rungs each -- which is hours of CPU and a list of lists
#: measured in gigabytes, requested by one authenticated GET. Off the event
#: loop it does not freeze the API, and it would still take a core away from a
#: rig that is guiding and dithering and writing frames.
#:
#: 20000 is a real ceiling rather than a round one: the default 2 by 4 degree
#: grid is 43 by 90 = 3870 rays and takes about three seconds, and the finest
#: grid anyone has a use for -- 1 by 2 degrees, one ray per square degree of
#: sky -- is 86 by 180 = 15480. This permits that and refuses the next step
#: down, which is 62000 and buys nothing: the mask cell under the site subtends
#: rather more than half a degree at the heights that matter, so a finer grid
#: is resampling one measurement, not making a sharper picture.
MAX_DOME_RAYS = 20_000

#: How many ``/dome`` grids may be walked at once, across all callers. ALSO AN
#: ADDITION TO DESIGN SECTION 6, and the other half of the ceiling above:
#: :data:`MAX_DOME_RAYS` bounds one request and nothing bounded N of them.
#:
#: ``dome`` is a pure-Python double loop, so ``asyncio.to_thread`` moves it off
#: the event loop without releasing the GIL -- eight parallel default-grid
#: requests do not finish any sooner, they just fight the loop for the
#: interpreter. Measured on a 24-core box: one request costs the loop 26 ms of
#: latency and 0.24 s of wall; eight at once cost the same 1.86 s of wall and a
#: worst-case loop stall of 1354 ms. Serialised through this, the same eight
#: cost 1.88 s of wall -- no slower -- and a worst-case stall of 30 ms.
#:
#: Stage 6b polls this route from a browser, and a browser can have several
#: tabs open. A queued second request is strictly better than two threads
#: taking the interpreter away from the guide loop.
MAX_CONCURRENT_DOMES = 1

#: How these answers credit their source. ``source`` is verbatim the name of
#: the NOAA entry in ``tools/credits_registry.py`` -- a test pins the two
#: together -- because that entry records, in the shipped notices, that there
#: is no screen naming NOAA yet and that the cloud-map panel should name it the
#: way the sky-conditions panel names Open-Meteo. These routes are how stage 6b
#: does that without inventing the string.
CREDIT_SOURCE = "NOAA GOES on AWS"

#: The bucket each platform's granules were actually read from, spelled out in
#: full for the reason ``source.py`` spells its two endpoints out:
#: ``test_credits.py`` finds outbound services by grepping this tree for
#: ``https://`` literals, and a URL assembled from a bucket name at runtime is
#: invisible to it.
#:
#: NOT the AWS Open Data registry page the credits entry links to, and that is
#: a deliberate second-best. Its host is named nowhere else in the product, so
#: putting it here would redden the detector that keeps the service list
#: honest, and the fix for that -- listing the page as documentation-only --
#: belongs in a change allowed to touch that file. Assembling the host out of
#: fragments to slip past the check was the other option and is not one: a
#: detector whose whole job is to notice an uncredited host must not be worked
#: around by the file it is watching.
CREDIT_URLS = {
    "G18": "https://noaa-goes18.s3.amazonaws.com/",
    "G19": "https://noaa-goes19.s3.amazonaws.com/",
}


def credit(platform: str) -> dict:
    """The credit block every route carries. Unknown platform falls back to G18."""
    return {
        "source": CREDIT_SOURCE,
        "url": CREDIT_URLS.get(platform, CREDIT_URLS["G18"]),
    }


def _iso_z(when: datetime | None) -> str | None:
    if when is None:
        return None
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CloudmapState:
    """Everything the service knows, as one immutable snapshot.

    A snapshot rather than the live object because a route reads five of these
    fields and a poll tick could land between two of them, producing an answer
    whose motion was measured from a mask it does not carry.

    ``previous_mask`` is the field that justifies the whole class -- see the
    module docstring. It is exposed rather than kept private so a test can
    assert the promotion happened, which is the only way to tell "a new granule
    arrived" from "the same one was read twice".
    """

    enabled: bool
    platform: str
    mask: GranuleWindow | None
    height: GranuleWindow | None
    previous_mask: GranuleWindow | None
    motion: Motion | None
    observed_at: datetime | None
    fetched_at: float | None
    stale: bool
    last_error: str | None


class CloudmapService:
    """Owns fetch, cache and state for the cloud-occlusion model.

    ``weather`` is injected rather than imported at use so a test can hand it a
    service with a known wind column, and so the dependency edge is visible in
    the constructor. It is only ever READ from: the cloud model does not tell
    the weather service anything, and it certainly does not tell the gate.
    """

    def __init__(self, *, clock=time.time, weather=None):
        self._clock = clock
        self._weather = weather if weather is not None else weather_service
        self._task: asyncio.Task | None = None
        self._platform = ""
        self._mask: GranuleWindow | None = None
        self._height: GranuleWindow | None = None
        self._previous_mask: GranuleWindow | None = None
        self._motion: Motion | None = None
        # The KEYS of the granules currently held. An unchanged key is an
        # upstream that has not published, which is a different thing from a
        # fetch that failed and must not promote anything -- see _refresh.
        self._mask_key: str | None = None
        self._height_key: str | None = None
        self._fetched_at: float | None = None
        self._last_error: str | None = None
        self._attempt_at: float = 0.0      # due-when-older-than attempt latch
        self._was_enabled = False
        # The SITE the windows now held were read around, for the reason the
        # platform is remembered: see the second half of the branch in `tick`.
        self._site_key: tuple[float, float] | None = None
        # One log line per enable, not one per minute, for a missing extra.
        self._h5py_warned = False
        # The /dome gate, bound lazily -- see _dome_gate.
        self._gate: asyncio.Semaphore | None = None
        self._gate_loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle (the WeatherService shape) --------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the poller must never die
                # THE TYPE, NOT THE TEXT -- the module docstring's rule, and
                # this was the one handler in the file still breaking it. It
                # is also the handler most likely to be reached by an
                # exception nobody here wrote: httpx puts the full request URL
                # in its message, and the day a point query carries the site
                # this line would have put the observatory's coordinates in
                # the night log, where they outlive the process.
                #
                # ...AND THE LOG ITSELF IS GUARDED, because it is the one call
                # that can escape the handler whose whole job is to keep the
                # loop alive. Section 8 says the poller never dies; a bus that
                # raises on its way to a full journal must not be the thing
                # that kills it. Losing a line is the cheaper failure.
                try:
                    bus.log("warning",
                            f"cloud map tick failed: {type(exc).__name__}",
                            "cloudmap")
                except Exception:  # noqa: BLE001 - nothing outranks the loop
                    pass
            await asyncio.sleep(CHECK_INTERVAL_S)

    # -- tick ----------------------------------------------------------------

    async def tick(self) -> None:
        """One beat. No-ops unless the feature is on and the site is real.

        The halves of that gate are separate facts. ``enabled`` is the
        operator's choice; a default site is coordinates nobody has set, and
        there is no place to fetch for; and an absent ``cloudmap`` extra is a
        build that could not read what it downloaded. Any one of them means no
        client is constructed at all.
        """
        now = self._clock()
        cfg = config_store.cfg()
        ccfg = cfg.cloudmap
        site = cfg.site
        if not (ccfg.enabled and not site.is_default):
            if self._was_enabled:
                # One cleared state on the first tick after the switch goes
                # off, then silence: holding a sky nobody asked for is how a
                # disabled feature comes to answer with data.
                self._clear()
                self._h5py_warned = False
            self._was_enabled = False
            return
        self._was_enabled = True
        if not HAVE_H5PY:
            # NOTHING HERE CAN READ A GRANULE, SO NOTHING HERE MAY DOWNLOAD
            # ONE. h5py is the optional ``cloudmap`` extra and ``enabled``
            # does not check for it, so a release built without the extra plus
            # one operator toggle was the whole trigger: 4.4 MB fetched every
            # ten minutes into CAPTURE_DIR -- the volume the frames are
            # written to -- and read_window raising CloudmapUnavailable over
            # every one of them. Refused at the top of the tick, before a
            # client is constructed, which is the only place that costs
            # nothing.
            #
            # SAID ONCE PER ENABLE. The loop beats every sixty seconds and a
            # line a minute about a static fact is a log nobody reads; the
            # state is on ``last_error`` for anyone asking the route.
            if not self._h5py_warned:
                self._h5py_warned = True
                bus.log("warning",
                        "the cloud map is switched on but cannot read a "
                        "granule: h5py ships as the optional 'cloudmap' "
                        "extra. Nothing will be fetched until it is "
                        "installed",
                        "cloudmap")
            # The type ``granule._require_h5py`` would have raised, so the
            # route says "the last attempt ended in CloudmapUnavailable"
            # rather than nothing at all.
            self._last_error = "CloudmapUnavailable"
            return
        # LATITUDE AND LONGITUDE ONLY. The windows are a rectangle of grid
        # cells around ``lonlat_to_index(lat, lon)`` and nothing else; the
        # elevation is read fresh out of config on every request, by the
        # geometry that walks the ray. Putting it in this key would throw two
        # good granules away every time somebody corrected the rig's height by
        # a metre, for a change that cannot make them wrong.
        site_key = (site.latitude, site.longitude)
        if ccfg.platform != self._platform or site_key != self._site_key:
            # A DIFFERENT SATELLITE IS A DIFFERENT GRID. Its cells are not this
            # one's cells, so a mask held from the old platform cannot be
            # correlated against a new one (stage 5 refuses the pair outright)
            # and a height window read on the old projection would place every
            # crossing somewhere else. Everything goes.
            #
            # AND SO IS A DIFFERENT PLACE, for reasons that are not the same
            # ones. The grid is unchanged, but the WINDOW cut out of it is a
            # rectangle around the old site: after the rig moves, the pierce
            # point falls outside it and every ray answers no_data, the next
            # motion correlates a new-site window against an old-site one, and
            # ``cell_km`` -- computed at the new site against the old window's
            # indices -- comes back as a distance belonging to neither place.
            # Measured before this line existed: /dome answered 200 with 0 of
            # 162 cells filled and a cell_km of [3.773, 2.506]. It degraded
            # honestly and it stayed degraded for a whole poll for no reason.
            self._clear()
            self._platform = ccfg.platform
            self._site_key = site_key
        if now - self._attempt_at < ccfg.poll_minutes * 60.0:
            return
        self._attempt_at = now
        await self._refresh(ccfg, site, now)

    def _clear(self) -> None:
        self._platform = ""
        self._site_key = None
        self._mask = self._height = self._previous_mask = None
        self._motion = None
        self._mask_key = self._height_key = None
        self._fetched_at = None
        self._last_error = None
        self._attempt_at = 0.0

    async def _refresh(self, ccfg, site, now: float) -> None:
        """One poll: list, fetch what is new, read it, re-measure the motion.

        NOTHING IS PROMOTED FOR A GRANULE THAT DID NOT CHANGE. NOAA publishes
        every five minutes and this polls every ten, so the key normally moves
        -- but when it does not, re-reading the same file and promoting it over
        itself would make ``previous_mask`` and ``mask`` the same observation,
        put the separation at zero, and destroy a perfectly good motion vector
        because the satellite was late. Comparing the key costs nothing: the
        listing has already been made.
        """
        geo = Site(site.latitude, site.longitude, site.elevation_m / 1000.0)
        bucket = bucket_for(ccfg.platform)
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        stage = MASK_PRODUCT
        mask: GranuleWindow | None = None
        height: GranuleWindow | None = None
        mask_key = height_key = None
        try:
            async with httpx.AsyncClient(
                    timeout=_TIMEOUT_S,
                    headers={"User-Agent": _USER_AGENT}) as client:
                mask_ref = await latest_ref(client, bucket, MASK_PRODUCT, when)
                if mask_ref is None:
                    raise CloudmapUnavailable(
                        "no granule of this product in the last two hours")
                stage = HEIGHT_PRODUCT
                height_ref = await latest_ref(
                    client, bucket, HEIGHT_PRODUCT, when)
                if height_ref is None:
                    raise CloudmapUnavailable(
                        "no granule of this product in the last two hours")
                stage = MASK_PRODUCT
                if mask_ref.key != self._mask_key:
                    path = await ensure_cached(client, mask_ref, cache_dir())
                    # The HDF5 read is blocking and a 201-cell window off a
                    # 4 MB file is not free; on a rig mid-sequence the event
                    # loop is also carrying the frame pipeline.
                    mask = await asyncio.to_thread(
                        read_window, path,
                        (MASK_PROBABILITY, MASK_QUALITY),
                        centre_lat_deg=geo.lat_deg,
                        centre_lon_deg=geo.lon_deg,
                        half_rows=ccfg.half_px, half_cols=ccfg.half_px)
                    mask_key = mask_ref.key
                stage = HEIGHT_PRODUCT
                if height_ref.key != self._height_key:
                    path = await ensure_cached(client, height_ref, cache_dir())
                    # THE SAME half_px ON A FIVE-TIMES-COARSER GRID, which is
                    # deliberate: the occlusion ladder walks to 151 km
                    # downrange at its 5 degree floor, and a height window that
                    # does not reach turns the whole bottom of the dome into
                    # no_data. At 10 km cells this asks for far more than it
                    # needs and clips at the sector edge, which costs one
                    # array and buys the horizon.
                    height = await asyncio.to_thread(
                        read_window, path, (CLOUD_TOP,),
                        centre_lat_deg=geo.lat_deg,
                        centre_lon_deg=geo.lon_deg,
                        half_rows=ccfg.half_px, half_cols=ccfg.half_px)
                    height_key = height_ref.key
        except Exception as exc:  # noqa: BLE001 - keep the previous windows
            # THE TYPE AND THE PRODUCT, AND NOTHING ELSE. httpx puts the full
            # request URL in its exception text and today's S3 URL is a query
            # string that will carry a site coordinate the day somebody adds a
            # point query, so the exception is never echoed -- only its name.
            bus.log("warning",
                    f"cloud map {stage} fetch failed: {type(exc).__name__}",
                    "cloudmap")
            self._last_error = type(exc).__name__
            return
        finally:
            # TRIMMED WHETHER OR NOT THE READ WORKED, and that is the whole
            # point of the `finally`. ``ensure_cached`` has already written
            # 4.4 MB to disk by the time ``read_window`` runs, so every
            # failure that lands AFTER the download used to return past the
            # only call to ``evict`` in the tree: an absent h5py extra, a
            # granule that will not parse, a site the sector does not cover.
            # Measured with the real ``ensure_cached`` and the real ``evict``
            # on a real filesystem, 40 polls each -- healthy: 12 files, two
            # products at KEEP_PER_PRODUCT; read failing: 40 files, exactly
            # one added per poll, 176 MB, nothing ever trimmed. That is 144
            # granules and 634 MB a day, for ever, on CAPTURE_DIR: the same
            # volume the frames are written to.
            #
            # A trim must never be conditional on the thing that filled the
            # cache having worked.
            self._evict()

        if mask is not None:
            self._previous_mask = self._mask
            self._mask = mask
            self._mask_key = mask_key
            # ASSIGNED UNCONDITIONALLY, including to None. This is the whole of
            # "a stale motion vector is worse than none": the vector describes
            # the pair that produced it, and the moment that pair changes the
            # old answer is a claim about a sky that has moved on.
            self._motion = await self._measure(geo, ccfg, now)
        if height is not None:
            self._height = height
            self._height_key = height_key
        self._fetched_at = now
        self._last_error = None

    def _evict(self) -> None:
        """Trim the granule cache, and never let that be why a poll failed.

        EVERY Exception, not just OSError, and that widening is load-bearing
        now that this runs in a ``finally`` ahead of the promotion rather than
        at the end of a successful poll. Anything escaping here would take a
        granule that WAS read cleanly down with it -- the mask never promoted,
        the motion never measured, and the cause reported as a housekeeping
        fault. A full disk is not a sky fault, and neither is anything else
        the filesystem can do to a directory listing.
        """
        try:
            evict(cache_dir(), KEEP_PER_PRODUCT)
        except Exception as exc:  # noqa: BLE001 - never the reason a poll fails
            bus.log("warning",
                    f"cloud map cache eviction failed: {type(exc).__name__}",
                    "cloudmap")

    async def _measure(self, geo: Site, ccfg, now: float) -> Motion | None:
        """The motion of the field between the two masks now held, or None.

        ``estimate_motion`` answers None for a pair it cannot measure -- a
        separation outside 120 to 1200 s, a correlation peak that is not a
        peak -- and RAISES for a pair it should never have been given: two
        different grids, a site outside the sector, an overlap too small to
        correlate. The raise is caught here rather than left to the tick,
        because a fetch that worked is not a failed poll and must not roll back
        the windows or set ``last_error``; it is one unmeasurable pair.

        THE CORRELATION ITSELF GOES OFF THE EVENT LOOP, like the two expensive
        calls either side of it. Measured, best of five: 3.3 ms at the default
        ``half_px`` of 100, 20.3 ms at 200, and 61.6 ms at 400 -- the
        configured maximum, which is a hard stall of the guide loop, the WS
        heartbeat and the frame pipeline once per poll, on a box several times
        faster per core than the Orange Pi appliance. Only the arithmetic is
        moved; the logging and the corroboration stay here, where they are
        cheap and where ``bus`` is on its own thread.
        """
        if self._previous_mask is None or self._mask is None:
            return None
        try:
            motion = await asyncio.to_thread(
                estimate_motion, self._previous_mask, self._mask, geo,
                half_px=ccfg.half_px)
        except Exception as exc:  # noqa: BLE001 - outcome-only, never fatal
            bus.log("warning",
                    f"cloud motion not measurable: {type(exc).__name__}",
                    "cloudmap")
            return None
        if motion is None:
            return None
        return corroborate(motion, self._wind_levels(now))

    def _wind_levels(self, now: float) -> list[WindLevel]:
        """The weather service's wind column, turned into stage 5's levels.

        NO SECOND CONNECTION TO OPEN-METEO. ``weather.py`` already reaches it
        every fifteen minutes and the pressure-level fields ride that request;
        this only reads the result.

        THE BEARING IS TURNED AROUND HERE, and this is the line to check when a
        corroboration looks wrong. Open-Meteo reports the direction the wind
        comes FROM, which is the meteorological convention; ``WindLevel``
        wants the direction the air is GOING, matching ``Motion.toward_deg``.
        They are 180 degrees apart, and a column entered backwards corroborates
        nothing while looking exactly like a sky the wind disagrees with -- a
        silent wrong answer rather than a loud one. The turn is done at this
        boundary, next to the construction, rather than inside weather.py,
        because this is the side that knows what ``WindLevel`` means.

        An empty or unreadable column is not a refutation and never an error.
        """
        try:
            rows = self._weather.wind_column(now)
        except Exception as exc:  # noqa: BLE001 - absence is not a refutation
            bus.log("warning",
                    f"wind column unavailable: {type(exc).__name__}",
                    "cloudmap")
            return []
        levels: list[WindLevel] = []
        for row in rows or []:
            try:
                levels.append(WindLevel(
                    label=str(row["label"]),
                    height_km=float(row["height_km"]),
                    speed_kmh=float(row["speed_kmh"]),
                    toward_deg=(float(row["from_deg"]) + 180.0) % 360.0,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return levels

    # -- what the routes read -------------------------------------------------

    def site(self) -> Site | None:
        """The observing site as stage 1 wants it, or None if never set."""
        cfg = config_store.cfg().site
        if cfg.is_default:
            return None
        return Site(cfg.latitude, cfg.longitude, cfg.elevation_m / 1000.0)

    def state(self, now: float | None = None) -> CloudmapState:
        """An immutable snapshot. Disabled answers with nulls, not with the sky
        it happened to be holding when the switch went off."""
        now = self._clock() if now is None else now
        ccfg = config_store.cfg().cloudmap
        if not ccfg.enabled:
            return CloudmapState(
                enabled=False, platform=ccfg.platform, mask=None, height=None,
                previous_mask=None, motion=None, observed_at=None,
                fetched_at=None, stale=False, last_error=None)
        observed = self._mask.observed_at if self._mask is not None else None
        # STALENESS IS ABOUT THE SKY, NOT ABOUT THE POLLING. Derived from
        # fetched_at instead, a service that successfully re-lists a dead
        # product every ten minutes would report a fresh sky forever.
        horizon_s = STALE_POLLS * ccfg.poll_minutes * 60.0
        stale = (observed is None
                 or (now - observed.timestamp()) > horizon_s)
        return CloudmapState(
            enabled=True,
            platform=ccfg.platform,
            mask=self._mask,
            height=self._height,
            previous_mask=self._previous_mask,
            motion=self._motion,
            observed_at=observed,
            fetched_at=self._fetched_at,
            stale=stale,
            last_error=self._last_error,
        )

    def payload(self, now: float | None = None) -> dict:
        """``GET /api/cloudmap``: is it on, how old is it, which way is it going."""
        now = self._clock() if now is None else now
        st = self.state(now)
        motion = None
        if st.motion is not None:
            motion = {
                "speed_kmh": st.motion.speed_kmh,
                "toward_deg": st.motion.toward_deg,
                "corroborated": st.motion.corroborated,
                # THE THIRD STATE. Without it `corroborated: false` means both
                # "no wind column was available" and "one was and disagreed",
                # and 6b draws unconfirmed and contradicted differently.
                "column_consulted": st.motion.column_consulted,
                "matched_level": st.motion.matched_level,
                "peak": st.motion.peak,
                # Two fields beyond the design's list, both honesty fields the
                # peak alone cannot carry: the separation the vector was
                # measured over, and the sentence stage 5 wrote about it.
                "dt_s": st.motion.dt_s,
                "reason": st.motion.reason,
            }
        age_s = (None if st.observed_at is None
                 else now - st.observed_at.timestamp())
        return {
            "enabled": st.enabled,
            "platform": st.platform,
            "observed_at": _iso_z(st.observed_at),
            "age_s": age_s,
            "stale": st.stale,
            "last_error": st.last_error,
            "motion": motion,
            "credit": credit(st.platform),
        }

    def _no_sky(self, st: CloudmapState) -> str | None:
        """Why there is nothing to answer with, or None when there is.

        Three sentences and not one, because a UI has to draw "off", "no data
        yet" and "the site was never set" differently, and a single "no data"
        makes the operator go and read a log to tell them apart.
        """
        if not st.enabled:
            return "the cloud map is switched off"
        if self.site() is None:
            return ("no observing site has been set, so there is no place to "
                    "read the sky above")
        if st.mask is None or st.height is None:
            if st.last_error is not None:
                return ("no cloud granule has been read yet; the last attempt "
                        "ended in " + st.last_error)
            return "no cloud granule has been read yet"
        return None

    @staticmethod
    def _require_budget(alt_step_deg: float, az_step_deg: float) -> None:
        """Refuse a grid too fine to compute. See :data:`MAX_DOME_RAYS`.

        Stage 4's own ``(0, 45]`` check is left where it is and runs after this
        one, so a step of zero still gets stage 4's sentence about a step being
        a real slice of sky rather than a division by zero here.
        """
        if not (alt_step_deg > 0.0 and az_step_deg > 0.0):
            return                       # stage 4's domain error, not this one
        rows = (90.0 - MIN_USABLE_ALT_DEG) / alt_step_deg
        cols = 360.0 / az_step_deg
        if not (math.isfinite(rows) and math.isfinite(cols)):
            # A DENORMAL STEP IS STILL A CALLER ERROR. ``5e-324`` is positive,
            # finite and inside stage 4's ``(0, 45]``, so it reaches here --
            # and its reciprocal overflows to inf, where ``math.ceil`` raises
            # OverflowError. The route catches ValueError, so the single
            # finest grid anybody can ask for came back as a 500 while every
            # other pathological step -- nan, inf, -1, 1e-300, 1e-8 -- was
            # correctly a 400. The counting is guarded rather than the route
            # widened: the ray budget is the thing that knows this is a grid
            # too fine, and it can say so in the sentence that explains why.
            raise ValueError(
                "an alt step of " + repr(alt_step_deg) + " and an az step of "
                + repr(az_step_deg) + " is more rays than can be counted, far "
                "past the " + repr(MAX_DOME_RAYS) + " this route will compute"
            )
        rays = (math.floor(rows) + 1) * math.ceil(cols)
        if rays > MAX_DOME_RAYS:
            raise ValueError(
                "an alt step of " + repr(alt_step_deg) + " and an az step of "
                + repr(az_step_deg) + " is " + repr(rays) + " rays, past the "
                + repr(MAX_DOME_RAYS) + " this route will compute. The mask "
                "cell under a site subtends more than half a degree at the "
                "heights that matter, so a finer grid resamples one "
                "measurement rather than sharpening the picture"
            )

    def _dome_gate(self) -> asyncio.Semaphore:
        """The one-at-a-time gate of :data:`MAX_CONCURRENT_DOMES`.

        BOUND TO THE RUNNING LOOP RATHER THAN CREATED AT IMPORT. An asyncio
        primitive remembers the first loop it ever waited on and raises
        "bound to a different event loop" for the second, and this service is
        a module-level singleton that the test suite drives from a fresh loop
        per ``TestClient``. Re-binding when the loop changes is safe for the
        same reason it is needed: a different loop is a different process's
        worth of callers, never two sets contending at once.
        """
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_loop is not loop:
            self._gate = asyncio.Semaphore(MAX_CONCURRENT_DOMES)
            self._gate_loop = loop
        return self._gate

    async def dome_payload(self, *, alt_step_deg: float, az_step_deg: float,
                           now: float | None = None) -> dict:
        """``GET /api/cloudmap/dome``: every direction at once.

        Raises ``ValueError`` for a step outside stage 4's ``(0, 45]``, which
        the route turns into a 400 -- that is a caller error and not a state of
        the sky, and the two must not share a status code.

        The grid itself is computed OFF THE EVENT LOOP. The default 2 by 4
        degrees is 3870 rays of up to 75 rungs each; blocking the loop for that
        long on a rig mid-sequence would stall the frame pipeline and the WS
        heartbeat behind a picture of the sky.
        """
        now = self._clock() if now is None else now
        # THE ARGUMENTS ARE CHECKED BEFORE THE SKY IS READ, and the ordering is
        # the point rather than a tidiness. A step of zero is a caller error at
        # every hour of the day; checked after the "have we got a granule yet"
        # branch below, the first ten minutes after a reboot would answer it
        # with a cheerful 200 and "no data yet", and the bug would surface on
        # the night the fetch started working.
        #
        # Stage 4's own checker, imported rather than restated: two copies of
        # "0 < step <= 45" is how two copies come to disagree, and the sentence
        # it raises is the one that explains the domain.
        _require_step(alt_step_deg, "alt_step")
        _require_step(az_step_deg, "az_step")
        self._require_budget(alt_step_deg, az_step_deg)
        st = self.state(now)
        base = {
            "enabled": st.enabled,
            "platform": st.platform,
            "rows": None,
            "alt_start": MIN_USABLE_ALT_DEG,
            "alt_step": alt_step_deg,
            "az_step": az_step_deg,
            "observed_at": _iso_z(st.observed_at),
            "stale": st.stale,
            "cell_km": None,
            "reason": None,
            "credit": credit(st.platform),
        }
        site = self.site()
        reason = self._no_sky(st)
        if reason is not None:
            base["reason"] = reason
            return base
        # ONE AT A TIME, ACROSS ALL CALLERS -- see MAX_CONCURRENT_DOMES. The
        # per-request ceiling above bounds one grid; nothing bounded eight
        # tabs, and eight threads of pure Python do not finish any sooner for
        # having been started together, they just take the interpreter away
        # from the guide loop while they argue over it.
        async with self._dome_gate():
            rows = await asyncio.to_thread(
                dome, site, st.mask, st.height,
                alt_step_deg=alt_step_deg, az_step_deg=az_step_deg)
        base["rows"] = rows
        base["cell_km"] = self._cell_km(site, st.mask)
        return base

    def at_payload(self, *, alt_deg: float, az_deg: float, ahead_s: float,
                   now: float | None = None) -> dict:
        """``GET /api/cloudmap/at``: one look direction, now or ahead.

        ``ahead_s`` of zero is answered by stage 4 and not by stage 5's
        forecast. They differ for a reason worth stating: the forecast refuses
        outright when no motion could be measured, which is the right answer
        for "what will be there in fifteen minutes" and the wrong one for "what
        is there now" -- the present sky is readable from one granule pair
        whether or not the field's motion was.

        ``beam_m`` AND ``cell_km`` TRAVEL TOGETHER. At 30 degrees the beam is a
        couple of hundred millimetres across where it meets a 4 km deck and the
        mask cell is 2.9 by 2.2 km, over a hundred times the area. A payload
        carrying the probability without the ratio would be claiming a
        determination the data cannot support.
        """
        now = self._clock() if now is None else now
        # Checked before the sky is read, for the reason ``dome_payload`` sets
        # out at length, and through stage 4's own checkers for the same
        # reason: one copy of the altitude domain, one of the azimuth check.
        _require_altitude(alt_deg, "alt")
        if not math.isfinite(az_deg):
            raise ValueError("az must be finite, got " + repr(az_deg))
        # A LEAD TIME THAT IS NOT A LEAD TIME IS A CALLER ERROR, refused here
        # rather than passed on. A negative one would silently fall to the
        # present-time branch below and answer "now" to a question about the
        # past, and a NaN one would reach the payload and serialise as the
        # bare token NaN, which is not JSON and which every strict parser on
        # the other end rejects as a syntax error nobody can trace back here.
        if not (math.isfinite(ahead_s) and ahead_s >= 0.0):
            raise ValueError(
                "ahead_s must be a finite number of seconds at or after now, "
                "got " + repr(ahead_s))
        st = self.state(now)
        out = {
            "enabled": st.enabled,
            "platform": st.platform,
            "observed_at": _iso_z(st.observed_at),
            "stale": st.stale,
            "ahead_s": ahead_s,
            "alt_deg": alt_deg,
            "az_deg": az_deg,
            "probability": None,
            "basis": "no_data",
            "crossing_km": None,
            "pierce_lat_deg": None,
            "pierce_lon_deg": None,
            "downrange_km": None,
            "beam_m": None,
            "cell_km": None,
            "quality": None,
            "reason": "",
            "credit": credit(st.platform),
        }
        site = self.site()
        reason = self._no_sky(st)
        if reason is not None:
            out["reason"] = reason
            return out
        if ahead_s > 0.0:
            answer = forecast_at(site, alt_deg, az_deg, st.mask, st.height,
                                 st.motion, ahead_s)
        else:
            answer = occlusion_at(site, alt_deg, az_deg, st.mask, st.height)
        out.update({
            "probability": answer.probability,
            "basis": answer.basis,
            "crossing_km": answer.crossing_km,
            "pierce_lat_deg": answer.pierce_lat_deg,
            "pierce_lon_deg": answer.pierce_lon_deg,
            "downrange_km": answer.downrange_km,
            "beam_m": answer.beam_m,
            "cell_km": (None if answer.cell_km is None
                        else [answer.cell_km[0], answer.cell_km[1]]),
            "quality": answer.quality,
            "reason": answer.reason,
        })
        return out

    def _cell_km(self, site: Site, mask: GranuleWindow) -> list[float] | None:
        """The mask cell's true ground size under the site, or None.

        Under this site the two axes differ by a third, which is the number
        that makes "2 km product" misleading. ``pixel_size_km`` raises for a
        cell whose neighbours are off the earth, and that is a real answer of
        "there is no such distance" -- never a reason to lose the dome.
        """
        try:
            index = lonlat_to_index(mask.spec, site.lat_deg, site.lon_deg)
            if index is None:
                return None
            ns_km, ew_km = pixel_size_km(mask.spec, index[0], index[1])
        except (ValueError, KeyError):
            return None
        return [ns_km, ew_km]


#: Module-level singleton, wired in api/app.py like ``weather_service``.
cloudmap_service = CloudmapService()
