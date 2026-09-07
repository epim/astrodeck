# NGC 604 first night, 2026-09-05/06: real-field fixtures

512x512 crops of last night's light frames from astrotown (Player One
IMX571 mono, 801 mm, 0.97 arcsec/px, bin 1, gain 125), gzip FITS. Each
header carries `VERDICT` (what a human saw in the full frame), `NOTE`,
`SRCFRAME`, `CROPX0/CROPY0` (crop origin in the source frame), `FILTER`,
`EXPTIME`, `GAIN`, `CCD-TEMP`, `PIXSCALE`.

These exist so detectors are graded against what real defects look like,
not synthetic ones. The night's story is in the memory note
`astrodeck-guider-runaway-ngc604` and the triage spec
`docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md`.

| file | verdict | what it shows |
|---|---|---|
| `donut_L60.fits.gz` | defocused | first autofocus sweep landed 74 steps off; every star a donut; grader HFR 4.06 |
| `staircase_G60.fits.gz` | trailed | guider runaway under a pier-flipped calibration; staircase on every star; grader HFR 3.10 ACCEPTED it |
| `jump_R60.fits.gz` | trailed | one ~40 arcsec guide jump then settle; grader HFR 2.98 accepted it |
| `doubleblob_L60.fits.gz` | trailed | one RA jump mid-exposure; double blob on every star; ecc 0.84 |
| `pedrift_L60.fits.gz` | trailed | UNGUIDED 60 s: smooth ~15 px periodic-error drift on every star; ecc 0.87 |
| `tail_Ha300.fits.gz` | tailed | 128 arcsec pull-back in the first 30 s of a 300 s sub: faint curved tail on every star; ecc 0.62 |
| `clean_R60.fits.gz` | clean | guided on a fresh calibration: round stars; grader HFR 2.73, ecc 0.51 |
| `m33core_G60.fits.gz` | galaxy | M33 core / NGC 604: resolved structure the autofocus size metric counts as "sources" |

Two WIDE crops were added on 2026-09-07 for the focus-metric repair, because
both defects it fixes are invisible at 512 px: the discriminators they broke
read the brightness of the frame's brightest stars, and a 512 px crop does not
contain them.

| file | size | verdict | what it shows |
|---|---|---|---|
| `m33field_G60.fits.gz` | 2048x1536 | galaxy, in focus | M33's core AND its star field: 200 detections at grader HFR 3.43. At the 2026-09-06 HEAD `measure_blob` read r80 466 px here (932 px across, published to the Focus panel as "far out of focus") and `star_size` answered 5.97 px from pyramid scale 16 |
| `donutfield_L60.fits.gz` | 1024x1024 | defocused | the same L_0001 as `donut_L60`, wide enough to hold a field of donuts: 43 detections, grader HFR 4.01, and it must stay on the pyramid |

`clean_R60` and `jump_R60` are the same crop window of consecutive R subs,
so their star lists should match star for star.
