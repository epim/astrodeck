# Dome screenshot harness

Renders `SkyDome`'s telescope glyph at several attitudes, widths and yaws, and
screenshots it. Two commands:

```bash
cd ui
npx esbuild tools/domeshot/entry.ts --bundle --format=iife --outfile=tools/domeshot/bundle.js
python tools/domeshot/shoot.py          # the SYSTEM python, which has Playwright
```

The PNG lands beside the script as `tools/domeshot/dome.png`. Both the bundle
and the PNG are git-ignored.

## Why this exists

Issue #67's glyph went through four versions, and every wrong one passed the
whole of `src/lib/__tests__/domeScope.test.ts`:

- a tube billboarded on an arbitrary perpendicular, which stood edge-on to the
  camera at azimuth 120 and projected to an invisible hairline;
- an honestly-3D tube, which foreshortened to an unidentifiable stub anywhere
  low in the south, because the camera is due south and the projection is
  orthographic;
- a full model with tripod, mount head, dew shield and focuser, which at phone
  size read as a tangle of grey rectangles.

Each was found by rendering it and looking at it, and none of them could have
been found any other way: a geometry test can check where the glyph points and
cannot check whether a person can tell what it is.

The harness imports the shipping modules rather than copying them, so it cannot
drift from what the rig draws. It needs the system python (Playwright), the same
interpreter `tools/ui_probe` requires — see that directory's README.

## The two knobs

`TUBE_SCREEN` in `src/lib/domeScope.ts` is the glyph's length as a fraction of
the dome radius. `R_BACK`/`R_FRONT` beside it are the taper; their RATIO is what
carries the direction, and below about 2 the shape stops reading as pointing
anywhere. Change either, re-run the two commands above, and look.
