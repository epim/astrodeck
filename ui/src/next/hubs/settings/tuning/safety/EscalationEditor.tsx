// EscalationEditor.tsx - the rebuilt run-recovery policy (wave R7, T-R7-9;
// plan section 3.F2, cutover table section 7).
//
// It replaces `components/settings/EscalationPanel.tsx` at BOTH of that panel's
// mount points inside the new UI - `settings/sheets/SafetyTuningSheet.tsx` and
// `hubs/rig/sheets/safety.tsx` - which is why it lives in its own module rather
// than inside either sheet. The legacy file is untouched; `#/classic` still
// renders it.
//
// THREE THINGS CHANGED, AND WHY.
//
// 1. NO SAVE BUTTON. The legacy panel held a draft of the whole block and wrote
//    it on a Save press, so a switch a user had flipped LOOKED armed while the
//    rig still had the old policy - the same failure the rig safety sheet's
//    header calls out and refuses. Every control here owns exactly one field,
//    so every control writes immediately and echoes the whole block (the server
//    replaces `escalation` wholesale). `NumberField` holds its own text and
//    commits only at blur or Enter, so typing 1 then 5 does not fire two writes
//    on the way to 15.
//
// 2. NO NATIVE `disabled`. The legacy panel disabled the action pickers while
//    their parent switch was off, and both number inputs with them: controls a
//    user could want to press, greyed out with no reason attached. Each is now
//    `lockedReason` + `onExplain` - it stays readable, stays focusable, and a
//    press says which switch turns it back on. It also stops the two numeric
//    fields VANISHING with their parent switch, which is how a retake limit
//    somebody set last month becomes invisible rather than inapplicable.
//
// 3. NO `sm:` GRIDS. The legacy rows are Tailwind grids keyed to the VIEWPORT,
//    not the container, so at 820 px they laid out a fixed 13 rem column inside
//    a 360 px sheet and pushed 100 px of horizontal scroll onto the page
//    (`rig/sheets/safety.tsx`'s deleted `safety-escalation-scroll` box existed
//    only to contain that). Every group here is a single column that cannot
//    overflow, so the workaround went with it.
//
// The one thing that did NOT change is the copy: see `escalationModel.ts`.

import { useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { setEscalationConfig } from "../../../../../api/backends";
import { accessPhrase } from "../../../../../lib/caps";
import { useConfig, useStore } from "../../../../../store";
import type { EscalationConfig } from "../../../../../types";
import { useLock } from "../../../../lib/gateHook";
import { isLocalOnly, LOCAL_ONLY_REASON } from "../../../../lib/gate";
import {
  Card, Divider, EmptyCard, Label, LockNote, Mono, NumberField, Segmented, Switch,
} from "../../../../ui";
import {
  ACTION_BLURB, ACTION_OPTIONS, AF_BLURB, AF_TITLE, COOLING_ACTION_TITLE, COOLING_BLURB,
  COOLING_OFF_REASON, COOLING_TITLE, dependsOn, ESC_EMPTY_HINT, ESC_EMPTY_TITLE, ESC_EYEBROW,
  ESC_INTRO, ESC_TITLE, escalationLockSentence, GROUP_BEFORE, GROUP_DURING, GROUP_RECOVERY,
  GUIDING_ACTION_TITLE, GUIDING_BLURB, GUIDING_OFF_REASON, GUIDING_TITLE, HFR_BLURB,
  HFR_BLURB_ROW, HFR_OPTIONS, HFR_TITLE, RECONNECT_BLURB, RECONNECT_TITLE, RETAKE_HINT,
  RETAKE_OFF_REASON, RETAKE_TITLE, RETRIES_HINT, RETRIES_OFF_REASON, RETRIES_TITLE,
  SAVE_FAILED, WATCHDOG_HINT, WATCHDOG_TITLE, watchdogLine, type Action, type HfrAction,
} from "./escalationModel";
// The area's ONE css import. It sits on this component rather than on the
// nominal area root because this is the component BOTH mount points render: the
// hub bundles split per hub, so a Rig hub loaded without ever opening Settings
// would otherwise render this editor unstyled.
import "./safety.css";

export function EscalationEditor({ showLockNote = true }: {
  /** The Safety tuning sheet renders ONE lock note for both of its capabilities
   *  (`config.safety` for the preset, `config.alerts` for this editor), so it
   *  turns this one off rather than print the same sentence twice. At the rig
   *  sheet's mount there is nothing above it, so it prints its own. */
  showLockNote?: boolean;
} = {}): JSX.Element {
  const config = useConfig();
  const esc = config?.escalation ?? null;
  // `needsLan`: every row writes `POST /api/config {escalation}`, on the fence.
  const lock = useLock({ cap: "config.alerts", needsLan: true });

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // A refused write leaves `NumberField`'s own text showing the number the user
  // typed while the rig still holds the old one. Bumping this remounts the
  // fields, which re-seeds each from the config - so the box never shows a
  // value the rig did not take.
  const [nonce, setNonce] = useState(0);

  const write = async (patch: Partial<EscalationConfig>): Promise<void> => {
    if (busy || !esc) return;
    setBusy(true);
    setErr(null);
    setSaved(false);
    try {
      // Wholesale replace: the server swaps the whole `escalation` block, so a
      // partial body would drop every field this editor does not show
      // (hfr_reject_factor, require_safety_monitor - both owned elsewhere).
      await setEscalationConfig({ ...esc, ...patch });
      await useStore.getState().loadConfig();
      setSaved(true);
    } catch (e) {
      // `isLocalOnly` FIRST: the fence answers 403 for every role.
      setErr(isLocalOnly(e) ? LOCAL_ONLY_REASON
        : e instanceof ApiError
          ? (e.status === 403
            ? `Refused - ${accessPhrase("config.alerts")} is needed here.`
            : e.message || SAVE_FAILED)
          : SAVE_FAILED);
      setNonce((n) => n + 1);
    } finally {
      setBusy(false);
    }
  };

  const busyReason = busy ? "a change is still being saved" : null;
  const base = lock.lockedReason ?? busyReason;
  const explain = lock.onExplain;
  const gate = (extra: string | null): string | null => base ?? extra;
  const noteReason = lock.lockedReason
    ? escalationLockSentence(lock.lockedReason, accessPhrase("config.alerts"))
    : null;

  if (!esc) {
    return (
      <Card data-testid="escalation-editor">
        <div className="nx-safetune-col">
          <Label size={11}>{ESC_TITLE}</Label>
          <EmptyCard title={ESC_EMPTY_TITLE} hint={ESC_EMPTY_HINT} data-testid="escalation-empty" />
        </div>
      </Card>
    );
  }

  return (
    <Card data-testid="escalation-editor">
      <div className="nx-safetune-col">
        <div className="nx-esc-head">
          <Label size={11}>{ESC_TITLE}</Label>
          <Mono size={10} tone="dim">{ESC_EYEBROW}</Mono>
        </div>
        <p className="nx-esc-intro">{ESC_INTRO}</p>

        {/* -------------------------------------------------------- preflight */}
        <Label>{GROUP_BEFORE}</Label>

        <Switch
          checked={esc.require_cooling}
          onChange={(v) => void write({ require_cooling: v })}
          label={COOLING_TITLE}
          note={COOLING_BLURB}
          lockedReason={base}
          onExplain={explain}
          data-testid="escalation-row-cooling"
        />
        <div className="nx-esc-group">
          <Label>{COOLING_ACTION_TITLE}</Label>
          <Segmented
            options={ACTION_OPTIONS}
            value={esc.cooling_action}
            onChange={(v: Action) => void write({ cooling_action: v })}
            label={COOLING_ACTION_TITLE}
            lockedReason={gate(dependsOn(esc.require_cooling, COOLING_OFF_REASON))}
            onExplain={explain}
            data-testid="escalation-row-cooling-action"
          />
          <Mono size={10.5} tone="dim">{ACTION_BLURB[esc.cooling_action]}</Mono>
        </div>

        <Switch
          checked={esc.require_guiding}
          onChange={(v) => void write({ require_guiding: v })}
          label={GUIDING_TITLE}
          note={GUIDING_BLURB}
          lockedReason={base}
          onExplain={explain}
          data-testid="escalation-row-guiding"
        />
        <div className="nx-esc-group">
          <Label>{GUIDING_ACTION_TITLE}</Label>
          <Segmented
            options={ACTION_OPTIONS}
            value={esc.guiding_action}
            onChange={(v: Action) => void write({ guiding_action: v })}
            label={GUIDING_ACTION_TITLE}
            lockedReason={gate(dependsOn(esc.require_guiding, GUIDING_OFF_REASON))}
            onExplain={explain}
            data-testid="escalation-row-guiding-action"
          />
          <Mono size={10.5} tone="dim">{ACTION_BLURB[esc.guiding_action]}</Mono>
        </div>

        <Divider />

        {/* ------------------------------------------------------- during run */}
        <Label>{GROUP_DURING}</Label>

        <div className="nx-esc-group">
          <Label>{AF_TITLE}</Label>
          <p className="nx-esc-blurb">{AF_BLURB}</p>
          <Segmented
            options={ACTION_OPTIONS}
            value={esc.af_failure_action}
            onChange={(v: Action) => void write({ af_failure_action: v })}
            label={AF_TITLE}
            lockedReason={base}
            onExplain={explain}
            data-testid="escalation-row-af"
          />
          <Mono size={10.5} tone="dim">{ACTION_BLURB[esc.af_failure_action]}</Mono>
        </div>

        <div className="nx-esc-group">
          <Label>{HFR_TITLE}</Label>
          <p className="nx-esc-blurb">{HFR_BLURB_ROW}</p>
          <Segmented
            options={HFR_OPTIONS}
            value={esc.hfr_reject_action}
            onChange={(v: HfrAction) => void write({ hfr_reject_action: v })}
            label={HFR_TITLE}
            lockedReason={base}
            onExplain={explain}
            data-testid="escalation-row-hfr"
          />
          <Mono size={10.5} tone="dim">{HFR_BLURB[esc.hfr_reject_action]}</Mono>
        </div>

        <NumberField
          key={`retake-${nonce}`}
          label={RETAKE_TITLE}
          ariaLabel="Retake limit per target, frames"
          value={esc.hfr_retake_limit_per_target}
          onCommit={(v) => void write({ hfr_retake_limit_per_target: v })}
          min={1}
          max={100}
          integer
          hint={RETAKE_HINT}
          lockedReason={gate(dependsOn(esc.hfr_reject_action === "retake", RETAKE_OFF_REASON))}
          onExplain={explain}
          data-testid="escalation-row-retake-limit"
        />

        <div className="nx-esc-group">
          <NumberField
            key={`watchdog-${nonce}`}
            label={WATCHDOG_TITLE}
            ariaLabel="No-progress watchdog, minutes"
            unit="min"
            value={Math.round((esc.no_progress_watchdog_s ?? 0) / 60)}
            onCommit={(v) => void write({ no_progress_watchdog_s: v * 60 })}
            min={0}
            max={600}
            step={5}
            integer
            zeroMeans="off"
            hint={WATCHDOG_HINT}
            lockedReason={base}
            onExplain={explain}
            data-testid="escalation-row-watchdog"
          />
          <Mono size={10.5} tone={esc.no_progress_watchdog_s > 0 ? "dim" : "warn"}>
            {watchdogLine(esc.no_progress_watchdog_s)}
          </Mono>
        </div>

        <Divider />

        {/* -------------------------------------------------------- reconnect */}
        <Label>{GROUP_RECOVERY}</Label>

        <Switch
          checked={esc.reconnect_resume}
          onChange={(v) => void write({ reconnect_resume: v })}
          label={RECONNECT_TITLE}
          note={RECONNECT_BLURB}
          lockedReason={base}
          onExplain={explain}
          data-testid="escalation-row-reconnect"
        />

        <NumberField
          key={`retries-${nonce}`}
          label={RETRIES_TITLE}
          ariaLabel="Reconnect attempts before giving up"
          value={esc.reconnect_retries}
          onCommit={(v) => void write({ reconnect_retries: v })}
          min={1}
          max={20}
          integer
          hint={RETRIES_HINT}
          lockedReason={gate(dependsOn(esc.reconnect_resume, RETRIES_OFF_REASON))}
          onExplain={explain}
          data-testid="escalation-row-reconnect-retries"
        />

        <div data-testid="escalation-status">
          {err && <Mono size={10.5} tone="bad">{err}</Mono>}
          {!err && saved && <Mono size={10.5} tone="good">Saved - the rig has this policy now</Mono>}
        </div>

        {showLockNote && <LockNote reason={noteReason} data-testid="escalation-lock" />}
      </div>
    </Card>
  );
}
