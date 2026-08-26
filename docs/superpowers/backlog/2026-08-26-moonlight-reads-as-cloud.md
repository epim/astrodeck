# A clear sky near the moon reads as cloud, and that holds runs

Measured on the rig 2026-08-26 ~00:50 PDT, release 0.3.18, on a genuinely clear
night. Found while validating the cloud model's parallax fix against real
frames -- not looked for.

## The measurement

Moon at alt 32.3, az 191.7. Two 5 s frames, gain 100, minutes apart, same
camera, same focus position:

| field | moon sep | `cloudy` | score | bright_stars | contrast | HFR | star_flux_median |
|---|---|---|---|---|---|---|---|
| Altair | **32 deg** | **true** | **0.859** | 3 | 6.9x | 5.06 | 1,823 |
| Deneb | 65 deg | false | 0.07 | 200 | 21.1x | 2.87 | 95,379 |

The detector's own sentence for the first one:

    "cloudy: 3 bright stars, low contrast (7x noise)"

The GOES cloud model, independently, called Altair the CLEAREST of the four
directions sampled that night: p = 0.016, against Vega 0.044, Deneb 0.072 and
Alderamin 0.072. The satellite and the camera disagree, and the satellite is
right -- it was a clear night.

## Focus is not the confound, and that is what isolates it

The obvious objection is that the Altair frame is softer: HFR 5.06 against
Deneb's 2.87, and `clouds.py`'s own docstring warns that "a badly defocused
frame also lacks bright tight stars".

Alderamin settles it. Same session, **HFR 5.05** -- indistinguishable from
Altair's 5.06 -- and **200 bright stars, clear**. Same softness, opposite
verdict. The variable that separates Altair from Alderamin is not focus, it is
that Altair sat 32 degrees from a moon 50 degrees higher in the sky than
Alderamin's separation of 82.

## Why it matters

The verdict is not decorative. `cloudy` drives the run holds -- see
tests/test_cloud_hold_*.py. So on a clear night, a target near the moon can:

* hold a run that should be exposing,
* shoot hold darks instead of lights,
* and, with the safety gate armed, end the night.

The failure is silent in the worst way: the reason string says "cloudy", the
sky says otherwise, and nothing on screen reconciles them.

## What the module already says, and what it does not

`imaging/clouds.py` names the moon exactly once:

    * A bright *moonlit* cloud deck is still starless, so it reads cloudy --
      the [right answer]

That is moonlit CLOUD, and it is correct. Nothing addresses moonlit CLEAR sky,
where the same mechanism -- a raised background lifting the noise floor so
fewer stars clear `bright_sigma`, and compressing peak-to-noise contrast --
produces the same numbers from the opposite condition. Both readings collapse
to "few bright stars, low contrast", which is the whole of the score.

## What a fix has to do

1. **The rig already knows where the moon is.** `catalog/visibility.py` computes
   moon altitude, phase and target separation for the Tonight screen, and
   astropy is a hard dependency. This is a fact available for free at the
   moment a frame is graded.
2. **Do not simply relax the threshold near the moon** -- that would make a
   genuine moonlit cloud deck, the case the docstring already gets right, read
   as clear. The two must stay distinguishable.
3. A discriminator worth testing: cloud flattens contrast AND suppresses total
   flux, while moonlight raises the background with the flux still there.
   `star_flux_median` differed by a factor of 52 here (1,823 vs 95,379) but
   that conflates altitude, extinction and field richness -- it needs measuring
   against known-cloudy moonlit frames before being trusted.
4. Whatever the rule, the reason string must say which it concluded and why.
   "cloudy: 3 bright stars" with a full moon 32 degrees away is a sentence that
   should mention the moon.

## Not verified

* Whether a hold ACTUALLY fired tonight from this -- no run was in progress, I
  was driving the camera by hand.
* The separation at which it stops mattering. Two points is a direction, not a
  curve. Worth a sweep: fixed exposure, several separations at similar
  altitude, one night.
* Whether moon PHASE matters as much as separation. Tonight's phase was not
  recorded.
