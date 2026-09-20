# Prepare at dusk

Issue: [#28](https://github.com/epim/astrodeck/issues/28).

In the classic UI, open **Settings > Safety > Prepare at dusk**. Choose a saved
equipment profile, a Sun altitude, and the imaging temperature used by your dark
frames. Enable automatic preparation and save. The feature is off after an upgrade
until an administrator enables it.

The server must remain running. Every 30 seconds, it checks whether dusk has
arrived at the configured observing location. An unset location never starts
preparation. The default threshold is -12 degrees; the allowed range is -18 to -6
degrees, keeping preparation below the dawn warm-down boundary.

## What happens

1. Wait for an idle rig and the configured weather policy to allow preparation.
2. Connect the selected profile if no equipment is connected. If that profile is
   already connected, keep its connections. Leave a different or partly connected
   rig alone and explain the hold in Settings.
3. Check that the mount and camera connected, that the camera supports cooling,
   and that any enabled, connected safety monitor reports safe conditions.
4. Explicitly switch the cooler on **at the saved imaging temperature**. The last
   device setpoint may be the temperature used for dawn warm-up; it is never used
   as the imaging target.
5. Wait for the measured temperature to stay within 1 degree of the target for
   120 seconds. The cooldown has a deadline based on `cooling.cool_timeout_s`
   (at least 60 seconds), plus the settling interval. Report success only after
   the temperature settles.

Preparation makes no mount-motion requests and starts no new sequence. An
already armed session can resume through the existing recovery service after
preparation succeeds. Manual runs retain their usual cooling checks.

## Ownership and failures

An active or paused sequence, capture, slew, guider, warm-up, video recording, or
another connection takes precedence. Automatic recovery waits while dusk
preparation is pending. New sequences and capture operations are refused while
the dusk profile is connecting; emergency park remains available.
If a run is already active, or you start one during cooldown, that run takes
ownership of preparation for the night and keeps its normal automatic recovery.

Before the first hardware action, the server records the attempt in
`dusk-arm.json` inside its configured data directory. This prevents a server
restart, manual disconnect, or warm-up from causing repeated attempts that
night. A failed connection or cooldown needs operator attention; it is not
retried automatically. Fix the problem in Equipment and start manually, or leave
the feature enabled for the next evening. Disabling the feature stops further
preparation; use the ordinary Warm control to warm a camera already cooling.

There must be enough night left for the bounded connection and cooldown. If dawn
is too close, the service waits for the next evening. Sites without a dusk/dawn
crossing at the selected Sun altitude remain waiting. A missing or corrupt
attempt record is handled differently: a missing file is a first attempt; a
corrupt or unwritable file blocks unattended action.

## API

`POST /api/config` accepts a `dusk` block:

```json
{"dusk":{"enabled":true,"profile_id":"saved-profile-id","sun_alt_deg":-12}}
```

The target remains the existing `cooling.setpoint_c`, avoiding a second competing
imaging temperature. The Settings form saves both blocks together and preserves
the existing warm-down policy. Changing `dusk` requires both `config.backend`
and `config.safety`. `GET /api/dusk/state` requires `view.status` and returns the
current preparation state and a readable explanation. `completed` means a prior
process recorded success tonight; it is not a fresh temperature measurement.

## Verification

Local tests use fake devices, isolated configuration, and controlled time; the
Sun-window test also exercises the real astronomical dusk calculation. Run:

```text
python -m pytest tests/test_dusk_arm.py tests/test_dusk_api.py -q -n0
```

From `ui/`, run the settings interaction test directly:

```text
node --import ./test-css-stub.mjs --import tsx src/components/settings/__tests__/duskStartupDom.test.tsx
```

Before the first unattended hardware night, verify a supervised dusk with the
intended profile: connection results, explicit cooler target, settling status,
and the armed-session handoff. This change has not been deployed to the rig.
