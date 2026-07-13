// SchedulePanel.tsx — per-target autorun schedule sub-panel (wave-3 §1,6). A
// collapsed disclosure row whose chip is scheduleSummary(); expanding reveals the
// structured editor (NO token mini-language — C1-24). Pure controlled: the parent
// (SequenceView.renderTarget) owns the Schedule and patches it via onChange, so
// per-target state stays in the plan SSOT. Per-card OPEN state lives HERE (local
// useState) so expanding one target's panel never expands another's. `disabled`
// (the run-in-progress flag) greys every control — same `disabled={running}`
// convention as the step editors; the plan BUILDER stays usable for viewers, only
// run controls gate on capability.

import { useEffect, useState } from "react";
import type { Schedule } from "../../types";
import { scheduleSummary } from "../../lib/schedule";
import { Segmented, type SegmentedOption } from "../Segmented";
import { InfoDot, Stepper, Toggle } from "../ui";

const START_OPTIONS: SegmentedOption<Schedule["start_mode"]>[] = [
  { value: "now", label: "Now" },
  { value: "dusk", label: "Dusk" },
  { value: "dawn", label: "Dawn" },
  { value: "time", label: "Time" },
];
const STOP_OPTIONS: SegmentedOption<Schedule["stop_mode"]>[] = [
  { value: "none", label: "None" },
  { value: "dawn", label: "Dawn" },
  { value: "time", label: "Time" },
];

// "HH:MM", 24-hour — 0-23 : 00-59, optional leading zero on the hour. The server
// (pydantic) is the real validator (422s a malformed time); this is only the
// gate for the local revert so a bad edit never persists a broken value.
const HHMM = /^([01]?\d|2[0-3]):([0-5]\d)$/;

/** Plain HH:MM text field: commits on blur (or Enter), and REVERTS to the last
 *  saved value on invalid input — it never clears a good value. */
function TimeField({ value, disabled, ariaLabel, onCommit }: {
  value: string | null; disabled: boolean; ariaLabel: string;
  onCommit: (v: string) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  // Re-seed if the saved value changes underneath us (undo, backfill, mode flip).
  useEffect(() => { setDraft(value ?? ""); }, [value]);
  const commit = () => {
    const t = draft.trim();
    if (HHMM.test(t)) onCommit(t);
    else setDraft(value ?? ""); // revert — keep the saved value intact
  };
  return (
    <input
      className="field !py-1 !w-20 mono text-center"
      placeholder="HH:MM"
      inputMode="numeric"
      value={draft}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
    />
  );
}

export default function SchedulePanel({ schedule, disabled, onChange }: {
  schedule: Schedule;
  disabled: boolean;
  onChange: (patch: Partial<Schedule>) => void;
}) {
  const [open, setOpen] = useState(false);
  const s = schedule;

  return (
    <div className="mt-2 border-t border-line/60 pt-2">
      {/* Collapsed disclosure row — chip is the scheduleSummary(); >=44px tap. */}
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Hide" : "Edit"} schedule — ${scheduleSummary(s)}`}
        onClick={() => setOpen((v) => !v)}
        className="w-full tap min-h-[44px] flex items-center gap-2 px-1 text-left
          text-[11px] text-dim hover:text-accent transition-colors cursor-pointer"
      >
        <span aria-hidden className="text-accent">⏱</span>
        <span className="mono truncate">{scheduleSummary(s)}</span>
        <span className="flex-1" />
        <span aria-hidden className="text-sm leading-none">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="mt-1 flex flex-col gap-3 px-1 pb-1 text-[11px]">
          {/* ------------------------------------------------------- start */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label w-16 shrink-0">start</span>
            <Segmented
              ariaLabel="Start mode"
              disabled={disabled}
              value={s.start_mode}
              onChange={(v) => onChange({ start_mode: v })}
              options={START_OPTIONS}
            />
            {(s.start_mode === "dusk" || s.start_mode === "dawn") && (
              <Stepper
                label="offset"
                value={s.start_offset_min}
                onChange={(v) => onChange({ start_offset_min: v })}
                min={-120} max={120} step={5} unit="min" disabled={disabled}
              />
            )}
            {s.start_mode === "time" && (
              <TimeField
                value={s.start_time}
                disabled={disabled}
                ariaLabel="Start time (HH:MM)"
                onCommit={(v) => onChange({ start_time: v })}
              />
            )}
          </div>

          {/* --------------------------------------------- min-altitude gate */}
          <div className="flex items-center gap-2 flex-wrap">
            <Stepper
              label="min alt"
              value={s.min_altitude_deg}
              onChange={(v) => onChange({ min_altitude_deg: v })}
              min={0} max={80} step={5} unit="°" disabled={disabled}
            />
            <InfoDot
              label="About the minimum-altitude start gate"
              content="Per-TARGET start gate: this target begins only once IT climbs above this altitude (0 = no gate). Separate from the global pier-collision safety floor, which watches the MOUNT altitude."
            />
          </div>

          {/* -------------------------------------------------------- stop */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label w-16 shrink-0">stop</span>
            <Segmented
              ariaLabel="Stop mode"
              disabled={disabled}
              value={s.stop_mode}
              onChange={(v) => onChange({ stop_mode: v })}
              options={STOP_OPTIONS}
            />
            {s.stop_mode === "dawn" && (
              <Stepper
                label="offset"
                value={s.stop_offset_min}
                onChange={(v) => onChange({ stop_offset_min: v })}
                min={-120} max={120} step={5} unit="min" disabled={disabled}
              />
            )}
            {s.stop_mode === "time" && (
              <TimeField
                value={s.stop_time}
                disabled={disabled}
                ariaLabel="Stop time (HH:MM)"
                onCommit={(v) => onChange({ stop_time: v })}
              />
            )}
          </div>

          {/* ---------------------------------------------------- max run */}
          <div className="flex items-center gap-2 flex-wrap">
            <Stepper
              label="max run"
              value={s.max_run_min}
              onChange={(v) => onChange({ max_run_min: v })}
              min={0} max={600} step={15} disabled={disabled}
              format={(v) => (v === 0 ? "no cap" : `${v} min`)}
            />
          </div>

          {/* --------------------------------------------------- on missed */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label inline-flex items-center gap-1 w-16 shrink-0">
              if missed
              <InfoDot
                label="About the missed-window behavior"
                content="wait — the engine holds and waits for this target's window to open. skip — if the window is already missed (or later closes) the engine skips this target for the night and moves on."
              />
            </span>
            <span className="inline-flex items-center gap-2">
              <span className={s.on_missed === "wait" ? "text-accent" : "text-dim"}>wait</span>
              <Toggle
                checked={s.on_missed === "skip"}
                disabled={disabled}
                onChange={(v) => onChange({ on_missed: v ? "skip" : "wait" })}
                label="Skip this target if its window is missed"
              />
              <span className={s.on_missed === "skip" ? "text-accent" : "text-dim"}>skip</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
