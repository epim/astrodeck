# Classic sky tools

Classic Atlas and Monitor → Sky dome now expose **Sky atlas**, **AR camera**, and
**Horizon line / Edit horizon**. These tools use live data and real persistence;
the separately labeled SkyDome design preview remains illustrative.

Atlas is a single **Sky atlas** at `#/classic/atlas`, opening directly on the
survey without requiring a target. **Survey / AR camera** selects its background.
The funnel filters the survey's catalogue overlay and the camera's objects;
the layer icon controls imagery, objects, and camera overlays. There is no
duplicate settings button or separate list of target buttons beside the camera.
These controls are anchored over the actual survey/camera viewport, with compact
scrollable layer/filter popovers. The background switch shows an aperture to
enter AR and a constellation to return to the survey. In both backgrounds, a compass rose is
dim while phone tracking is off and glows when enabled. Compass-only survey browsing
uses a separate visual centre and makes no camera request or framing writes; stopping
tracking restores the original framing. Tracking stays on when switching backgrounds.
All icons have accessible
action names and tooltips; their gestures do not pan or zoom the sky underneath.
**Layers → Edit horizon line** opens the native editor, including capture,
tracing, saved profiles, and the current camera direction. Monitor's **Sky atlas**
button opens this same workspace; its horizon shortcut still opens a compact editor.
Framing controls stay mounted and retain their draft while hidden. Switching
back to Survey releases the camera; opening the horizon editor releases both sensors.
Survey-to-camera aiming is approximate and never writes the framing coordinates
or sends a mount command. The local preview simulator enables online survey
fetching because it has no offline sky pack; the rig's configuration is untouched.

Finder and dome trajectories are dashed, with repeated forward-time chevrons.
Live paths interpolate hour markers at local clock hours; the selected/bright
dome track shows small clock labels with stems, omitting crowded labels.
The separately marked design preview uses the same visual treatment with its
illustrative clock. These are observer-relative sky paths, not equipment commands.

## Included

- Phone-camera passthrough, compass following, manual map panning, target
  selection, paths, object filters, cloud/horizon/wind layers, and reach list.
- Active-site horizon editing: add, drag, delete, and confirmed clear.
- Saved-location horizon editing, plus an explicit action to apply the location
  and its horizon together. Opening or selecting a profile does not apply it.
- Camera-based photosphere sweep, proposed skyline, and explicit adoption.
- Live classic SkyDome horizon, object paths to dawn, and cloud-drift outlines
  when the source data supports them. The telescope sits at the horizon center.
- Existing capability checks and server persistence routes remain in use.

The map opens without requesting camera access, even when the new UI previously
remembered camera mode. Camera activation requires a button press. Closing or
leaving the finder releases its stream. A photosphere camera granted after the
editor closes is also released. Horizon editing is blocked while loading,
after a failed load, or while a save is pending.

## Shared implementation and deprecation

Classic owns the launchers, modal, navigation, and layout in
`components/sky/ClassicSkyTools.tsx`. It reuses the existing finder/model,
horizon editor, photosphere, and dome-overlay implementations. It does not mount
NextApp or navigate into its shell.

Retiring the next shell must retain or relocate these shared modules and their
dependencies: `next/hubs/sky/finder`, `next/hubs/sky/sheets/horizon.tsx`,
`horizonStrip.ts`, `photosphere.ts`, `next/hubs/weather/dome/domeOverlay.tsx`,
and the imported shared next UI primitives, styles, and pure libraries.
Deleting the entire `next` directory would remove functionality used by classic.

## Validation

Production build, existing finder/horizon/Monitor checks, and classic-host DOM
checks cover explicit camera activation, stream cleanup, staying in classic,
saved-profile selection, late camera grants, and active-horizon refresh.
Desktop and 390 px mobile layouts were inspected in Chrome.

Sensor checks use fixtures. Real phone camera/compass alignment and daylight
photosphere accuracy require device validation. Existing photosphere tracing
assumes a level sweep; it is not a calibrated 3D reconstruction.
