// PlanScheduleSheet.tsx - one target's autorun schedule, as its own layer.
//
// The rebuild of `components/sequence/SchedulePanel.tsx`. The legacy panel was
// an inline disclosure at the bottom of every target card, which in a 420 px
// sheet would put nine controls inside a card inside a list; the plan's own
// ruling is that this opens as a depth-1 sheet instead.
//
// PURE CONTROLLED, exactly as the legacy panel was: the plan draft is the SSOT,
// this takes the `Schedule` and hands back a patch. Per-target state lives in
// the plan, never here, so opening one target's schedule can never show
// another's values.
//
// EVERY `InfoDot` BECOMES A FIELD HINT. A popup that explains what a control
// does is a control you have to press to understand the control; the sentence
// is short enough to live under the row, and there it is readable at 3 a.m.
// without a second tap.
//
// THE TIME FIELD REVERTS, IT NEVER CLEARS. "HH:MM" is validated locally only so
// a malformed edit cannot replace a good value; the server (pydantic) is the
// real validator and 422s anything this lets through.

import { useEffect, useState, type JSX } from "react";

import { scheduleSummary } from "../../../../lib/schedule";
import type { Schedule } from "../../../../types";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import {
  Card, Field, Label, LockNote, Segmented, Sheet, Stepper2, TextInput,
} from "../../../ui";

/** 24-hour "HH:MM", optional leading zero on the hour. */
const HHMM = /^([01]?\d|2[0-3]):([0-5]\d)$/;

function TimeField({ label, hint, value, lockedReason, ariaLabel, onCommit, testid }: {
  label: string;
  hint: string;
  value: string | null;
  lockedReason: string | null;
  ariaLabel: string;
  onCommit: (v: string) => void;
  testid: string;
}): JSX.Element {
  const [draft, setDraft] = useState(value ?? "");
  // Re-seed when the saved value moves underneath (an undo, a mode flip).
  useEffect(() => { setDraft(value ?? ""); }, [value]);
  const commit = (raw: string) => {
    const t = raw.trim();
    if (HHMM.test(t)) onCommit(t);
    else setDraft(value ?? "");   // revert - never clear a good value
  };
  return (
    <Field label={label} hint={hint}>
      <TextInput
        value={draft}
        onChange={setDraft}
        onBlur={commit}
        onEnter={commit}
        placeholder="HH:MM"
        mono
        ariaLabel={ariaLabel}
        lockedReason={lockedReason}
        data-testid={testid}
      />
    </Field>
  );
}

