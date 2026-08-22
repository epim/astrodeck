# Cloud occlusion, stage 6a: the live service and its API — design

**Status:** stage 6a specified to implementation precision. The UI panel is
stage 6b and is NOT built from this document.

**Predecessors:** stages 1-5, all shipped (`ac8c72c`, `328b69d`, `cec9f2e`,
`e9ab1f2`, `8cbadec`).

## 1. What stage 6a is

Stages 1-5 are a library nothing calls. Stage 6a makes them live: a poller that
keeps current granules in memory, and three routes that answer questions about
the sky above this rig.

Build `server/astrodeck/cloudmap/service.py` plus routes in the existing
`api/app.py`. Config gains a `cloudmap` block.

**Non-goals.** No UI (6b). No calibration (stage 7). No safety-gate wiring —
the cloud model does **not** vote on whether to observe. Design 1 and the
2026-08-21 rain/cloud split settled that: the frames decide, and a forecast that
can veto a night is how a clear sky gets refused.

## 2. Shape: copy `weather.py`, because it already solved this

`weather.py` is the working example of "a service that talks to the internet on
a timer without hurting anything". Follow it rather than inventing:

- One service class owning fetch, cache and state; a module-level singleton
  wired into the app lifespan.
- A poller task that ticks and **no-ops unless `cfg.cloudmap.enabled` AND the
  site is set**, so a runtime config toggle takes effect within one tick with
  no restart.
- **ZERO outbound requests when disabled**, test-enforced with the `_Boom`
  pattern weather.py already uses.
- **Failures are never cached.** On a failed fetch the previous windows are
  kept and staleness is derived from their age.
- **Logging is outcome-only**: exception type and product. Never a URL, never a
  coordinate. Stage 3 and stage 4 both enforce this; a pierce point is within
  30 km of the rig.

## 3. Poll cadence, and why not 5 minutes

The products update every 5 minutes and cost 4.4 MB a cycle (design 3 section
2.2), which is 53 MB an hour. **Poll every 10 minutes.** The cloud pattern's own
lifetime is under 40 minutes (design 2 section 2.6), so a 10-minute cadence
loses nothing a 5-minute one would have caught, and halves the traffic on a rig
that is also carrying the relay tunnel and, on an imaging night, uploading
frames.

`cloudmap.poll_minutes` is configurable with a floor of 5 and a default of 10.
Below 5 is refused: it cannot produce fresher data and only wastes bandwidth.

## 4. State the service holds

```python
@dataclass(frozen=True)
class CloudmapState:
    enabled: bool
    platform: str                  # "G18" | "G19"
    mask: GranuleWindow | None
    height: GranuleWindow | None
    previous_mask: GranuleWindow | None    # for motion; see below
    motion: Motion | None
    observed_at: datetime | None
    fetched_at: float | None
    stale: bool
    last_error: str | None         # exception TYPE only
```

**`previous_mask` is why the service exists rather than a bare function.**
Motion needs two mask windows separated by `MIN_SEPARATION_S`..`MAX_SEPARATION_S`
(120-1200 s). A stateless endpoint would have to fetch two granules per request;
holding the previous one makes motion free on every tick after the first.

On each successful tick: the old `mask` becomes `previous_mask`, the new one
becomes `mask`, and motion is re-estimated. If the separation falls outside the
band — the previous fetch failed, say, so the gap is 20 minutes — `estimate_motion`
returns `None` and the service keeps `motion = None` rather than the last
vector. **A stale motion vector is worse than none**: it is a confident claim
about a sky that has since changed.

`stale` is true when `observed_at` is older than `3 * poll_minutes`.

## 5. The wind column

`corroborate` needs pressure-level winds, which `weather.py` already has a
connection to Open-Meteo for. **Do not open a second connection.** Extend
weather.py's existing request with
`hourly=wind_speed_{L}hPa,wind_direction_{L}hPa,geopotential_height_{L}hPa` for
L in (850, 700, 500, 400, 300, 250, 200), and expose

```python
def wind_column(self, now: float) -> list[WindLevel]
```

on the weather service. The cloudmap service calls it and passes the result to
`corroborate` as data, so stage 5 stays pure.

An unavailable column is **not** a refutation (stage 5 section 5.2): the motion
is still reported with `column_consulted=False`.

## 6. Routes

All three require an authenticated session. All three are read-only.

