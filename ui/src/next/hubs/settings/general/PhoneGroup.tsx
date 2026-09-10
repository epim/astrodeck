// PhoneGroup.tsx - Settings > GENERAL > PHONE (plan section C.2.3).
//
// Every row here is an EXISTING preference that had to survive the migration,
// and every one of them keeps its EXISTING localStorage key so a phone that has
// been set up once does not lose its settings (plan section 2.2). The store is
// the only writer of those keys - this file never touches `localStorage`
// itself, and never sets `--screen-brightness` / `--scrim-opacity`, which
// `store.setBrightness` owns (`store.ts:1657-1667`).
//
// Two rows are new UI for old state:
//   - AUTO-LOCK. `astrodeck-autolock` is written by `setTouch({autoLockMs})`,
//     read by `TouchGuard.tsx`, typed at `types.ts:2103` - and no component in
//     `ui/src` wrote it. A persisted setting the app honours and no screen can
//     change is the exact broken-promise shape this codebase has a taxonomy
//     for; this row closes it (plan F.3).
//   - DOWNLOADS. `astrodeck-next-dl-pref` is new (the Session hub's Files sheet
//     reads it), so it is the one key here written locally.
//
// HAPTICS is ABSENT ENTIRELY when `haptics.supported` is false - iOS and iPad
// Safari have no `navigator.vibrate`, and a toggle that cannot do anything on
// the field-dominant device is worse than no toggle (`lib/haptics.ts`, R26).

import { useState, type JSX, type ReactNode } from "react";
import { ListRow, Mono, Segmented, Stepper2, Switch } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import {
  useStore, useNight, useBrightness, useAutoMonitor,
} from "../../../../store";
import {
  useTouchSettings, useSetTouch, useMonitorAwake, useSetMonitorAwake,
} from "../../../../lib/touchStore";
import { haptics } from "../../../../lib/haptics";
import { BellGlyph } from "./glyphs";
import { Group } from "./Group";

/** New key (plan section C.2.3). Which file the Files sheet hands you when you
 *  press DOWNLOAD: the science frame or a picture to share. */
export const DL_PREF_KEY = "astrodeck-next-dl-pref";
export type DlPref = "fits" | "jpeg";

export function readDlPref(): DlPref {
  try {
    return localStorage.getItem(DL_PREF_KEY) === "jpeg" ? "jpeg" : "fits";
  } catch {
    return "fits";
  }
}
function writeDlPref(v: DlPref): void {
  try { localStorage.setItem(DL_PREF_KEY, v); } catch { /* private mode */ }
}

/** `NavMoreSheet.tsx:74-78`, verbatim - the labels and the stored ids both. The
 *  ids are what `astrodeck-touch-size` holds, so renaming a label is free and
 *  renaming an id silently resets every phone. */
const SIZING = [
  { value: "auto" as const, label: "Auto" },
  { value: "on" as const, label: "Large" },
  { value: "off" as const, label: "Off" },
];

const AUTOLOCK = [
  { value: 0, label: "Off" },
  { value: 180000, label: "3 min" },
  { value: 300000, label: "5 min" },
];

const DOWNLOADS = [
  { value: "fits" as const, label: "FITS" },
  { value: "jpeg" as const, label: "JPEG" },
];

const STACK_CONTROL = { padding: "0 14px 12px" } as const;

/** A preference whose control is too wide to ride in the row's right slot.
 *
 *  A three-option radiogroup is ~170 px and a stepper is ~150 px; on a 390 px
 *  phone that leaves about 110 px for the title, so the sub-line - which is
 *  `text-overflow: ellipsis` - loses most of the sentence that says what the
 *  setting DOES. The wide controls therefore sit on their own line under the
 *  row, which costs a few pixels of height and keeps the explanation readable.
 *  The switches (40 px) stay in the right slot where the design puts them. */
function StackedPref({ icon, title, sub, control, testId }: {
  icon: ReactNode;
  title: string;
  sub: string;
  control: ReactNode;
  testId: string;
}): JSX.Element {
  return (
    <div data-testid={testId}>
      <ListRow icon={icon} title={title} sub={sub} />
      <div style={STACK_CONTROL}>{control}</div>
    </div>
  );
}