export function PlanScheduleSheet({ name, schedule, lockedReason, onChange, onBack }: {
  /** The target this schedule belongs to. It is the sheet's title, so the user
   *  can never be editing one target's window while reading another's name. */
  name: string;
  schedule: Schedule;
  lockedReason: string | null;
  onChange: (patch: Partial<Schedule>) => void;
  onBack: () => void;
}): JSX.Element {
  const s = schedule;
  const lock = { lockedReason, onExplain: explainLock };

  return (
    <Sheet
      data-testid="session-plan-schedule"
      title={name}
      sub="AUTORUN WINDOW"
      live={scheduleSummary(s)}
      icon={<NxIcon name="clock" size={18} />}
      onBack={onBack}
      backLabel="PLAN"
    >
      <div className="nx-plan">
        <Card>
          <div className="nx-plan-stack">
            <Label size={11}>START</Label>
            <Segmented<Schedule["start_mode"]>
              label="Start mode"
              value={s.start_mode}
              onChange={(v) => onChange({ start_mode: v })}
              options={[
                { value: "now", label: "NOW" },
                { value: "dusk", label: "DUSK" },
                { value: "dawn", label: "DAWN" },
                { value: "time", label: "TIME" },
              ]}
              data-testid="plan-schedule-start-mode"
              {...lock}
            />
            {(s.start_mode === "dusk" || s.start_mode === "dawn") && (
              <Field label="OFFSET" hint={`Minutes either side of ${s.start_mode}.`}>
                <Stepper2
                  label="Start offset in minutes"
                  value={s.start_offset_min}
                  onChange={(v) => onChange({ start_offset_min: v })}
                  step={5} min={-120} max={120}
                  format={(v) => `${v > 0 ? "+" : ""}${v} min`}
                  data-testid="plan-schedule-start-offset"
                  {...lock}
                />
              </Field>
            )}
            {s.start_mode === "time" && (
              <TimeField
                label="START TIME"
                hint="Local clock time, 24-hour."
                value={s.start_time}
                ariaLabel="Start time, hours and minutes"
                lockedReason={lockedReason}
                onCommit={(v) => onChange({ start_time: v })}
                testid="plan-schedule-start-time"
              />
            )}
          </div>
        </Card>

        <Card>
          <div className="nx-plan-stack">
            <Label size={11}>GATES</Label>

            <Field
              label="MINIMUM ALTITUDE"
              hint={"This target begins only once IT climbs above this altitude (0 = no gate). "
                + "Separate from the pier-collision safety floor, which watches the MOUNT."}
            >
              <Stepper2
                label="Minimum altitude in degrees"
                value={s.min_altitude_deg}
                onChange={(v) => onChange({ min_altitude_deg: v })}
                step={5} min={0} max={80}
                format={(v) => (v === 0 ? "no gate" : `${v} deg`)}
                data-testid="plan-schedule-min-alt"
                {...lock}
              />
            </Field>

            <Field
              label="HOUR-ANGLE WINDOW"
              hint={"Image only within this many hours either side of the meridian (0 = no "
                + "limit). Before the target rises into the window it waits; past the west "
                + "limit it is skipped for the night."}
            >
              <Stepper2
                label="Hour-angle window in hours"
                value={s.max_hour_angle_h}
                onChange={(v) => onChange({ max_hour_angle_h: v })}
                step={0.5} min={0} max={12}
                format={(v) => (v === 0 ? "no limit" : `${v} h either side`)}
                data-testid="plan-schedule-max-ha"
                {...lock}
              />
            </Field>

            <Field
              label="MOON SEPARATION"
              hint={"This target waits while it is closer than this to the Moon AND the Moon "
                + "is above the horizon (0 = off). A Moon below the horizon imposes no gate."}
            >
              <Stepper2
                label="Minimum moon separation in degrees"
                value={s.min_moon_sep_deg}
                onChange={(v) => onChange({ min_moon_sep_deg: v })}
                step={5} min={0} max={180}
                format={(v) => (v === 0 ? "off" : `${v} deg or more`)}
                data-testid="plan-schedule-moon-sep"
                {...lock}
              />
            </Field>

            <Field
              label="MOON BRIGHTNESS"
              hint={"This target waits while the Moon is up AND more than this percent lit "
                + "(0 = off). It resumes once the Moon sets, or after this bright Moon's night."}
            >
              <Stepper2
                label="Maximum moon illumination percent"
                value={s.max_moon_illum_pct}
                onChange={(v) => onChange({ max_moon_illum_pct: v })}
                step={5} min={0} max={100}
                format={(v) => (v === 0 ? "off" : `${v}% or less`)}
                data-testid="plan-schedule-moon-illum"
                {...lock}
              />
            </Field>
          </div>
        </Card>

        <Card>
          <div className="nx-plan-stack">
            <Label size={11}>STOP</Label>
            <Segmented<Schedule["stop_mode"]>
              label="Stop mode"
              value={s.stop_mode}
              onChange={(v) => onChange({ stop_mode: v })}
              options={[
                { value: "none", label: "NONE" },
                { value: "dawn", label: "DAWN" },
                { value: "time", label: "TIME" },
              ]}
              data-testid="plan-schedule-stop-mode"
              {...lock}
            />
            {s.stop_mode === "dawn" && (
              <Field label="OFFSET" hint="Minutes either side of dawn.">
                <Stepper2
                  label="Stop offset in minutes"
                  value={s.stop_offset_min}
                  onChange={(v) => onChange({ stop_offset_min: v })}
                  step={5} min={-120} max={120}
                  format={(v) => `${v > 0 ? "+" : ""}${v} min`}
                  data-testid="plan-schedule-stop-offset"
                  {...lock}
                />
              </Field>
            )}
            {s.stop_mode === "time" && (
              <TimeField
                label="STOP TIME"
                hint="Local clock time, 24-hour."
                value={s.stop_time}
                ariaLabel="Stop time, hours and minutes"
                lockedReason={lockedReason}
                onCommit={(v) => onChange({ stop_time: v })}
                testid="plan-schedule-stop-time"
              />
            )}

            <Field label="MAXIMUM RUN" hint="How long this target may hold the rig before the run moves on.">
              <Stepper2
                label="Maximum run in minutes"
                value={s.max_run_min}
                onChange={(v) => onChange({ max_run_min: v })}
                step={15} min={0} max={600}
                format={(v) => (v === 0 ? "no cap" : `${v} min`)}
                data-testid="plan-schedule-max-run"
                {...lock}
              />
            </Field>

            <Field
              label="IF THE WINDOW IS MISSED"
              hint={"WAIT runs this target whenever its window is open, even if the start "
                + "passed while another target was shooting. SKIP drops it for the night once "
                + "its start is more than five minutes past. Either way a window that has "
                + "already CLOSED is skipped: a closed window cannot be waited for, and a NOW "
                + "start has nothing to miss."}
            >
              <Segmented<Schedule["on_missed"]>
                label="If the window is missed"
                value={s.on_missed}
                onChange={(v) => onChange({ on_missed: v })}
                options={[
                  { value: "wait", label: "WAIT" },
                  { value: "skip", label: "SKIP" },
                ]}
                data-testid="plan-schedule-on-missed"
                {...lock}
              />
            </Field>
          </div>
        </Card>

        <LockNote reason={lockedReason} data-testid="plan-schedule-lock" />
      </div>
    </Sheet>
  );
}
