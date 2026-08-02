# What a wrong row length does to a real photograph (#110)

**Read this first: #110 is still open, and this sheet is not the answer to it.**
What is shown here is that ONE candidate mechanism can produce a picture
matching the words in the report. The same evidence also contradicts the other
half of the report, and that is stated below rather than at the bottom.

`shear_comparison.png` renders the third candidate cause of the Capture preview
drawn "distorted and stretched and shown at an angle. And tiled" on 2026-07-31 —
the download being laid out at a row length the sensor did not apply. Two
earlier diagnoses were published and both were retracted, so this one is shown
rather than asserted.

Every panel is the SAME real sky frame — `../star_noise/star_field.npz`, a
1024×1024 clear-sky field near Deneb with ~200 stars at HFR 3.8 — pushed through
the shipped `AsiCameraAdapter` (with its geometry read-back disabled, i.e. the
adapter exactly as it stood that night) driven by the shipped
`NativeCamera.expose`. Nothing in the generator re-implements the engine, so if
the engine's layout changes these panels change with it. Rendering is the real
preview encoder (`imaging.processing.to_png`, auto-stretch on).

| panel | applied width | what it shows |
| --- | --- | --- |
| A | 1024 (as asked) | the undamaged frame, for comparison |
| B | 1023 | one wrap: stars become diagonal streaks and the field rolls around a single diagonal seam |
| C | 1016 | the same thing eight times over — the frame reads as combed |
| D | 512×512 | **what the preview actually draws** if the camera stayed at twice the requested bin: a blown-out band over black. `auto_stretch` takes its black point from a frame that is three-quarters empty buffer, so the quarter holding the data saturates. Nobody would describe D as tiled |
| E | 512×512, top quarter | D's band **re-stretched on itself** — a second stretch the product never performs. Only here is the "sky twice, side by side" visible |

## Three things this sheet does not show

1. **No single mismatch produces all four reported words.** "At an angle" is
   B/C, a width a few pixels short. "Stretched … and tiled" is D/E, a width
   about half the request. A camera applies one width per exposure, so one
   frame is one of these, not both. Asserted in
   `test_no_single_mismatch_produces_all_four_of_the_reported_words`.
2. **The tiling is only legible after a stretch the product does not do.** D is
   the preview; E is a diagnostic. Asserted in
   `test_what_the_preview_actually_draws_for_the_tiling_case_is_a_blown_out_band`.
3. **This mechanism puts the damage in the FITS as well as the preview.**
   `_shape` returns one array and both consumers encode that object. The report
   was a CLEAN FITS from the same exposure, so this mechanism survives only if
   the clean FITS and the sheared preview were different exposures — a bin-1
   save alongside a bin-2 preview loop would be exactly that, and the night's
   numbers fit (the imaging sensor is 6252 px wide; bin 1 gives 6252, a multiple
   of 4, and bin 2 gives 3126, which is not). Nobody has checked that pairing.
   Asserted in `test_the_fits_would_carry_the_same_damage_so_the_night_is_not_closed`.

The single measurement that would close or kill this is the width the SDK
actually applied for that exposure, and reading it needs the camera.

Regenerate with:

    cd server && .venv/Scripts/python.exe tests/test_camera_roi_shear.py

The arithmetic behind each panel is asserted in `tests/test_camera_roi_shear.py`;
the adapter gap it points at is in `tests/test_camera_roi_readback.py`.
