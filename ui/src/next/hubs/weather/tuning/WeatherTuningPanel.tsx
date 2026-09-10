// WeatherTuningPanel.tsx - the body of WEATHER > settings, rebuilt in the
// design's own vocabulary (wave R7, T-R7-15; plan section 3.F18 and the cutover
// table in section 7).
//
// It replaces `components/settings/WeatherPanel.tsx` at this sheet's mount.
// The legacy file is NOT edited and NOT deleted: `#/classic` still renders it.
//
// FOUR THINGS CHANGED, AND WHY EACH IS NOT A RE-SKIN.
//
// 1. NO SAVE BUTTON FOR THE THREE SETTINGS. The legacy panel held a draft of
//    the whole block behind one "Save weather settings" press, so a toggle a
//    user had flipped LOOKED armed while the rig still had the old policy, and
//    an unsaved draft survived every re-render with only a small grey
//    "Unsaved changes" line to say so. Each control here owns exactly one
//    field and writes immediately, echoing the whole block (the server replaces
//    `weather` wholesale). `NumberField` holds its own text and commits only at
//    blur or Enter, so typing 1 then 0 then 0 does not fire three writes on the
//    way to 100.
//
//    The API KEY keeps its own explicit button, and that is deliberate: a
//    secret must be sent on purpose, never by a focus change, and a half-typed
//    key that auto-committed at blur would be stored on the rig.
//
// 2. FIVE NATIVE `disabled` ATTRIBUTES ARE GONE (`WeatherPanel.tsx` :121, :135,
//    :147, :165, :175, :199 - the toggle, both numbers, the key box, "Clear
//    key" and Save). A `disabled` element leaves the accessibility tree and
//    takes the reason with it: a viewer got a dead grey form and no way to ask
//    why. Every one is now `lockedReason` + `onExplain` - dim, focusable,
//    `aria-disabled`, and a press states the reason.
//
// 3. `Number("")` CANNOT REACH THE RIG. The legacy panel guarded this with its
//    own `toNum` and a validate-then-toast pass; `NumberField` carries the same
//    guard once, for every field in the wave, and REJECTS a blank rather than
//    reading it as a real zero. It also clamps to the server's own bounds, so
//    the 422 the legacy validator existed to pre-empt cannot be reached.
//
// 4. THE BLOCK IS NOT INVENTED WHEN IT IS ABSENT. The legacy panel seeded its
//    draft with "50" and "30" and showed those numbers whether or not the rig
//    had sent a weather block - a screen that states a threshold the rig may
//    not hold. With no block this says so and offers nothing to edit.
//
// The write-only key contract is unchanged and must stay unchanged: the server
// blanks the value outbound (`redact.py`), `astrospheric_configured` is the
// only fact available about it, and the box is seeded from the DRAFT - never
// from the config payload. See `weatherDom.test.tsx`'s secret-hygiene test.

import { useId, useState, type JSX } from "react";
import { ApiError } from "../../../../api";
import { saveWeatherConfig } from "../../../../api/weather";
import { accessPhrase } from "../../../../lib/caps";
import { useConfig, useStore } from "../../../../store";
import { useLock } from "../../../lib/gateHook";
import {
  ActionButton, Card, EmptyCard, Field, Label, LockNote, Mono, NumberField, Switch, TextInput,
} from "../../../ui";
import {
  ASTRO_CLEAR_ARM, ASTRO_CLEAR_LABEL, ASTRO_EMPTY_REASON, ASTRO_LABEL, ASTRO_NOTE,
  ASTRO_PLACEHOLDER_SET, ASTRO_PLACEHOLDER_UNSET, ASTRO_SAVE_LABEL, ASTRO_SET_HINT,
  ASTRO_UNSET_HINT, keyStateLabel, policyLine, SUSTAIN_MAX, SUSTAIN_MIN, THRESHOLD_MAX,
  THRESHOLD_MIN, weatherLockSentence, WX_CONFLICT, WX_DEFAULT_SITE_WARN, WX_EMPTY_HINT,
  WX_EMPTY_TITLE, WX_ENABLED_NOTE, WX_INTRO, WX_KEY_CLEARED, WX_KEY_SAVED, WX_SAVE_FAILED,
  WX_TITLE,
} from "./weatherModel";