```
GET /api/cloudmap
    -> {enabled, platform, observed_at, age_s, stale, last_error,
        motion: {speed_kmh, toward_deg, corroborated, column_consulted,
                 matched_level, peak} | null,
        credit: {source, url}}

GET /api/cloudmap/dome?alt_step=2&az_step=4
    -> {rows: [[p|null, ...], ...], alt_start, alt_step, az_step,
        observed_at, stale, cell_km: [ns, ew], credit: {...}}

GET /api/cloudmap/at?alt=&az=&ahead_s=0
    -> the Occlusion dataclass as JSON, plus credit
```

**`credit` is not decoration.** `tools/credits_registry.py` carries an entry for
NOAA GOES that currently says, in the shipped notices, that there is no screen
naming NOAA and that one should name it when it lands. These routes are how 6b
does that without inventing the string.

**Disabled returns 200, not 404.** `{"enabled": false}` with nulls. A UI needs
to render "off" differently from "broken", and a 404 conflates them.

**A `/dome` request when `mask` is None returns 200 with `rows: null` and the
reason.** Same argument.

`alt_step` and `az_step` are clamped to stage 4's `(0, 45]` and a 400 is
returned outside it, because that is a caller error and not a state of the sky.

## 7. Config

```python
class CloudmapConfig(BaseModel):
    enabled: bool = False
    platform: Literal["G18", "G19"] = "G18"
    poll_minutes: int = Field(10, ge=5, le=60)
    half_px: int = Field(100, ge=16, le=400)
```

Default **off**. A feature that silently starts pulling 26 MB an hour on
somebody's metered connection because they upgraded is a bad citizen.

`platform` defaults to G18 (west) with a comment recording why: from the western
United States the zenith angle is 46.2 degrees against GOES-19's 64.7, which is
a 2.9 km smeared pixel against 4.7 and a 9.4 km parallax error against 19.1
(design 2 section 2.1). East of roughly 100 W the answer flips, and this is the
knob for it.

## 8. Failure behaviour

Nothing here may take the rig down. Every fetch path catches, logs
outcome-only, keeps the previous state, and sets `last_error` to the exception
TYPE. The poller never dies: an exception in a tick is caught, logged, and the
next tick runs.

**The cloud model has no vote.** No route here is consulted by the sequence
engine, the safety gate, or auto-resume. If that changes it is a separate design
with its own argument, and the argument had better be better than the one that
refused two clear nights.

## 9. Tests

`server/tests/test_cloudmap_service.py`, `test_cloudmap_routes.py`.

1. `test_disabled_fetches_nothing` — `_Boom` client, a full tick, no request.
2. `test_disabled_returns_two_hundred_not_a_404`.
3. `test_a_tick_promotes_the_previous_mask_and_estimates_motion`.
4. `test_a_gap_too_long_clears_the_motion_rather_than_keeping_the_old_one` —
   the stale-vector rule of section 4.
5. `test_a_failed_tick_keeps_the_previous_windows`.
6. `test_a_failed_tick_logs_no_url_and_no_coordinate`.
7. `test_staleness_follows_the_observation_not_the_fetch`.
8. `test_the_poller_survives_an_exception_in_a_tick`.
9. `test_the_poll_floor_is_five_minutes` — config rejects 4.
10. `test_the_wind_column_comes_from_the_weather_service_not_a_second_fetch` —
    assert cloudmap makes no Open-Meteo request of its own.
11. `test_an_absent_wind_column_is_not_a_refutation` — `column_consulted`
    False, motion still present.
12. `test_the_dome_route_carries_the_noaa_credit`.
13. `test_the_at_route_reports_the_beam_and_the_cell_together` — the honesty
    pair of design 4 section 4.3 must survive serialisation.
14. `test_a_bad_step_is_a_400_and_a_missing_mask_is_a_200`.
15. `test_no_route_here_is_reachable_without_a_session`.
16. `test_the_cloud_model_does_not_reach_the_safety_gate` — grep the engine and
    the resume gate for any cloudmap import, and assert none. The one
    architectural rule of this stage, kept by a test rather than by intent.

## 10. Seams for 6b and stage 7

6b renders `/api/cloudmap/dome` as a polar canvas with the horizon at the rim,
shows the beam-to-cell ratio beside every probability, names NOAA from the
`credit` field, and offers now / +15 / +30 minutes from `/at?ahead_s=`. It must
draw "off", "stale" and "no data" as three different things.

Stage 7 is calibration: join each captured frame's own cloud verdict to its
pierce point and see whether the model agrees with the camera. That labelled
set is what settles `HORIZON_S`, which stage 5 deliberately left at 1800 s
because the measurement proposing 1200 s did not reproduce.