export function PhoneGroup(): JSX.Element {
  const night = useNight();
  const toggleNight = useStore((s) => s.toggleNight);
  const brightness = useBrightness();
  const setBrightness = useStore((s) => s.setBrightness);
  const resetBrightness = useStore((s) => s.resetBrightness);
  const autoMonitor = useAutoMonitor();
  const setAutoMonitor = useStore((s) => s.setAutoMonitor);
  const touch = useTouchSettings();
  const setTouch = useSetTouch();
  const monitorAwake = useMonitorAwake();
  const setMonitorAwake = useSetMonitorAwake();

  const [dlPref, setDlPref] = useState<DlPref>(readDlPref);

  return (
    <Group label="PHONE" testId="group-phone">
      <ListRow
        icon={<NxIcon name="moon" />}
        title="NIGHT MODE"
        sub="every colour token swaps to deep red, so the screen stops costing you dark adaptation"
        right={
          <Switch
            checked={night}
            onChange={() => toggleNight()}
            label="Night mode"
            hideLabel
            data-testid="switch-night"
          />
        }
      />

      <StackedPref
        icon={<NxIcon name="sun" />}
        title="SCREEN BRIGHTNESS"
        sub="dims the whole app without touching the system slider · Shift+B resets"
        testId="row-brightness"
        control={
          <Stepper2
            value={Math.round(brightness * 100)}
            onChange={(pct) => setBrightness(pct / 100)}
            step={5}
            min={50}
            max={100}
            format={(v) => `${v}%`}
            label="Screen brightness"
            data-testid="stepper-brightness"
          />
        }
      />
      {brightness < 0.999 && (
        <ListRow
          icon={<NxIcon name="refresh" />}
          title="RESET BRIGHTNESS"
          sub={`back to 100% for ${night ? "night" : "day"} mode`}
          right={<Mono>RESET</Mono>}
          onPress={() => resetBrightness()}
          data-testid="row-reset-brightness"
        />
      )}

      <StackedPref
        icon={<NxIcon name="download" />}
        title="DOWNLOADS"
        testId="row-downloads"
        sub={
          dlPref === "fits"
            ? "FITS: the science file, every bit the sensor recorded"
            : "JPEG: a picture to share, a fraction of the size"
        }
        control={
          <Segmented
            options={DOWNLOADS}
            value={dlPref}
            onChange={(v) => { setDlPref(v); writeDlPref(v); }}
            label="Download format"
            data-testid="seg-downloads"
          />
        }
      />

      <ListRow
        icon={<BellGlyph />}
        title="NOTIFICATIONS"
        sub="where holds, safety trips and finished targets are announced"
        right={<Mono>EDIT</Mono>}
        chevron
        onPress={() => nav.go("/monitor/alerts")}
        data-testid="row-notifications"
      />

      <StackedPref
        icon={<NxIcon name="gauge" />}
        title="TOUCH SIZE"
        testId="row-touch-size"
        sub="Large grows every control past the 44 px floor · Off keeps the desktop sizes"
        control={
          <Segmented
            options={SIZING}
            value={touch.touchSizing}
            onChange={(v) => setTouch({ touchSizing: v })}
            label="Touch target size"
            data-testid="seg-touch-size"
          />
        }
      />

      {haptics.supported && (
        <ListRow
          icon={<NxIcon name="wind" />}
          title="HAPTICS"
          sub="a short buzz on start, stop and refusal · the number on screen is still the answer"
          right={
            <Switch
              checked={touch.hapticsEnabled}
              onChange={(v) => setTouch({ hapticsEnabled: v })}
              label="Haptic feedback"
              hideLabel
              data-testid="switch-haptics"
            />
          }
        />
      )}

      <ListRow
        icon={<NxIcon name="mount" />}
        title="REVERSE RA"
        sub="for a diagonal or a mirrored view: the pad's E and W swap"
        right={
          <Switch
            checked={touch.reverseRa}
            onChange={(v) => setTouch({ reverseRa: v })}
            label="Reverse RA slew direction"
            hideLabel
            data-testid="switch-rev-ra"
          />
        }
      />
      <ListRow
        icon={<NxIcon name="mount" />}
        title="REVERSE DEC"
        sub="for a diagonal or a mirrored view: the pad's N and S swap"
        right={
          <Switch
            checked={touch.reverseDec}
            onChange={(v) => setTouch({ reverseDec: v })}
            label="Reverse Dec slew direction"
            hideLabel
            data-testid="switch-rev-dec"
          />
        }
      />

      <StackedPref
        icon={<NxIcon name="lock" />}
        title="AUTO-LOCK"
        testId="row-autolock"
        sub="locks the screen after this long with no touch · a 5 s countdown warns first"
        control={
          <Segmented
            options={AUTOLOCK}
            value={touch.autoLockMs ?? 0}
            onChange={(v) =>
              setTouch({ autoLockMs: v === 0 ? null : (v as 180000 | 300000) })}
            label="Auto-lock after"
            data-testid="seg-autolock"
          />
        }
      />

      <ListRow
        icon={<NxIcon name="eye" />}
        title="KEEP AWAKE"
        sub="holds the screen on while you are watching, even with no run going"
        right={
          <Switch
            checked={monitorAwake}
            onChange={setMonitorAwake}
            label="Keep the screen awake as a monitor"
            hideLabel
            data-testid="switch-awake"
          />
        }
      />

      <ListRow
        icon={<NxIcon name="monitor" />}
        title="JUMP TO MONITOR WHEN A RUN STARTS"
        sub="opens Monitor by itself the moment a sequence begins"
        right={
          <Switch
            checked={autoMonitor}
            onChange={setAutoMonitor}
            label="Jump to Monitor when a run starts"
            hideLabel
            data-testid="switch-auto-monitor"
          />
        }
      />
    </Group>
  );
}