/** The three fields the server's `weather` block holds that this panel edits.
 *  `astrospheric_api_key` is not one of them - it rides its own write. */
interface WeatherEdit {
  enabled?: boolean;
  cloud_threshold_pct?: number;
  sustain_minutes?: number;
}

export function WeatherTuningPanel(): JSX.Element {
  const config = useConfig();
  const w = config?.weather ?? null;
  const lock = useLock({ cap: "config.site_optics" });
  const keyId = useId();

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  // The key box's own text. It starts empty, it is never seeded from the
  // config, and nothing outside this component can put a value in it.
  const [keyDraft, setKeyDraft] = useState("");
  // A refused write leaves `NumberField`'s own text showing the number the user
  // typed while the rig still holds the old one. Bumping this remounts the
  // fields, which re-seeds each from the config - so the box never shows a
  // value the rig did not take.
  const [nonce, setNonce] = useState(0);

  const write = async (
    edit: WeatherEdit,
    key: { value: string | null; clear: boolean } | null = null,
  ): Promise<void> => {
    if (busy || !w) return;
    setBusy(true);
    setErr(null);
    setSaid(null);
    try {
      // Wholesale replace: the server swaps the whole `weather` block, so a
      // partial body would drop the fields this edit does not name.
      await saveWeatherConfig(
        {
          enabled: edit.enabled ?? w.enabled,
          cloud_threshold_pct: edit.cloud_threshold_pct ?? w.cloud_threshold_pct,
          sustain_minutes: edit.sustain_minutes ?? w.sustain_minutes,
          // null means "keep the stored key" (api/weather.ts's contract). Every
          // write that is not the key's own write sends null, so changing the
          // threshold can never disturb the secret.
          astrospheric_api_key: key?.value ?? null,
        },
        config?.version ?? null,
        key?.clear ?? false,
      );
      await useStore.getState().loadConfig();
      if (key) {
        setKeyDraft("");
        setSaid(key.clear ? WX_KEY_CLEARED : WX_KEY_SAVED);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Somebody else wrote the config. Reload so the numbers on screen are
        // the rig's again, and say so rather than retrying over their edit.
        await useStore.getState().loadConfig();
        setErr(WX_CONFLICT);
      } else if (e instanceof ApiError && e.status === 403) {
        setErr(`Refused - ${accessPhrase("config.site_optics")} is needed here.`);
      } else {
        setErr(e instanceof Error ? (e.message || WX_SAVE_FAILED) : WX_SAVE_FAILED);
      }
      setNonce((n) => n + 1);
    } finally {
      setBusy(false);
    }
  };

  const busyReason = busy ? "a change is still being saved" : null;
  const gate = lock.lockedReason ?? busyReason;
  const explain = lock.onExplain;
  const noteReason = lock.lockedReason ? weatherLockSentence(lock.lockedReason) : null;

  if (!w) {
    return (
      <div className="nx-wxtune-stack" data-testid="wx-tuning">
        <Card>
          <div className="nx-wxtune-col">
            <Label size={11}>{WX_TITLE}</Label>
            <EmptyCard title={WX_EMPTY_TITLE} hint={WX_EMPTY_HINT} data-testid="wx-tuning-empty" />
          </div>
        </Card>
        <LockNote reason={noteReason} data-testid="wx-tuning-lock" />
      </div>
    );
  }

  const configured = !!w.astrospheric_configured;
  const keyTyped = keyDraft.trim() !== "";

  return (
    <div className="nx-wxtune-stack" data-testid="wx-tuning">
      <Card data-testid="wx-tuning-card">
        <div className="nx-wxtune-col">
          <div className="nx-wxtune-head">
            <Label size={11}>{WX_TITLE}</Label>
            <Mono size={10} tone="dim">{w.enabled ? "FETCHING" : "NOT FETCHING"}</Mono>
          </div>
          <p className="nx-wxtune-intro">{WX_INTRO}</p>

          <Switch
            checked={w.enabled}
            onChange={(v) => void write({ enabled: v })}
            label="Weather enabled"
            note={WX_ENABLED_NOTE}
            lockedReason={gate}
            onExplain={explain}
            data-testid="wx-weather-enabled"
          />

          {w.enabled && config?.site?.is_default && (
            <p className="nx-wxtune-warn" data-testid="wx-default-site">
              {WX_DEFAULT_SITE_WARN}
            </p>
          )}

          <div className="nx-wxtune-pair">
            <NumberField
              key={`threshold-${nonce}`}
              label="Cloud threshold (%)"
              value={w.cloud_threshold_pct}
              onCommit={(v) => void write({ cloud_threshold_pct: v })}
              unit="%"
              min={THRESHOLD_MIN}
              max={THRESHOLD_MAX}
              integer
              ariaLabel="Cloud threshold, percent of sky covered"
              lockedReason={gate}
              onExplain={explain}
              data-testid="wx-cloud-threshold"
            />
            <NumberField
              key={`sustain-${nonce}`}
              label="Sustained for (min)"
              value={w.sustain_minutes}
              onCommit={(v) => void write({ sustain_minutes: v })}
              unit="min"
              min={SUSTAIN_MIN}
              max={SUSTAIN_MAX}
              integer
              ariaLabel="Sustained for, minutes above the threshold"
              lockedReason={gate}
              onExplain={explain}
              data-testid="wx-sustain"
            />
          </div>
          <p className="nx-wxtune-note" data-testid="wx-policy-line">
            {policyLine(w.cloud_threshold_pct, w.sustain_minutes)}
          </p>
        </div>
      </Card>

      <Card data-testid="wx-astro-card">
        <div className="nx-wxtune-col">
          <Field
            label={`${ASTRO_LABEL} - ${keyStateLabel(configured)}`}
            htmlFor={keyId}
            hint={configured ? ASTRO_SET_HINT : ASTRO_UNSET_HINT}
          >
            <TextInput
              id={keyId}
              type="password"
              value={keyDraft}
              onChange={setKeyDraft}
              placeholder={configured ? ASTRO_PLACEHOLDER_SET : ASTRO_PLACEHOLDER_UNSET}
              ariaLabel="Astrospheric API key (write-only)"
              lockedReason={gate}
              data-testid="wx-astro-key"
            />
          </Field>
          <div className="nx-wxtune-actions">
            <ActionButton
              kind="secondary"
              onPress={() => void write({}, { value: keyDraft.trim(), clear: false })}
              lockedReason={gate ?? (keyTyped ? null : ASTRO_EMPTY_REASON)}
              onExplain={explain}
              busy={busy}
              data-testid="wx-astro-key-save"
            >
              {ASTRO_SAVE_LABEL}
            </ActionButton>
            {configured && (
              <ActionButton
                kind="danger"
                onPress={() => void write({}, { value: null, clear: true })}
                arm={{ label: ASTRO_CLEAR_ARM }}
                lockedReason={gate}
                onExplain={explain}
                data-testid="wx-astro-key-clear"
              >
                {ASTRO_CLEAR_LABEL}
              </ActionButton>
            )}
          </div>
          <p className="nx-wxtune-note">{ASTRO_NOTE}</p>
          {said && <Mono size={10.5} tone="good" data-testid="wx-tuning-said">{said}</Mono>}
        </div>
      </Card>

      {err && <Mono size={10.5} tone="bad" data-testid="wx-tuning-error">{err}</Mono>}
      <LockNote reason={noteReason} data-testid="wx-tuning-lock" />
    </div>
  );
}
