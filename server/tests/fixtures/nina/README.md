# NINA sequence fixtures

Six pairs for the NINA import design in `docs/flows/nina-import.md` (#766, WP 6.3). No importer exists yet. Each pair is a NINA Advanced Sequencer file and the AstroFlows flow the design says it should import to:

- `<name>.nina.json`: a file in NINA's Newtonsoft shape (`$id`, `$ref`, `$type`, lists as `{"$id", "$type", "$values"}`, children pointing back with `"Parent": {"$ref": ...}`).
- `<name>.expected.json`: a body `POST /api/flows` accepts, `{"flow": {...}}`, plus `"warnings"`, the warnings the importer must return. A later test compares the importer's output with the flow and with the warnings as a multiset (order is not pinned). Warnings carry `code`, `level` and, when about one NINA type, `type`; the wording is the design's, section 4, and the codes are its section 9.

The expected nodes list only the parameters the importer sets. Everything else is the node's created value (`nodes.create_params`), which a later test layers under them before comparing. Coordinates are written the way `catalog/panel_csv._node_ra` and `_node_dec` write them, so the degree sign appears as a JSON escape for U+00B0 and the files stay ASCII.

## Provenance of the shape

Hand-written from NINA's source and from the example templates NINA ships. The files were assembled by a throwaway script that numbers the `$id` and `$ref` values; the script is not kept. They are not exports from a running NINA: the first real file the owner supplies is the check on them (design, question 10).

- Container, item, condition and trigger shapes, and the `$type` spelling: `NINA/Sequencer/Examples/Basic Sequence Target.template.json`, `Basic Sequence Startup.template.json` and `Basic Sequence End.template.json` in NINA (commit `7c9de0c4`), and the serialiser in `NINA.Sequencer/Serialization/SequenceJsonConverter.cs`.
- Current assembly names (`, NINA.Sequencer`, `, NINA.Core`, `, NINA.Astrometry`, `, System.ObjectModel`): the fallback in `JsonCreationConverter.GetType`, which maps the old `, NINA` to those modules. `saved_target_legacy` uses the old spelling; the others the current one.
- Property names and types: the `[JsonProperty]` members of `InputTarget`, `InputCoordinates`, `DeepSkyObjectContainer`, `SequenceContainer`, `SequenceItem`, `TakeExposure`, `LoopCondition`, `TimeCondition`, `WaitForTime`, `WaitLoopData`, `FilterInfo`, `BinningMode` and each trigger class. Enums are integers (no string converter).
- The mosaic's panel numbering and angle: `FramingAssistantVM.CalculateRectangle` and `Coordinates.Shift`.
- Targets are real catalogue objects at their catalogue J2000 coordinates (M31, M33, M42, NGC 7000, NGC 6946).
- No site latitude, longitude or name appears anywhere. NINA sequences do not carry one.
- The plugin namespace `Example.Plugin.Sequencer` and the script path in `unknown_plugin` are invented.

## The pairs

The last column is read by `server/tests/test_w6_3_nina_fixtures.py`: the doctor `danger` issues each expected flow may have, `none` when it may have none. Warnings and notes from the doctor are not listed, because they are not errors.

| Pair | Exercises | Doctor dangers expected |
|---|---|---|
| `single_dso_lrgb` | One DSO container with L, R, G and B as four looped sets and an `AutofocusAfterHFRIncreaseTrigger`. Setup (`CoolCamera`, `UnparkScope`), `Center`, `RunAutofocus`, `StartGuiding`, end (`WarmCamera`, `ParkScope`). Camera offset 50 on every exposure. Guiding with no dither, so `quickDitherEvery` is 0. | none |
| `two_targets_time_condition` | M31 then M33 in series. `WaitForTime` at 21:30 on the Time provider, a `TimeCondition` on each target (00:30 on the first, 03:30 on the last), a `SmartExposure` with a `DitherAfterExposures` of 3. The first becomes a 180 minute `maxTime`, the last the window's clock stop. | none |
| `mosaic_2x2_pa30` | Four sibling DSO containers named "M31 Panel 1" to "M31 Panel 4" at position angle 30, items identical, `CenterAndRotate` in each. Panel centres are the independently laid-out panes of `server/tests/fixtures/telescopius_2x2_pa30.csv`, numbered the way NINA numbers them, so NINA panel 1 is AstroDeck panel 1-2. A `WaitForSunAltitude` of -12 degrees. Imports to one TARGET, 2 rows by 2 columns. Expects the rig's optics to arrive with the request: a 2.0 by 1.33 degree field (the CSV's), recorded as `fovFrom` "supplied with the import". Without optics `fovX` and `fovY` would be 0 and the doctor would refuse the block. | none |
| `meridian_dither_drift` | NGC 7000 in three narrowband sets. Container triggers `MeridianFlipTrigger`, `DitherAfterExposures` of 5, `CenterAfterDriftTrigger` and `RestoreGuiding`. `WaitForTime` on the astronomical dusk provider with a 15 minute offset and a stale cached clock time of 22:00, which the import must ignore. | none |
| `unknown_plugin` | NGC 6946 with an unknown plugin instruction and an unknown plugin trigger, an `AltitudeCondition` of 35 degrees, an `ExternalScript` in the End area, no autofocus anywhere (so `quickAutofocus` is false). The doctor warns that no AUTOFOCUS precedes the loop, which is what the file asked for. | none |
| `saved_target_legacy` | A saved target file (the root is one DSO container) in the old `, NINA` spelling with the legacy `Rotation` of 330 in both the target and `CenterAndRotate`. M42 at a negative declination, and one looped set holding red, green and blue exposures: an interleaved FILTER CYCLE of 12 passes. | none |

## What the doctor says about these flows

Not errors, and not listed above:

- `single_dso_lrgb` and `saved_target_legacy` have no DUSK WINDOW, because their files have no wait, so the doctor warns "TARGET - 'arm' input unwired". The EAA example gets the same warning. The operator starts such a flow by hand.
- Every flow lacks a SESSION REPORT, which the importer does not add: a note.
- `mosaic_2x2_pa30` is shot panel by panel with no loop wire, as NINA runs its containers, and the doctor warns that a night cut short leaves the last panels empty.
- `unknown_plugin` has no AUTOFOCUS, so the doctor warns that focus drift goes uncorrected.
