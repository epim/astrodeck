# Marine-layer early warning — design

Requested 2026-08-04 after the owner watched a marine layer roll in while
AstroDeck reported a clear sky all night.

## The event, and why the current integration cannot see it

`weather.py` asks Open-Meteo for exactly four fields:

```
minutely_15 = cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high
```

On the night of 2026-08-03/04 those reported **0% cloud, including 0% low
cloud, from 01:00 through 04:30**, fetched fresh at 01:08 and not stale. The
owner was outside looking at stratus.

That is not a bug in the fetch. Numerical forecast grids routinely miss coastal
stratus: it is a shallow boundary-layer feature that forms and advects below the
model's resolution, and the cloud-fraction field is the wrong instrument for it.

The same request, extended to fields we do not currently ask for, shows it
plainly. Hourly, same site, same night:

| local | T | Td | T−Td | RH | visibility | wind | dir | cloud_low |
|---|---|---|---|---|---|---|---|---|
| 17:00 | 31.7 | 14.8 | **16.9** | 36% | 53.4 km | 12.8 | 320° | 0 |
| 20:00 | 24.2 | 15.4 | **8.8** | 58% | 30.3 km | 8.9 | 313° | 0 |
| 23:00 | 19.5 | 11.1 | **8.4** | 58% | 31.2 km | 3.4 | 288° | 0 |
| 00:00 | 18.6 | 10.9 | **7.7** | 61% | 28.6 km | 1.5 | 284° | 0 |
| 01:00 | 17.7 | 11.3 | **6.4** | 66% | 25.4 km | 3.3 | 347° | 0 |
| 02:00 | 16.5 | 12.1 | **4.4** | 75% | 20.5 km | 5.6 | 333° | 0 |
| 03:00 | 15.8 | 12.0 | **3.8** | 78% | 19.1 km | 5.5 | 328° | 0 |
| 04:00 | 15.0 | 11.6 | **3.4** | 80% | 17.9 km | 5.2 | 326° | 0 |

Dew-point depression collapses monotonically, relative humidity climbs, and
visibility halves — while the field we watch never moves off zero.

## The model

**A trend detector, not a threshold detector.** At 01:00, when the owner could
see it, the depression was 6.4 °C and RH was 66% — neither is alarming in
absolute terms, and any fixed threshold tuned to fire there would fire on half
of all clear nights. What identifies the event is the *collapse*: 16.9 → 6.4 over
eight hours, still falling, with visibility tracking it down.

Score from three signals, each independently meaningful:

1. **Dew-point depression and its slope.** `T − Td` now, and its change over the
   last 3 h. Falling depression is air approaching saturation.
2. **Relative humidity and its slope.** Corroborates (1) and is more robust when
   temperature is changing quickly at dusk.
3. **Visibility trend.** Falls before stratus is overhead, because the marine
   air arrives before the deck does.

Wind direction is deliberately **not** in the score. The onshore sector is
site-specific, and hard-coding a compass range would make the model wrong
everywhere but one back garden. It can be surfaced as context.

Output a **risk level with the numbers that produced it**, never a bare
verdict — the owner has to be able to disagree with it at 01:00 in the dark.

## Honest limits, which must reach the UI

**This is fitted to ONE night.** N=1 is a hypothesis, not a validated model. It
must not be allowed to abort a run or veto a resume until it has been checked
against more events. Ship it as an **advisory** that says what it saw.

**These are grid-interpolated forecast values, not a local sensor.** They carry
their own lag and bias, and a marine layer that stops two ridges short of the
site will still show in them.

**The camera is the ground truth and it already exists.** `imaging/clouds.py`
measures bright-star count and contrast on real frames, and it was validated on
a genuinely cloudy frame. The correct division of labour:

- **weather → early warning.** It leads the event, and it is available before
  the sky degrades enough to measure.
- **camera → confirmation.** Definitive, but only once the sky is already
  fouled, which is too late to be a warning.

The two must be reported as separate statements. Collapsing them into one
"cloudy: yes/no" throws away the only thing that makes the pair useful — that
one of them is early and uncertain, and the other is late and certain.

## Scope

- Extend `_fetch_open_meteo` to request `temperature_2m`, `dew_point_2m`,
  `relative_humidity_2m` and `visibility`. Keep the existing cloud fields
  untouched; nothing that works today may change behaviour.
- The existing cloud-percentage veto keeps its current semantics exactly. This
  is additive.
- Persist enough history to compute 3 h slopes across a restart.
- Surface on Monitor beside the existing sky panel, with the numbers visible.

## Testing

Tonight's series is the fixture — it is a real labelled event and it belongs in
the test suite verbatim (values only; the site's coordinates must never appear
in any file). The detector must fire on it, and must NOT fire on the 17:00–20:00
stretch of the same night, when the depression was large and falling fast purely
because the afternoon was cooling off. That second case is the one that
separates a trend detector from a slope-triggered false alarm.
