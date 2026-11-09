# Importing NINA Advanced Sequencer files into AstroFlows

- Date: 2026-11-09
- Status: design for the owner to read. No importer code exists; none is written until the owner has read this (ruling D-L on #766, consolidation plan WP 6.3).
- Issue: #766, "Plan and Flow imports should support NINA Sequencer files." Its body: "Add support to Astrodeck to support NINA sequencer files for importing. These should be translated into AstroFlows."
- Fixtures: `server/tests/fixtures/nina/` (six pairs, each a NINA file and the flow it should import to). Their README lists what each exercises.

## What this is and why

NINA's Advanced Sequencer saves a night as a JSON tree: containers that hold instructions, loop and time conditions, and triggers. An operator who built a night there has already decided the targets, the exposures, the autofocus and dither policy and the start and stop times. This design turns that file into an AstroFlows graph, so the operator does not retype it. Importing is not running: the result is an unsaved draft the operator reviews, together with a list of everything that did not carry over and why. Most of the work is deciding what must be refused or warned about, because a NINA sequence is a program and an AstroFlows graph is a closed vocabulary (`server/astrodeck/flows/nodes.py`: "resist any 'just add a script node' temptation"). The importer maps to nodes that exist and never invents one. Where NINA has no AstroFlows equivalent it drops the item with a named warning, or refuses the file when dropping would invent a night the file did not describe.

Three rules run through every table below. First, nothing is silent: every dropped, approximated or unknown thing produces one warning that names the NINA type. Second, an import never turns on a behaviour the file did not ask for when the flow can say so: dither, autofocus and the window are written explicitly. Third, no site data is read or written: a NINA sequence does not need a latitude or longitude, and the importer ignores any field that would hold one (section 8).

## 1. Scope

| File | Verdict | Why |
|---|---|---|
| A whole sequence, `*.json` saved by the Advanced Sequencer. Root `$type` is `SequenceRootContainer`, with a Start area, a Targets area and an End area. | In | The main case, and the one #766 names. |
| A saved target, `*.json` in NINA's targets folder (`TargetController.cs`). Root is one `DeepSkyObjectContainer`. | In | One target and its instructions: the same reader, a flow with one TARGET. |
| A template, `*.template.json` (`TemplateController.cs`). Root is any container that is not a `SequenceRootContainer` (a DSO container, a sequential or parallel container). | In | Read like a saved target when it holds a DSO container. A template with no DSO container is refused with `no_targets`: it has no target to build a flow around. |
| The framing assistant's mosaic export. NINA adds one `DeepSkyObjectContainer` per panel to the sequence (`FramingAssistantVM.GetDSOContainerListFromFraming`), named "<target> Panel <n>" in the user's language. | In, as a pattern inside the files above | There is no separate file kind: the panels arrive as sibling containers in a sequence, a target file per panel, or a template. Section 5 says when they become one TARGET with a grid. |
| Several files at once (one target file per panel, saved separately). | Out for the first version | One request is one file. Question 9. |
| The Simple Sequencer, `*.xml` (`CaptureSequenceList`). | Out | A different schema that NINA itself migrates into the advanced sequencer. Open the file in NINA, save it as an advanced sequence, import that. |
| NINA's Target Scheduler plugin databases, profile files, the Advanced API's sequence state. | Out | Not sequencer files. They carry equipment, not a night. |
| Files from other tools (ASIAIR plans, Telescopius CSV). | Out | Telescopius CSV has its own door (`catalog/panel_csv.py`, #178). |
| Flat, dark and bias sets, the flat panel instructions, `TrainedFlatExposure`. | In the file, not carried | Calibration is the CALIBRATION QUEUE's and DUSK FLATS's job in AstroFlows. Each is dropped with `calibration_not_carried`. |

The format is detected from the root `$type`, never from the file name. A file that is not JSON, or has no `$type` ending in a NINA container class, is refused with `not_nina`.

## 2. How the file reads

Every fact in this section is read from NINA's source (commit `7c9de0c4`, the same one `docs/native-parity/algorithms/nina-sequencer-triggers.md` cites) and from the example templates NINA ships in `NINA/Sequencer/Examples/`. No real operator file was available (question 10), so the fixtures are written to this shape and the first real file is the check on it.

- **Newtonsoft with type names and references.** `SequenceJsonConverter.cs` serialises with `TypeNameHandling.All` and `PreserveReferencesHandling.All`. Every object carries `$id` and `$type`. A list is `{"$id", "$type", "$values": [...]}`. A child's `"Parent"` is `{"$ref": "<id>"}`. The importer builds a table of `$id`s, resolves every `$ref` except `Parent` (the structure comes from nesting, and a `Parent` reference is a cycle by construction), and refuses a `$ref` that names no `$id` with `bad_reference`.
- **Type names.** `$type` is `"<Namespace.Class>, <Assembly>"`. Files from before the module split say `, NINA` and `System`; current files say `, NINA.Sequencer`, `NINA.Core`, `NINA.Astrometry` and `System.ObjectModel`. NINA itself retries the old names (`JsonCreationConverter.GetType`). The importer reads only the part before the first comma, and only for `NINA.Sequencer.*` types. Data objects (`Target`, `InputCoordinates`, `Binning`, `Filter`, `SelectedProvider`) are read by property name and their `$type` is ignored, so `NINA.Model.InputTarget` and `NINA.Astrometry.InputTarget` both work.
- **Unknown types.** NINA turns a type it cannot resolve into an `Unknown*` placeholder (`SequenceContainerCreationConverter.cs` and its siblings). The importer does the same: section 7.
- **Enums are integers.** No `StringEnumConverter` is applied, so `Comparator` is 0 equals, 1 less than, 2 less than or equal, 3 greater than, 4 greater than or equal, 5 not equal (`NINA.Core/Enum/ComparisonOperatorEnum.cs`), and `ErrorBehavior` is an integer too. `ErrorBehavior` and `Attempts` are not read: AstroDeck's per-item error policy is the engine's.
- **Coordinates.** `InputCoordinates` holds `RAHours` and `RAMinutes` (integers), `RASeconds`, `NegativeDec`, `DecDegrees`, `DecMinutes`, `DecSeconds`, J2000. Right ascension in hours is `RAHours + RAMinutes/60 + RASeconds/3600`. The declination is negative when `NegativeDec` is true or `DecDegrees` is negative, and its magnitude is `|DecDegrees| + DecMinutes/60 + DecSeconds/3600` (NINA stores `-0` degrees by flag alone, `InputCoordinates.cs`). Any non-finite or out-of-range value refuses the file with `bad_coordinates`. The importer writes them with the same functions the panel CSV uses, so a target reads `00h 42m 44.30000s` and `+41 deg 16' 09.0000"`.
- **Progress counters.** `LoopCondition.CompletedIterations` and `TakeExposure.ExposureCount` are how far NINA got, and `ExposureInfoList` on a DSO container is a derived display list. None is read as a plan. The session ledger of the imported flow starts empty. When any counter is non-zero the import says so once (`progress_ignored`).
- **Provider-bound times are a cache.** `WaitForTime` and `TimeCondition` carry `Hours`, `Minutes`, `Seconds` and a `SelectedProvider`. For every provider except the plain Time one, NINA rewrites the three numbers from the provider when the file loads (`WaitForTime.UpdateTime`), so the stored values are the author's site and the author's day. The importer reads the provider class and `MinutesOffset` and ignores the numbers. For the Time provider the numbers are the setting and are read (seconds are dropped).
- **Limits.** The text is at most 4 MiB, JSON nesting at most 64 deep, and the flow it would build at most 400 nodes (`FlowGraph.nodes` `max_length`). Each is a refusal (`too_large`, `too_deep`, `too_many_nodes`). A UTF-8 byte order mark is accepted.

## 3. What the import produces

The result is `{flow, warnings, summary}`. `flow` is an unsaved `FlowRecord`: a name (the root container's `Name`, else "Imported NINA sequence", at most 120 characters), a tagline saying where it came from, and the graph. Nothing is stored until the operator saves it through the ordinary `POST /api/flows`, which also runs the save rules and the doctor. The importer calls neither.

**Parameters.** A node is written as a freshly created node is (`nodes.create_params`, the Created-as column) plus the keys the file decided. The expected files in the fixtures list only the keys the importer sets, and a later test layers `create_params` under them before comparing.

**Layout.** A fixed, boring grid: DUSK WINDOW at x 30, y 70; target number i (from 0) has its TARGET at x 270, y 70 + 300 i and its lane stages 240 to the right of each other; the CONDITION and REFOCUS pair sits 200 below the first capture. Node ids are `n1`, `n2` in creation order, edge ids `e1`, `e2`.

**The lane of one target.** Walking a DSO container's items in file order gives, in file order: an AUTOFOCUS node for the first `RunAutofocus`, a GUIDE node for the first `StartGuiding`, and one CAPTURE LOOP or FILTER CYCLE per exposure set (section 4.4). Wiring: TARGET `target` to the first stage's `run`, then each stage's flow output (`focused`, `guiding`, `complete`) to the next stage's `run`. This is the lane shape the examples and `compile.py` use. With more than one target, the last stage's flow output of target i goes to the TARGET `arm` of target i+1 (the series shape in `tests/fixtures/panel_lane_cases.json`, "second-target-owns-the-capture"), so each stage belongs to its own target. If a DUSK WINDOW exists its `window` output goes to the first TARGET's `arm`. Without a DUSK WINDOW the first TARGET's `arm` stays unwired, the doctor says so (a warn, the same one the EAA example gets), and the operator starts the flow by hand, which is what NINA does too.

**When a DUSK WINDOW is created.** Only when the file carries a start wait, a stop time or an altitude condition (section 4.3). A created DUSK WINDOW always has `minAlt` and `offset` written, `minAlt` 0 and `offset` 0 unless the file said otherwise, because its created defaults (30 degrees, 30 minutes before dusk) are decisions the file did not make. Its `stop` is "Dawn" unless the file gave a clock stop: the window then stops at dawn though NINA would run until the sequence ended. That difference is deliberate (the run parks at dawn) and is covered by the notes on the target's own bounds.

**Flow settings.** Two settings are written, because the nodes' own fields never reach the plan (`to_plan.NODE_SETTINGS`: the GUIDE card's dither and the AUTOFOCUS card's settings "come from Rig > Guider" and "the focuser's own measured sweep geometry"). Both are the same flat keys `wizard.quick` writes, `quickDitherEvery` and `quickAutofocus` (`nodes.py`, `QUICK_DITHER_EVERY`, `QUICK_AUTOFOCUS`). Question 2.

- `quickDitherEvery`: the file's dither cadence in frames (section 4.6). When the file guides and never dithers it is 0 and the import says `dither_cadence`, so the flow does not start dithering every 3 frames because AstroDeck's default says so. GUIDE's own `dither` is written with the same number, so the card and the plan agree. A file with no guiding writes neither.
- `quickAutofocus`: written as `false` only when no target in the file has an autofocus (no `RunAutofocus` in a lane and no autofocus trigger), with `autofocus_not_asked`. If some targets have one and others do not, nothing is written and each target without one gets `autofocus_added`, because AstroDeck focuses at the start of every target.

**Warnings.** `{code, level, type, path, text}`. `level` is `note` (nothing the operator needs to act on: the block does it, or the engine does) or `warn` (something changed, was approximated, or was dropped). `type` is the full NINA class name (`NINA.Sequencer.SequenceItem.Camera.CoolCamera`), empty for a flow-level finding. `path` names the containers from the root ("Targets / NGC 7000"). The codes are closed (section 9), and warnings are de-duplicated by code, type and wording with a count in the text. Refusals are not warnings: they are a 422 with `{detail, code}`, like `POST /api/plans/import`.

## 4. The mapping

Each row ends with what is lost, written as the warning. `{...}` marks a value taken from the file. Only values the operator chose are ever quoted (temperatures, counts, exposure lengths, clock times); positions are never quoted (section 8).

### 4.1 Containers

| NINA type | Becomes | Carried | Dropped, approximated, warning |
|---|---|---|---|
| `SequenceRootContainer` | The flow. | `Name` as the flow name. Its three children in order: `StartAreaContainer`, `TargetAreaContainer`, `EndAreaContainer`. | Triggers and conditions on the root or on the Targets area apply to every target below them, as NINA applies them (a trigger run walks up the parent chain, `docs/native-parity/algorithms/nina-sequencer-triggers.md` 1.2). |
| `DeepSkyObjectContainer` | One TARGET and its lane. Several sibling containers may become one TARGET with a grid (section 5). | `Target.TargetName` as `name` (else the container `Name`, else "Target {n}"); `InputCoordinates` as `ra` and `dec`; `PositionAngle` or `Rotation` as the angle (section 6); the container's `Items`, `Conditions` and `Triggers`. | `ExposureInfoList` is ignored (a derived display list). |
| `SequentialContainer` | Transparent: its items are read in place. | With a `LoopCondition` and exposure items it is an exposure set (4.4). One that holds several DSO containers is a group of targets in order. | Its other conditions and triggers apply to each target inside it. |
| `ParallelContainer` | Read in order, as if sequential. | Each child by its own row. | `approximated` (warn) when it holds an exposure, a slew or a guiding item: "ParallelContainer: its items ran at the same time in NINA and run one after another here." Setup items only (cool and unpark in the Start area) need no warning of their own. |
| `SmartExposure`, `TakeManyExposures` | An exposure set (4.4). | The same fields as a sequential set. | None beyond 4.4. |
| `TrainedFlatExposure`, `TrainedDarkFlatExposure` | Nothing. | None. | `calibration_not_carried` (warn): "{type}: flats and darks are the CALIBRATION QUEUE's in AstroDeck and were not imported." |
| `UnknownSequenceContainer`, any other `$type` | Nothing; its children are not read. | None. | `unknown_type`, section 7. |

### 4.2 Setup and end-of-night instructions

These sit in the Start and End areas, or between targets. None has a node, because the flow vocabulary has no equipment steps and the engine does them on its own.

| NINA type | Becomes | Warning |
|---|---|---|
| `CoolCamera` (`Temperature`, `Duration`) | Nothing. | `cooling_not_carried` (warn): "CoolCamera: cooling to {Temperature} C over {Duration} min is not carried. AstroDeck cools to the rig's own setpoint. Set Target C on the Capture tab so lights match your darks." |
| `WarmCamera`, `ParkScope` | Nothing. | `engine_does_it` (note): "{type}: every run parks the mount and warms the camera at the end of the night, with or without this step." |
| `UnparkScope` | Nothing. | `engine_does_it` (note): "UnparkScope: the run unparks the mount before its first slew." |
| `FindHome`, `SetTracking`, `SlewScopeToAltAz` | Nothing. | `not_carried` (warn): "{type} has no AstroFlows equivalent and was dropped." The alt/az values are never quoted. |
| `MoveRotatorMechanical` | Nothing. | `not_carried` (warn): "MoveRotatorMechanical: AstroDeck turns the rotator to a sky angle, not a mechanical one. Set the angle on the TARGET." |
| `OpenDomeShutter` | A DOME CONTROL node before the first target (`window` to `run`, `open` to `arm`). | None. `bind` stays the created "Bind to mount". |
| `CloseDomeShutter`, `ParkDome` | Nothing. | `engine_does_it` (note): "{type}: the run closes the roof as the dome policy says." |
| `SlewDomeAzimuth`, `FindHomeDome`, `SynchronizeDome`, `EnableDomeSynchronization`, `DisableDomeSynchronization`, `SynchronizeDomeTrigger` | Nothing. | `not_carried` (warn): "{type}: a bound dome follows the mount; there is no step to say otherwise." |
| `OpenCover`, `CloseCover`, `ToggleLight`, `SetBrightness` | Nothing. | `calibration_not_carried` (warn), as above. |
| `MoveFocuserAbsolute`, `MoveFocuserRelative`, `MoveFocuserByTemperature`, `SetSwitchValue`, `DewHeater`, `SetReadoutMode`, `SetUSBLimit` | Nothing. | `not_carried` (warn): "{type} has no AstroFlows equivalent and was dropped." |
| `WaitUntilSafe`, and the conditions `SafetyMonitorCondition`, `LoopWhileUnsafe` | Nothing. | `engine_does_it` (note): "{type}: every run polls the rig's safety monitor and holds or aborts on its own." |
| `Annotation`, `MessageBox` | Nothing. | `annotations_dropped` (note): "{n} annotations and message boxes were dropped." One line for all of them. |
| `ExternalScript` | Nothing. | `script_not_run` (warn): "ExternalScript: AstroDeck does not run scripts, and the script text was not copied." The path is never quoted. |

### 4.3 The window: waits and time and altitude conditions

AstroFlows has one DUSK WINDOW per flow (`start`, `offset`, `startClock`, `stop`, `stopClock`, `minAlt`). NINA spreads the same ideas over waits and conditions. `minAlt` is the per-target start gate, and what a running target does when it sinks below it (`sequence/models.py`, `Schedule.min_altitude_deg`).

| NINA | DUSK WINDOW | Warning |
|---|---|---|
| `WaitForTime`, Time provider, `Hours:Minutes` | `start` "Clock time", `startClock` "HH:MM", `offset` 0. | `converted` (note): "WaitForTime: seconds were dropped; the window opens at {HH:MM}." |
| `WaitForTime`, astronomical dusk provider, `MinutesOffset` m | `start` "Astro dusk", `offset` m. | `converted` (note): "WaitForTime: the provider's cached clock time was ignored; the window opens {m} minutes from astronomical dusk." Nautical dusk gives "Nautical dusk". |
| `WaitForTime`, sunset provider | `start` "Civil dusk", `offset` m. | `approximated` (warn): "WaitForTime: sunset is about half an hour before civil dusk; the window opens {m} minutes from civil dusk." |
| `WaitForTime`, a dawn, sunrise or meridian provider | Nothing. | `not_carried` (warn): "WaitForTime: a wait for a time after midnight has no start form." |
| `WaitForSunAltitude`, comparator 3 (waits while the Sun is above `Offset`) | `start` "Astro dusk" for -18, "Nautical dusk" for -12, "Civil dusk" for -6, `offset` 0. | `converted` (note) for those three. Another value picks the nearest of the three: `approximated` (warn): "WaitForSunAltitude: the Sun at {x} degrees is not a window AstroDeck offers; the nearest is {name}." |
| `WaitForSunAltitude`, any other comparator (waits for the Sun to rise) | Nothing. | `not_carried` (warn). |
| `WaitForAltitude` (above), `WaitUntilAboveHorizon`, and the conditions `AltitudeCondition`, `AboveHorizonCondition`, `Data.Offset` x | `minAlt` x. The largest x in the file wins. | `approximated` (warn): "{type}: carried as the window's minimum altitude of {x} degrees for every target. NINA applied it to one." `AboveHorizonCondition` also says "the custom horizon is not in the file". |
| `WaitForAltitude` (below), `WaitForMoonAltitude`, `MoonAltitudeCondition`, `MoonIlluminationCondition` | Nothing. | `not_carried` (warn): "{type} has no AstroFlows equivalent and was dropped." |
| `WaitForTimeSpan` | Nothing. | `not_carried` (warn): "WaitForTimeSpan: a plain delay has no node." |
| `TimeSpanCondition` on a target (`Hours`, `Minutes`, `Seconds`) | TARGET `maxTime` = the span in whole minutes, rounded up. | `converted` (note). NINA counts the span from the container's start and `maxTime` counts from the target's first attempt: the same boundary. |
| `TimeCondition`, Time provider, on the last target | `stop` "Clock time", `stopClock` "HH:MM". | `converted` (note). |
| `TimeCondition`, Time provider, on an earlier target | TARGET `maxTime` = minutes from the previous boundary to the clock time, wrapping midnight. The previous boundary is the window's clock start for the first target and the earlier target's clock end for the rest. | `approximated` (warn): "TimeCondition: carried as a maximum of {m} minutes from {previous boundary}, counted from the target's first attempt." If the previous boundary is not a clock time, nothing is carried and the warning says `not_carried`. |
| `TimeCondition`, dawn or sunrise provider, on the last target | `stop` "Dawn". | `approximated` (warn) unless the provider is astronomical dawn. |
| Any time condition on an exposure set that has no `LoopCondition` | The file is refused with `unbounded_repeat`. | "A set repeats until {time} with no frame count. Give the set a count in NINA, or add its frames in AstroFlows." NINA repeats the set until the clock says stop. Guessing a count would invent the size of the night. |
| A second start wait, or a wait between targets | Nothing. | `not_carried` (warn): "{type}: the window has one start." |

A TARGET `maxTime` and a frame count together end the target at whichever comes first, which is ruling D-D's and NINA's own meaning of a container with both a loop and a time condition.

### 4.4 Target instructions and exposures

| NINA type | Becomes | Carried | Dropped, approximated, warning |
|---|---|---|---|
| `Center` (`Inherited`) | Part of the TARGET: the block slews and plate-solve centres. | None. Centring tolerance and tries stay at the block's own defaults. | `absorbed` (note): "Center: the TARGET slews and centres before its first frame." When `Inherited` is false and its own coordinates differ from the container's by more than one arcminute: `not_carried` (warn): "Center: AstroDeck centres on the target's coordinates only." |
| `SlewScopeToRaDec` | Part of the TARGET. | None. | `absorbed` (note), and the same difference check. |
| `CenterAndRotate` (`PositionAngle` or `Rotation`) | Part of the TARGET, and the angle (section 6): "Rotate to PA". | The angle. | `converted` (note): "CenterAndRotate: position angle {P} east of north became AstroDeck rotation {r}." |
| `SolveAndRotate` | The angle only: "Rotate to PA". | The angle. | `converted` (note), as above. |
| `SolveAndSync` | Nothing. | None. | `engine_does_it` (note): "SolveAndSync: the run syncs the mount from its own solves." |
| `RunAutofocus` | An AUTOFOCUS node, once per target, for the first one before the first exposure set. | None of its settings. | A `RunAutofocus` between exposure sets: `not_carried` (warn): "RunAutofocus between sets: AstroDeck focuses at the start of a target and when a rule asks. Add a CONDITION and REFOCUS to refocus between sets." |
| `StartGuiding` (`ForceCalibration`) | A GUIDE node, once per target, for the first one. | Its presence: the night guides. | `ForceCalibration` is dropped: the guider decides whether to calibrate. |
| `StopGuiding` | Nothing. | None. | `engine_does_it` (note): "StopGuiding: guiding stops when the target ends." |
| `Dither` inside a loop body | The dither cadence (4.6). | One dither per pass of the loop. | A `Dither` outside a loop has no cadence and is dropped with `not_carried` (warn). |
| `SwitchFilter` (`Filter._name`) | The filter of the exposure set that follows. | The name. | A null `Filter`: `filter_missing` (warn): "SwitchFilter names no filter. The exposure keeps the CAPTURE LOOP default L." |
| `TakeExposure` (`ExposureTime`, `Gain`, `Offset`, `Binning`, `ImageType`) | A CAPTURE LOOP, or a slot of a FILTER CYCLE. | `exposure`; `gain` (a value of -1 means "the camera's default", and the key is then left to the node's default); `bin` from `Binning.X` ("1", "2" or "4"). `goal` is written as 0, because NINA has no integration goal and the node's 12 hours would show a note on every capture. | `Offset` is never carried: `not_carried` (warn): "TakeExposure: camera offset {n} is not carried. The camera driver's offset applies." A `Binning.X` other than 1, 2, 4 uses the nearest and says `approximated`. `Binning.Y` different from `X` uses `X` and says `approximated`. `ImageType` DARK, BIAS or FLAT is `calibration_not_carried`; SNAPSHOT is dropped as `not_carried`. |
| `TakeSubframeExposure` | Nothing. | None. | `not_carried` (warn): "TakeSubframeExposure: AstroDeck shoots full frames." |

**Exposure sets.** A set is recognised by structure, in this order:

1. A `SequentialContainer`, `SmartExposure` or `TakeManyExposures` whose items, ignoring annotations and `Dither`, are one optional `SwitchFilter` and one `TakeExposure`, with a `LoopCondition` of N. It becomes a CAPTURE LOOP with `count` N. With no `LoopCondition` the count is 1.
2. The same container holding two or more `SwitchFilter` plus `TakeExposure` pairs, with a `LoopCondition` of N. It becomes a FILTER CYCLE: `plan` is the slots in file order ("R 180, G 180, B 180"), `cycles` N, `perCycle` 1. That is NINA's interleave (every filter once per pass, N passes), and the cycle is the same arrangement. `gain` and `bin` are cycle-wide, so a slot with a different gain or binning uses the first slot's and says `approximated`.
3. A looped `TakeExposure` with no `SwitchFilter` in its set uses the filter of the closest `SwitchFilter` before it in the same target, else `filter_missing`.
4. Bare `SwitchFilter` and `TakeExposure` pairs outside any container are CAPTURE LOOPs of count 1. Neighbours with the same filter, exposure, gain and binning merge into one count.
5. Anything else inside a loop (a loop of loops, several sets inside one pass, autofocus in the body) is flattened: every `TakeExposure` becomes a CAPTURE LOOP whose count is the product of the iteration counts around it. The order changes from interleaved to one filter after another. `approximated` (warn): "A loop of {N} passes over {k} sets was flattened: counts multiplied, interleaving lost."

A `LoopCondition` on the DSO container itself repeats the whole target N times in NINA. It is read as rule 5: the counts are multiplied.

### 4.5 Triggers

NINA triggers sit in a container's `Triggers` and run their `TriggerRunner` when they fire. AstroFlows has event wires (CONDITION to REFOCUS) and engine behaviours with no node.

| NINA trigger | Becomes | Warning |
|---|---|---|
| `AutofocusAfterHFRIncreaseTrigger` (`Amount` percent, `SampleSize`) | One CONDITION and one REFOCUS for the whole flow: `when` "HFR above (x focus)", `threshold` 1 + `Amount`/100, `window` "5 frames". Every CAPTURE LOOP's and FILTER CYCLE's `frame` output goes to the CONDITION's `events`; `fire` goes to the REFOCUS's `do`. With several triggers the smallest `Amount` is used. | `approximated` (warn): "AutofocusAfterHFRIncreaseTrigger: NINA fits a trend over the last {SampleSize} frames against the best HFR since the last focus. AstroDeck compares each frame's HFR with the HFR at focus (factor {threshold}) and ignores the window. A factor below 1.10 can fire on seeing alone." The threshold must be above 1.0 and at most 5.0 (`to_plan`, GN-08). An `Amount` of 0 or less drops the trigger (`not_carried`); over 400 percent is cut to 5.0. |
| `AutofocusAfterExposures` (`AfterExposures`) | Nothing. | `not_carried` (warn): "AutofocusAfterExposures: a refocus every {n} frames has no node. AstroDeck refocuses at the start of a target and on a rule." |
| `AutofocusAfterTimeTrigger` (`Amount` minutes) | Nothing. | `not_carried` (warn), same shape with the minutes. |
| `AutofocusAfterFilterChange` | Nothing. | `not_carried` (warn): "AutofocusAfterFilterChange: AstroDeck does not refocus on a filter change." |
| `AutofocusAfterTemperatureChangeTrigger` | Nothing. | `engine_does_it` (note): "AutofocusAfterTemperatureChangeTrigger: AstroDeck's temperature refocus (`refocus_on_temp_delta_c` in the rig's standards) is set on the rig, not in the flow." |
| `DitherAfterExposures` (`AfterExposures`) | The dither cadence (4.6). | `converted` (note): "DitherAfterExposures: dithering every {n} frames." |
| `RestoreGuiding` | Nothing. | `engine_does_it` (note): "RestoreGuiding: the engine restarts guiding itself when it is lost." |
| `MeridianFlipTrigger` | Nothing. | `engine_does_it` (note): "MeridianFlipTrigger: AstroDeck flips on its own; the flip settings are in the NINA profile, not in the file." When the file has none: `meridian_flip_added` (warn): "This file has no meridian flip, and AstroDeck flips a German mount on its own: a flow cannot turn that off." |
| `CenterAfterDriftTrigger` (`DistanceArcMinutes`, `AfterExposures`) | Nothing. | `not_carried` (warn): "CenterAfterDriftTrigger: re-centring after {d} arcminutes of drift every {n} frames has no node. AstroDeck centres at a target's start and after a flip or a hold." |
| Any other `$type` | Nothing. | `unknown_type`, section 7. |

### 4.6 Dither cadence

The cadence is every `DitherAfterExposures.AfterExposures` in the file and one per loop pass for a `Dither` in a loop body. One value, or all equal, is used. Different values use the most common, the first seen on a tie, with `approximated` (warn): "Different dither cadences ({list}); {n} is used for every target." None, with guiding present, is 0 (`dither_cadence`, note).

## 5. Mosaics

NINA's framing assistant makes one DSO container per panel. The importer joins containers into one TARGET with a grid, rather than six unrelated targets, when all of these hold, and otherwise leaves them separate:

1. They are siblings (the same Targets area, or the children of one template or group).
2. Their names end in an integer, and in file order the integers run 1, 2, 3 to n with a common text before them. The word NINA puts before the integer ("Panel") is translated, so it is never matched. Containers the operator renamed fail this test and import as separate targets: the conservative direction.
3. Their items are structurally identical: the same `$type` sequence, the same exposure, gain, binning, filter and count in every set (names and coordinates excluded). The framing assistant clones one template, so this holds for its output.
4. Their position angles agree. Equal within half a degree is the normal case. With NINA's "preserve alignment" the angles drift by the meridian convergence: all within 5 degrees is accepted, the most common angle is used, and the import says `angles_differ` (warn).
5. Their centres fit a rigid grid. Project every centre on the tangent plane about their mean direction, rotate by the angle into the frame's axes, and sort the distinct positions along each axis (two positions are one when they differ by less than 5 percent of the median step). That gives the columns and rows. Every container must land on its own cell, the grid must be at most 10 by 10 (`GRID_MAX`), and a missing cell is allowed and becomes `skip`. Fit the centre and check the layout against it with the machinery `catalog/panel_csv.py` already has (`_fit_centre`, `_layout`, `RIGID_TOLERANCE`, 2 percent of the smaller field), so the NINA path and the Telescopius path agree to the arcsecond.

When a test fails, the containers import as separate targets, and the import says `mosaic_not_a_grid` (warn) with the panel count, the test that failed and, for the grid test, how far the worst panel sat from its cell.

**What the grid carries.** `rows`, `cols`, `skip` (the missing cells, `r-c` text), `angle` "Rotate to PA" with the rotation if the items hold a `CenterAndRotate`, else "Camera fixed at PA" (a mosaic laid out at "Any angle" will not tile, doctor M2), `ra` and `dec` from the fitted centre, and `name` from the names' common text with its last word dropped when another word remains ("M31 Panel 1" gives "M31"). `order` is "Grid order", and the panels are shot panel by panel with no loop wire, which is how NINA runs its containers. The lane stages come from the first panel's items.

**Overlap and field.** The NINA file holds no camera field. The request may carry the rig's optics (`optics: {fov_x_deg, fov_y_deg, from}`, the same field the framing sheet takes from the active profile). With it, the importer sets `fovX`, `fovY`, `fovFrom` to the text it was given, and `overlap` = `1 - step / field`, from the spacing of adjacent panel centres along the columns. It also computes the overlap along the rows. When the two disagree by more than 2 percentage points the rig's camera is not the one NINA framed for: `overlap_mismatch` (warn), the horizontal value is used, and the block needs re-framing. Without optics, `fovX` and `fovY` stay 0 and `overlap` stays at the default, with `frame_this_block` (warn): "Panels are tiled from the camera's field and this file records none: frame this block before running." The doctor refuses a mosaic with no field as a danger, which is the right outcome.

**Panel numbering reverses.** NINA numbers panels row-major from the top-left of the frame. `Coordinates.Shift` negates the horizontal offset (`ShiftStenographic`: `deltaXAngle = -deltaX`), so at angle 0 NINA's panel 2 lies west of panel 1, and the framing chart has east on the left. AstroDeck's columns step east at rotation 0 (`compute_mosaic`; Telescopius' too). Row and column are therefore found from the geometry and never read from the number: reading NINA's number as AstroDeck's column would mirror the grid. Worked case, the 2 x 2 at position angle 30 in the fixtures (`mosaic_2x2_pa30`): NINA panel 1 is AstroDeck panel 1-2, 2 is 1-1, 3 is 2-2, 4 is 2-1. The import says `panels_renumbered` (note) with that mapping.

This was checked, not only read. NINA's `CalculateRectangle` and `ShiftStenographic` were re-evaluated for a 2 x 2 of 2.0 by 1.33 degree panes at 20 percent overlap about M31, with the frame turned 330 degrees clockwise (the clockwise rotation NINA stores for a `PositionAngle` of 30). They give the four centres of `telescopius_2x2_pa30.csv` to 0.02 seconds of right ascension and 0.3 arcseconds of declination, and in the order above: NINA's first panel is the CSV's second, NINA's second the CSV's first, its third the CSV's fourth, its fourth the CSV's third. So a NINA grid at position angle 30 and an AstroDeck grid at rotation 330 are the same four places on the sky. The fixture `mosaic_2x2_pa30` uses those centres in NINA's order.

## 6. Angles

AstroDeck's `rotation` increases from north through west, the sense of FITS CROTA2 (`catalog/panel_csv.py`, `CONVENTION`). NINA's `PositionAngle` is the position angle of the frame's top, east of north, which is the Telescopius column "Position Angle (East)". The evidence is NINA's own source: the framing assistant draws the frame turned `360 - PositionAngle` clockwise (`FramingAssistantVM.cs`, `RectangleRotation = 360 - dso.RotationPositionAngle`), the chart has east on the left, and the field NINA shows the operator goes through `ReverseAngleConverter` (`FramingAssistantView.xaml`). A frame turned clockwise by R on a chart with east on the left has its top at position angle 360 - R east of north, so the stored number is the position angle east of north.

**The conversion, stated once:** `rotation = (-PositionAngle) mod 360`, held to six places, `-0.0` folded to `0.0`. That is `catalog/panel_csv.rotation_from_pa_east`, and the importer calls it, so the Telescopius path and this path cannot disagree.

Worked example. `PositionAngle` 30 gives rotation 330. That is the 2 x 2 in `server/tests/fixtures/telescopius_2x2_pa30.csv`, whose header says "AstroDeck rotation 330", and in the fixtures `mosaic_2x2_pa30` imports to rotation 330. `PositionAngle` 0 is rotation 0, north up, a real angle: it is "Rotate to PA" with rotation 0 when the file has a `CenterAndRotate`, never -1. `PositionAngle` 100 gives rotation 260, and because 90 < 100 < 270 the top points toward the south and the import adds `half_turn` (warn) with the same words as `HALF_TURN_WARNING`.

Older files carry `Rotation` instead (a write-only property: `InputTarget.DeprecatedRotation`, `CenterAndRotate.DeprecatedRotation`; `PositionAngle = 360 - Rotation`). Substituting: `rotation = 360 - PositionAngle = Rotation`. The legacy number is already AstroDeck's. The fixture `saved_target_legacy` has `Rotation` 330 and imports to rotation 330.

Which angle mode a target gets: a `CenterAndRotate` or `SolveAndRotate` in its items gives "Rotate to PA"; none, with a single target, gives "Any angle" (rotation left at -1); none, in a mosaic, gives "Camera fixed at PA".

**The half turn that remains.** NINA's own solver reports a position angle that differs from the raw ASTAP `CROTA2` by a half turn (`NINA.Platesolving/Solvers/ASTAPSolver.cs`, `PositionAngle = 360 - (wcs.Rotation - 180)`; `docs/native-parity/algorithms/nina-platesolving.md`), and AstroDeck stores the raw `CROTA2` as its sky angle (#145, still open: the sign was measured, the pier-east check was not). The conversion above is right for the file. Whether a rotator commanded to that angle puts the frame the way NINA would depends on #145. Question 1.

## 7. Plugins and unknown types

NINA loads plugin instructions, conditions, triggers and containers by `$type`. A plugin that is not installed, or an instruction AstroFlows has no row for, is an unknown type. The import:

- drops the object and everything inside it (an unknown container's children are not read: their meaning depends on a container whose rules are unknown);
- adds `unknown_type` (warn): "{type}: unknown to AstroDeck (a plugin, or a NINA type this importer has no row for). It and its {n} children were dropped." The `type` is the full class name with the assembly removed, and it is quoted every time;
- never reads or echoes the unknown object's properties (an unknown object could hold paths, tokens or positions);
- reads the neighbours as if it were not there. An unknown item inside a loop body that would otherwise be an exposure set leaves the set recognised, and the warning names where it stood. An unknown condition leaves the container's other conditions in force. An unknown trigger changes nothing else.

The fixture `unknown_plugin` carries `Example.Plugin.Sequencer.ParkAndWait` (instruction) and `Example.Plugin.Sequencer.RotateOnWind` (trigger): invented names, not a real plugin.

## 8. Privacy

A NINA sequence does not need a site, and the importer treats any site field as foreign. The source shows that a sequence's `WaitLoopData` serialises only `Coordinates`, `Offset` and `Comparator` (latitude and longitude are not `JsonProperty`), so a normal file holds no site, but three things in a file can still follow the operator's site, and the importer handles each:

- provider-bound `Hours`, `Minutes`, `Seconds` are a cache of the author's site and day (section 2). They are ignored for every provider but Time.
- `SlewScopeToAltAz` and any alt/az, hour angle or sidereal-time field. The instruction is dropped and its values are never quoted in a warning, in the summary, in a log line or in an error. A mount's alt/az at home or park is the site latitude (#140).
- a field named `Latitude`, `Longitude` or `Elevation`, or a site name, wherever it appears. It is never read and never echoed. The fixtures contain none, and the validation test fails if one appears in them.

Target coordinates are catalogue positions and are carried: they are the point of the file. Warning texts quote only what the operator chose: temperatures, counts, exposure lengths, clock times, angles. The route's response and the importer's log lines are held to the same rule by a test that imports every fixture with a made-up observer value planted in an ignored field and searches the whole output for it.

## 9. The warning catalogue

The codes are closed. A test reads this table and checks that every code the fixtures expect is in it, at the level written here.

| Code | Level | What it says |
|---|---|---|
| `absorbed` | note | The TARGET block does what this step did. |
| `converted` | note | The value was carried in AstroDeck's form (a position angle, a clock time, a cadence). |
| `engine_does_it` | note | The run does this on its own, with or without a node. |
| `annotations_dropped` | note | Comments and message boxes were dropped, with a count. |
| `progress_ignored` | note | NINA's own progress counters were dropped; the ledger starts empty. |
| `dither_cadence` | note | The file guides and never dithers: the cadence is 0. |
| `autofocus_not_asked` | note | The file has no autofocus: the flow's autofocus is off. |
| `autofocus_added` | note | This target had no autofocus, and AstroDeck focuses at the start of every target. |
| `panels_renumbered` | note | Panel numbers changed, with the mapping. |
| `approximated` | warn | Carried as the nearest AstroFlows thing, with how it differs. |
| `not_carried` | warn | No AstroFlows equivalent: dropped. |
| `cooling_not_carried` | warn | The camera's setpoint is the rig's, not the file's. |
| `calibration_not_carried` | warn | Dark, flat and bias steps belong to the calibration queue. |
| `script_not_run` | warn | A script step was dropped and its text not copied. |
| `unknown_type` | warn | A plugin or unrecognised type was dropped. |
| `meridian_flip_added` | warn | The file has no meridian flip and the engine flips anyway. |
| `filter_missing` | warn | An exposure names no filter; the node default is used. |
| `half_turn` | warn | The frame's top points south: check the angle against the camera. |
| `angles_differ` | warn | Panels carry different angles; the most common is used. |
| `overlap_mismatch` | warn | The rig's camera is not the one NINA framed for. |
| `frame_this_block` | warn | The file records no camera field; frame the block before running. |
| `mosaic_not_a_grid` | warn | Panel-like containers do not form a grid and import separately. |

Refusals, as the `code` of a 422: `not_nina`, `too_large`, `too_deep`, `too_many_nodes`, `bad_reference`, `bad_coordinates`, `no_targets`, `unbounded_repeat`.

## 10. Where it would live

A plan for a later work package, not code.

**Files.**

- `server/astrodeck/flows/nina_import.py` (new, pure: text in, result out, no I/O and no clock). Functions: `import_nina(text, *, optics=None) -> NinaImport`; `load_tree(text)` (size, depth, byte order mark, `$id` table, `$ref` resolution, `Parent` dropped); `classify(root)`; `read_target(container)`; `read_items(container)` and `exposure_sets(items)` (4.4 rules in order); `read_conditions(...)` and `read_triggers(...)` (4.3, 4.5); `window_from(...)`; `cadence_from(...)`; `build_graph(...)` (layout, wiring, settings). `Warn` is a frozen dataclass; the catalogue is constants checked against section 9.
- `server/astrodeck/flows/nina_mosaic.py` (new): `group_panels(containers)` (tests 1 to 4), `grid_from_centres(centres, pa_deg, optics)` (test 5 and the overlap). It reuses `catalog/panel_csv.py`: `_fit_centre`, `_layout`, `_mean_direction`, `rotation_from_pa_east` and `HALF_TURN_WARNING` become public names there, and `parse_csv`'s fit is split into a function both callers use.
- `server/astrodeck/api/app.py`: `POST /api/flows/import/nina`, declared before `/api/flows/{flow_id}`, with `CAP_CONTROL_CAPTURE` like `POST /api/flows` and a body of `{text, optics}`. It returns `{flow, warnings, summary}` unsaved and answers a refusal as 422 `{detail, code}`. The RBAC tests (`test_rbac_boot_assertion.py`, `test_rbac_enforcement.py`, `test_api_fence.py`) list it.
- `ui/src/lib/flowsApi.ts`: `importNina(text, optics)`. `ui/src/components/flows/NinaImportSheet.tsx`: the review sheet, warnings grouped warn first and then note, each with its path, and SAVE FLOW or CANCEL. `ui/src/components/flows/FlowLibrary.tsx`: an IMPORT NINA SEQUENCE card next to NEW FLOW. `ui/src/components/sequence/PlanLibraryPanel.tsx` with `ui/src/lib/planFile.ts` (`isNinaSequence(raw)`): the plan import door recognises a NINA file and points the operator to the Flows door, so there is one translation (question 3).

**Tests** (each named for what it holds; names are proposals).

- `server/tests/test_nina_import_fixtures.py`: every `*.nina.json` imports to its `*.expected.json` (record and warnings as a multiset).
- `test_nina_import_reading.py`: `$ref` resolution and `Parent`, both type-name spellings, integer enums, coordinates including `NegativeDec` with 0 degrees, the provider cache ignored, progress counters ignored.
- `test_nina_import_sets.py`: one case per rule in 4.4, including the flattened loop and a loop with an unknown item.
- `test_nina_import_window.py`: every row of 4.3, including a midnight wrap, the largest `minAlt`, and `unbounded_repeat`.
- `test_nina_import_triggers.py`: every row of 4.5, with the HFR factor bounds and the dither cadence disagreement.
- `test_nina_import_mosaic.py`: a regular grid, a grid with a missing cell, an irregular grid (separate targets), NINA's reversed column order, the angle drift of preserved alignment, overlap from optics and the mismatch.
- `test_nina_import_angles.py`: the worked cases of section 6 and the legacy `Rotation`, the same numbers as `test_w16_panel_csv.py` for 30 and 330.
- `test_nina_import_refusals.py`: size, depth, node cap, dangling `$ref`, not NINA, no targets.
- `test_nina_import_privacy.py`: the planted observer value never appears in the response, a warning or a log line, and no alt/az value is quoted.
- `test_nina_import_route.py`: roles, 422 codes, and that nothing is saved.
- UI: `ninaImportSheet.test.tsx` (warn before note, SAVE only after the review) and `planFile.test.ts`.

Mutants for those tests would be named in the work package: skipping the angle conversion, reading NINA's panel number as the column, reading the cached provider time, and quoting an ignored field.

## 11. Fixtures

`server/tests/fixtures/nina/`, six pairs and a README:

| Pair | Exercises |
|---|---|
| `single_dso_lrgb` | One target, LRGB as four sets, an HFR autofocus trigger, setup and end instructions. |
| `two_targets_time_condition` | Two targets in series, a clock wait, a clock condition on each, a smart exposure with a dither trigger. |
| `mosaic_2x2_pa30` | Four framing-assistant panels at position angle 30, NINA's numbering, a Sun altitude wait. |
| `meridian_dither_drift` | Meridian flip, dither, centre-after-drift and restore-guiding triggers, a dusk-provider wait with a stale cached time. |
| `unknown_plugin` | An unknown instruction and trigger, an altitude condition, an external script, no autofocus. |
| `saved_target_legacy` | A saved target file in the old spelling, `Rotation`, a negative declination, an interleaved RGB loop. |

Each `*.expected.json` is a body `POST /api/flows` accepts (`{"flow": ...}`) with the warnings the importer must return beside it. `server/tests/test_w6_3_nina_fixtures.py` checks them now, before any importer exists.

## 12. Open questions, with a recommended answer

1. **The angle.** Import as `rotation = -PositionAngle`, the same as the Telescopius path, with the note on every converted angle and the half-turn warning for tops that point south? NINA's solver adds a half turn the file does not show (section 6) and AstroDeck's rotator sense is #145. Recommended: yes, and do not trust a commanded rotation from an imported file until the pier-east check on #145 is done.
2. **The two flow settings.** May the importer write `quickDitherEvery` and `quickAutofocus`? `nodes.py` says `wizard.quick` is the only writer, and the names say "quick". Without them the plan dithers every 3 frames and focuses at every target whatever the file said. Recommended: yes. The key names stay (renaming them is a migration), and the comment in `nodes.py` is reworded to say the importer writes them too.
3. **The plan import door.** The issue title says "Plan and Flow imports". A NINA file becomes a flow, not a plan. Recommended: one importer, in Flows. The plan import door recognises a NINA file and points to it, rather than a second translation into plans.
4. **Draft or saved.** Recommended: the import returns an unsaved draft that opens in the editor after the review sheet, and nothing is stored until the operator saves.
5. **The HFR threshold.** NINA's default of 5 percent is a trend, AstroDeck's is a single frame, so 1.05 can refocus on seeing alone. Recommended: carry the number as written and warn below 1.10 (section 4.5), rather than raise it silently. The alternative is a floor of 1.15, with a warning when it applies.
6. **Clock bounds on earlier targets.** An earlier target's clock condition becomes a `maxTime` counted from the previous clock boundary, an approximation. Recommended: approximate when the boundary is a clock time, refuse the repeat-with-no-count shape (`unbounded_repeat`), and say so.
7. **An end time the file lacks.** NINA runs until the sequence ends and AstroDeck's window stops at dawn. Recommended: keep Dawn and say so in the summary (not a warning per target).
8. **Progress.** NINA's counters are ignored and the ledger starts at zero. Recommended: yes. A half-shot NINA night is not continued from the file.
9. **One file per panel.** Recommended: not in the first version; accept several files in one request later, grouped by the same tests.
10. **A real file.** The fixtures are written from the source and the shipped examples. Recommended: the owner supplies two real files, one whole sequence that uses a plugin and one framing-assistant mosaic, with the target names changed if wished, and they replace or join the fixtures before the importer is built. This also answers whether NINA writes a disabled instruction into the file at all: the source marks no `Status` property for serialisation, so the importer as designed has no way to skip a disabled item, and the first real file shows whether one needs a rule.

## 13. Sources

NINA, commit `7c9de0c4202f2d054f58b0cb6b15d561f8b6b4b2`, read from a local clone (not part of this repository); the paths below are relative to that clone. NINA is MPL-2.0: no NINA source is copied into this repository. The type names, property names and numbers above are facts about a file format, and the fixtures are hand-written to that shape.

- Serialisation: `NINA.Sequencer/Serialization/SequenceJsonConverter.cs`, `JsonCreationConverter.cs`, `SequenceContainerCreationConverter.cs`, `SequenceItemCreationConverter.cs`, `SequenceConditionCreationConverter.cs`, `SequenceTriggerCreationConverter.cs`.
- File kinds: `NINA.Sequencer/TemplateController.cs` (`.template.json`), `TargetController.cs` (`.json`), `NINA/ViewModel/Sequencer/Sequence2/Sequence2VM.cs` (sequence `.json`), `NINA/Sequencer/Examples/Basic Sequence {Startup,Target,End}.template.json` (shapes).
- Containers: `NINA.Sequencer/Container/{SequenceRootContainer,TargetAreaContainer,StartAreaContainer,EndAreaContainer,DeepSkyObjectContainer,SequentialContainer,ParallelContainer,UnknownSequenceContainer}.cs`, `SequenceItem/Imaging/{SmartExposure,TakeManyExposures}.cs`.
- Targets and coordinates: `NINA.Astrometry/InputTarget.cs`, `InputCoordinates.cs`, `Coordinates.cs` (`Shift`), `WorldCoordinateSystem.cs`.
- Instructions: `NINA.Sequencer/SequenceItem/{Camera,Telescope,Platesolving,Autofocus,Guider,FilterWheel,Imaging,Utility,Dome,FlatDevice,Focuser,Rotator,Switch,SafetyMonitor}/`, `UnknownSequenceItem.cs`.
- Conditions: `NINA.Sequencer/Conditions/{LoopCondition,TimeCondition,TimeSpanCondition,AltitudeCondition,AboveHorizonCondition,SunAltitudeCondition,MoonAltitudeCondition,MoonIlluminationCondition,SafetyMonitorCondition,LoopForAltitudeBase}.cs`; time providers `NINA.Sequencer/Utility/DateTimeProvider/`.
- Triggers: `NINA.Sequencer/Trigger/{Autofocus,Guider,MeridianFlip,Platesolving,Dome}/`, `UnknownSequenceTrigger.cs`; semantics in `docs/native-parity/algorithms/nina-sequencer-triggers.md`.
- Framing assistant: `NINA/ViewModel/FramingAssistant/FramingAssistantVM.cs` (`CalculateRectangle`, `GetDSOContainerListFromFraming`, `RectangleRotation`), `NINA/View/FramingAssistantView.xaml`.
- Solver angle: `NINA.Platesolving/Solvers/ASTAPSolver.cs`.

In this repository: `server/astrodeck/flows/{nodes,models,compile,to_plan,doctor}.py`, `ui/src/components/flows/nodeDefs.ts`, `server/astrodeck/catalog/panel_csv.py`, `server/tests/fixtures/telescopius_2x2_pa30.csv`, issues #145, #178, #766.
