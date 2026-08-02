# What a wrong row length does to a real photograph (#110)

`shear_comparison.png` is the rendered evidence for the third and surviving
candidate cause of #110 — the Capture preview drawn "distorted and stretched and
shown at an angle. And tiled" on 2026-07-31. Two earlier diagnoses were
published and both were retracted, so this one is shown rather than asserted.

Every panel is the SAME real sky frame — `../star_noise/star_field.npz`, a
1024×1024 clear-sky field near Deneb with ~200 stars at HFR 3.8 — pushed through
exactly what the camera stack does: the adapter allocates a buffer from the size
it *requested*, the sensor fills the front of it with rows of the width it
*applied*, and `engine.py::_shape` cuts that buffer at the requested width.
Rendering is the real preview encoder (`imaging.processing.to_png`, auto-stretch
on), not an illustration.

| panel | applied width | what it shows |
| --- | --- | --- |
| A | 1024 (as asked) | the undamaged frame, for comparison |
| B | 1023 | the night's own mismatch to scale: stars become diagonal streaks and the field rolls around one diagonal seam |
| C | 1016 | the same thing eight times over — the frame reads as combed |
| D | 512×512 | what the preview would actually show if the camera stayed at twice the requested bin: a blown-out band over black, because the auto-stretch takes its black point from a frame that is three-quarters empty buffer |
| E | 512×512, top quarter | the same band re-stretched on itself: the sky appears TWICE side by side, squeezed to half height |

Between B/C and E, the mechanism accounts for all four words in the report.

Panel B is the night's numbers to scale: the imaging sensor is 6252 px wide, a
bin-2 loop asks for 3126, and 3126 is not a multiple of 4.

Regenerate with:

    cd server && .venv/Scripts/python.exe tests/test_camera_roi_shear.py

The arithmetic behind each panel is asserted in `tests/test_camera_roi_shear.py`;
the adapter gap it points at is in `tests/test_camera_roi_readback.py`.

**This is evidence for a mechanism, not a finding about the night.** The number
that would settle it is the width the Player One SDK actually applied for that
exposure, and reading it needs the camera.
