# Native Cameras — On-Box Verification + At-Scope Validation Runbook

**Cameras:** Player One Poseidon-M Pro (IMX571 mono, cooled) = imaging; ZWO ASI220MM = guide.
**Status at authoring (2026-07-20):** framework + both adapters/backends built and unit-tested
(fakes) on the dev box; ZWO ASI binding export-verified against the real `ASICamera2.dll`
present on the dev box (count()=0, no camera attached). NOT yet done: struct-layout
verification with a live camera, Player One binding on-box verification, Player One SDK
license/bundling verdict, and all at-scope validation. `__version__` stays 0.2.6 until this
runbook passes and the 0.2.7 deploy step below is taken.

## Phase 0 — SDK binding verification (before any hardware use)

**ZWO ASI (`cameras/zwo_asi_sdk.py`):** exports already resolve on the dev box. On the box
with the ASI220MM attached, confirm the STRUCT LAYOUT by a live read:
```
python -c "from astrodeck.devices.cameras.zwo_asi_sdk import AsiSdk; s=AsiSdk(); print(s.get_property(0))"
```
Expect a sane `AsiProperty` (name 'ZWO ASI220MM', plausible width/height/pixel size,
bit_depth, gain/offset ranges). Garbage fields => fix `ASI_CAMERA_INFO`/`ASI_CONTROL_CAPS`
against the shipped `ASICamera2.h`.

**Player One (`cameras/player_one_sdk.py`):** UNVERIFIED — do the full on-box pass first.
1. Resolve the **licensing verdict** (docs/hardware/player-one-sdk-licensing.md) — read the
   license inside Camera SDK V3.10.1. Bundle into `vendor/playerone/` only if permitted;
   else pick a fallback (env `ASTRODECK_PLAYERONE_SDK_DIR` / first-run download).
2. Against `PlayerOneCamera.h` V3.10.1, verify: the 17 export names in `_EXPORTS`; the
   `POAConfig` enum integer values (POA_EXPOSURE/GAIN/OFFSET/TARGET_TEMP/COOLER/
   COOLER_POWER/TEMPERATURE/HEATER_POWER/EGAIN); `POAImgFormat` RAW16 value; the
   `POAConfigValue` union; the full `POACameraProperties` + `POASensorModeInfo` layouts;
   and that the **sensor-mode API** (`POAGetSensorModeCount`/`POAGetSensorModeInfo`/
   `POASetSensorMode`) exists and is how LRN is selected.
3. Live read: `PlayerOneSdk().get_properties(0)` and `.sensor_modes(id)` return sane values
   (modes should include the Normal + Low-Noise pair).

## Phase 1 — Enumerate + connect (no vendor software running)

- `plugin_load_report()` shows `zwo-asi` and `player-one` loaded.
- Each backend's `discover()` returns its unit; assign roles in the profile: Poseidon →
  `camera`, ASI220MM → `guide_camera`. Confirm `connect_profile` brings both online and
  `ConnectResult.guide_camera` is the ASI (the orchestrator guide_camera-role generalization).

## Phase 2 — LRN / HCG read noise (photon-free, at the desk)

Cap covered / darkroom. For each: capture ≥3 bias frames (0 s, `light=False`), feed
`astrodeck.imaging.readnoise.read_noise_e(frames, egain)` (egain from
`caps.extra["egain"]`):
- **LRN:** Normal vs LowNoise sampling (same gain 0). LowNoise must be lower.
- **HCG:** gain 124 vs gain 125. Gain-125 read noise should drop toward ~1.36 e-.
Record the numbers here. This also proves the full expose→download→CameraFrame path.

## Phase 3 — Cooling (Poseidon)

Set −10 °C; confirm `get_temperature()` converges to setpoint and `cooler_power()` reports
a plausible duty cycle. Ramp back to ambient before disconnect.

## Phase 4 — Light frames + guider first light (clear night)

- Real light frames through the Poseidon; confirm the preview/stretch + FITS save path and
  cooling stability at setpoint during a short sequence.
- Native-guider first light through the ASI220MM + the native AM5N mount (cross-ref
  `native-guider-first-light-readiness` memory; supervised).

## Phase 5 — Deploy 0.2.7

Only after Phases 0–4 pass: bump `astrodeck/__init__.py` + `pyproject.toml` to 0.2.7; build
via `build_release.py` → scp tarball → `install-0.2.7.ps1` (stop task, freeze, extract to
`releases\0.2.7`, install new deps, `pip --force-reinstall --no-deps`, flip `current`,
restart, `healthz` shows 0.2.7). Keep the prior 0.2.6 release for rollback.
