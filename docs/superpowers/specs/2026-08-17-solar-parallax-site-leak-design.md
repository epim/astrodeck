# #203: the planets carry the site, and no field filter can stop them

2026-08-17. The last open security finding.

## The leak

`catalog/solar_system.position` evaluates every body **topocentrically** —
`_observer()` builds an `EarthLocation` from the configured site and hands it to
`get_body`. So every solar-system row is `f(lat, lon, elevation, t)`, and the
rows go to anyone with `view.status`.

Measured on this tree, 2026-08-18 06:00 UTC, at this rig's latitude:

| body | RA/Dec vs geocentric | `distance_km` vs geocentric |
|---|---|---|
| Moon | **3307″** (0.92°) | 713 km |
| Venus | 12.8″ | 1,704 km |
| Sun / Mercury | 7.5″ / 6.1″ | 3,269 / 3,263 km |
| Mars | 4.1″ | 2,829 km |
| Jupiter / Saturn | 1.3″ / 1.0″ | 3,374 / −1,126 km |
| Uranus / Neptune | 0.4″ | 1,598 / −1,781 km |

Only the **Moon** is withheld today (`SITE_DERIVED_BODIES`), on the strength of
its 0.92°. The other eight still ship. Each `distance_km` is the observer's
distance along that body's line of sight; eight of them at eight different
geometries is a trilateration problem with more equations than unknowns. The
module's own comment concedes this and files the decision rather than making it:

> Withholding them too is a judgement call that spans this module,
> `objects.search` and `region.region_rows` at once and is filed rather than
> made here; what is NOT acceptable is a comment asserting the residual is under
> an arcsecond, because it measurably is not.

The same comment states the constraint that decides the fix:

> a filter written against field NAMES cannot withhold f(lat, lon).

That is not rhetoric. `region._annotate` computes the full topocentric row set
with `site_derived=True` and then drops the Moon by `row["id"]` at the end — so
the eight surviving rows carry the site through a gate written to stop exactly
that.

## The fix: give an unprivileged caller a different observer, not a smaller row

Compute **geocentrically** for any caller without `view.site_derived`. The site
is then not withheld from the output; it is **never an input**. No field can
leak what was never used, including fields nobody has written yet.

The mechanism already exists: `_observer()` returns a geocentric
`EarthLocation` when the site is unconfigured, and the row is labelled. This
adds a second reason to take that branch.

**Accuracy cost:** ≤ 12.8″ of position on any planet, ≤ 7.5″ on the Sun. A
search result is a name and a place in the sky; the mount plate-solves. Nothing
downstream can tell.

**The Moon stays withheld.** Its geocentric position is 0.92° out — nearly two
lunar diameters, visibly wrong to anyone who looks up. Serving a wrong marker is
not an improvement on a stated refusal, and `_MOON_WITHHELD_NOTE` already says
why in the user's own terms. (Distinct from the unconfigured-site case, where
geocentric is the *only* answer available and the label is the honest one.)

**The label has to distinguish two reasons.** `topocentric: False` today means
"no site is configured", and `describe()` says "geocentric until you set your
site". Said to a viewer on a fully-configured rig that is simply false — and a
sentence that tells a user to fix a setting that is already correct is its own
small bug. `position()` gains `geocentric_reason` in `{None, "site_unset",
"not_permitted"}`, and the sentence follows it.

## Call sites

1. `solar_system._observer / position / row` — thread `site_derived` in.
2. `objects._solar_system_hits` — already receives the flag; pass it to `row()`
   instead of only using it to skip the Moon.
3. `region.solar_system_rows` — same, **and its module-level cache key must
   include the flag.** `_ephem_key()` covers site and clock only, so without
   this a topocentric set computed for an operator would be served to the
   viewer who asked 30 s later. Two cache entries, not one.
4. `region._annotate` — stop computing `site_derived=True` and filtering by
   name.

## How it is tested

The centrepiece is not a per-field assertion, because per-field assertions are
what the finding says cannot work. It is an **equality test over the whole
row**: compute every body's row at two sites 1000 km apart with
`site_derived=False`, and assert the outputs are identical, field for field,
including fields added later. The same test at `site_derived=True` must show
them DIFFERING — a positive control, or the first test passes for free the day
someone breaks the ephemeris.

Plus: the accuracy bound (a geocentric planet within 30″ of its topocentric
truth, so the degradation stays invisible), the Moon still withheld with its
note, the cache not crossing the capability boundary, and the label naming the
right reason.
