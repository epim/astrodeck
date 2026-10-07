// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// RotatorPanel.tsx - the ROTATOR sheet's body, rebuilt in the design's own
// vocabulary (wave R7, T-R7-8; replaces the mounted
// `components/equipment/RotatorCard.tsx`, which is NOT edited and keeps serving
// `#/classic`).
//
// This is the area ROOT: it is the one file that imports `rotator.css`, per the
// wave rule that `next.css` belongs to a single task and every other area
// carries its own stylesheet.
//
// WHAT CHANGED, AND WHY IT IS NOT A RE-SKIN.
//
//   ELEVEN NATIVE `disabled` ATTRIBUTES ARE GONE. The legacy card greyed out
//   every button and both range inputs (`RotatorCard.tsx` :165, :167, :169,
//   :173, :185, :199, :206, :225, :250, :259, :272). A `disabled` element is
//   removed from the accessibility tree, which takes the REASON with it: a
//   viewer saw a dead grey row and no way to ask why. Every one of them is now
//   `lockedReason` - dim, focusable, `aria-disabled`, and a press says the
//   reason instead of acting.
//
//   THE SPLIT GATE IS LOAD-BEARING. Motion needs `control.capture`; the
//   range-of-motion CONFIG needs `config.backend`. The server enforces them at
//   two different dependencies (`api/app.py:6125-6199` for the four motion
//   routes, the `config.backend` gate on `POST /api/config/rotator`), so the
//   sheet carries TWO lock notes and never merges them. An operator without
//   `config.backend` keeps a completely usable rotator; that is the ordinary
//   case, not an edge one.
//
//   HALT IGNORES BUSY, AND ONLY BUSY. Every other motion control locks while a
//   rotator command is in flight or the rig reports the lane busy. HALT is the
//   way OUT of that state, so it carries the capability gate and nothing else
//   (hub-rig.md 0.5's never-blocked list; `_spawn` cancels both lanes in
//   `app.py:6152-6158`). It is single-tap: an arm/confirm on a stop button is a
//   second tap between a person and a cable wrap.
//
//   THE ARC STAYS DISPLAY-ONLY. See `RotatorArc.tsx`.
//
// The angle maths is shared, never re-derived: `adjustedPa` / `mod360` from
// `lib/rotation.ts` (the sky/mechanical offset is NOT on the wire - it is
// derived from the two live values) and `allowedSweepDeg` / `arcPath` /
// `polarXY` from `lib/rotatorDial.ts`.
//
// `lib/radiogroup.ts` is NOT used here. The legacy card hand-rolled a
// `role="radiogroup"` and wired `rovingTabIndex` / `handleRadioKeyDown` into
// it; the `Segmented` primitive already is that radiogroup, with one tab stop
// and arrow keys that move the selection and follow it with focus
// (`ui/Segmented.tsx:40-54`). The helper stays in `lib/` for the legacy card.

import { useId, useMemo, useRef, useState, type JSX } from "react";
import { api, ApiError } from "../../../../api";
import { setRotatorConfig } from "../../../../api/backends";
import { useConfig, useStore } from "../../../../store";
import type { RotatorConfig, RotatorStatus } from "../../../../types";
import { adjustedPa, mod360 } from "../../../../lib/rotation";
import { allowedSweepDeg } from "../../../../lib/rotatorDial";
import { useLock } from "../../../lib/gateHook";
import {
  ActionButton, Card, Dial, Field, Label, LockNote, Mono, NumberField,
  ReadoutGrid, ReadoutTile, Segmented, Switch, TextInput,
} from "../../../ui";
import { RotatorArc } from "./RotatorArc";
import {
  CONFIG_LOCK_NOTE, DEFAULT_ROTATOR_CFG, FULL_RANGE_NOTE, IN_FLIGHT_NOTE,
  MOTION_LOCK_NOTE, NO_TARGET_NOTE, RANGE_LABEL, RANGE_OPTIONS, lockNote,
  outOfRangeLine, paStops, toleranceStops, toNum,
} from "./rotatorModel";
import "./rotator.css";

/** What SYNC TO SKY does, said where it is pressed. It shipped in 0.2.65 with
 *  no caller anywhere in the UI, so for two releases the only way to establish
 *  the sky/mechanical offset from the app was to command a rotation nobody
 *  wanted - which is the complaint the route was added to answer. */
const SYNC_NOTE =
  "SYNC TO SKY plate-solves and tells the rotator its sky angle. It does not "
  + "turn the camera; ROTATE TO PA does, and then solves again to check.";

/** What TEST ROTATOR does, said where it is pressed (#145, #594). It turns the
 *  rotator and exposes, so the cost is in the sentence: about 22 degrees of
 *  travel (a 2 degree step to learn the sign, a 20 degree step to check the
 *  camera follows) and four plate solves, two for each. It measures only what
 *  is unmeasured, EXCEPT that a follow test that FAILED is run again on its
 *  own (#697): that is the press that follows re-seating the coupling, and it
 *  is cheaper than the first (two solves, the 20 degree step, the sign kept),
 *  so the sentence says so. */
const PREFLIGHT_NOTE =
  "TEST ROTATOR turns the rotator about 22 degrees and takes four plate solves: "
  + "two to learn which way the sky angle runs against the mechanical angle, two "
  + "to check the camera follows a 20 degree step. It measures only what this "
  + "connection has not measured yet. After a FAILED follow test it runs that "
  + "test again on its own (two solves and the 20 degree step, the sign kept), "
  + "so press it once the coupling has been re-seated.";

/** Why TEST ROTATOR has nothing to do: the sign is measured and the follow test
 *  PASSED tonight. A FAILED test does not lock it (#697): that is the one state
 *  in which a press has something to measure. The follow test has an observing
 *  night (#709), so the lock lifts by itself when the night turns over. */
const PREFLIGHT_DONE_NOTE =
  "this connection has already measured the rotator tonight; it measures again "
  + "on the next observing night or after the rig reconnects.";

/** The one line of what the rig knows about this rotator: whether the sky
 *  angle's sign has been measured, and whether the camera was seen to follow a
 *  step. `null` and `undefined` (an older server) both read as "not measured",
 *  and neither is "failed": a rotator nobody has tested is not a rotator that
 *  failed. The legacy `RotatorCard.tsx` carries its own copy of this function
 *  (importing across the legacy/next line drags one bundle into the other),
 *  and both tests pin the same sentences. */
export function rotatorPreflightLine(
  rot: Pick<RotatorStatus, "sky_sign" | "trusted">,
): { text: string; failed: boolean } {
  if (rot.trusted === false) {
    return {
      text: "FAILED: the camera did not follow a 20 degree step, so rotation "
        + "is off. Press TEST ROTATOR to measure it again",
      failed: true,
    };
  }
  const sign = rot.sky_sign === 1 || rot.sky_sign === -1
    ? `sign ${rot.sky_sign === 1 ? "+1" : "-1"} measured`
    : "sign not measured";
  const follow = rot.trusted === true
    ? "follow test passed: the camera turned with a 20 degree step"
    : "follow test not run";
  return { text: `${sign}, ${follow}`, failed: false };
}

/** Which readout tile the one dial is pointed at (hub-rig.md 0.3). Local
 *  React state, never persisted - it is a view, not rig data. */
type DialTile = "pa" | "tolerance";

/** A stable key for the config block, so the ROM draft can be re-seeded during
 *  render when the server's answer lands instead of one frame later in an
 *  effect (which paints the stale number first). */
const cfgKey = (c: RotatorConfig): string =>
  `${c.range_type}|${c.range_start_deg}|${c.tolerance_deg}`;

export function RotatorPanel({ rot }: { rot: RotatorStatus }): JSX.Element {
  const config = useConfig();
  const cfg: RotatorConfig = { ...DEFAULT_ROTATOR_CFG, ...(config?.rotator ?? {}) };

  // ------------------------------------------------------------------ gates
  // Motion: the capability the four motion routes require, plus the device
  // itself, plus the rig's own rotator lane.
  const motion = useLock({ cap: "control.capture", needsRole: "rotator" });
  const motionLane = useLock({ cap: "control.capture", needsRole: "rotator", busyLane: "rotator" });
  // The two plate-solve routes additionally `hub.require("camera")`
  // (`app.py:6180-6198`) and run on the `rotate_to_pa` lane. Naming the camera
  // here turns a 400 the user could not have predicted into a reason on the
  // button.
  const solveLane = useLock({ cap: "control.capture", needsRole: "camera", busyLane: "rotate_to_pa" });
  // The range of motion is a CONFIG write and a different capability.
  const cfgLock = useLock({ cap: "config.backend" });
  const explain = motion.onExplain;

  // ------------------------------------------------------------------ state
  const [draft, setDraft] = useState<RotatorConfig>(cfg);
  const seenCfg = useRef<string>(cfgKey(cfg));
  if (seenCfg.current !== cfgKey(cfg)) {
    seenCfg.current = cfgKey(cfg);
    setDraft(cfg);
  }

  const [dialTile, setDialTile] = useState<DialTile>("pa");
  // The MOVE TO field holds its OWN text (the `SitePanel` / `RotatorCard`
  // raw-draft pattern): re-parsing "4" to a number and back wipes a trailing
  // "." or a lone "-" before the next digit can be typed. It is parsed once,
  // through `toNum`, at the press.
  const [paText, setPaText] = useState<string>("");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const paId = useId();

  const busy = pending != null;

  const run = async (tag: string, fn: () => Promise<unknown>) => {
    setErr(null);
    setPending(tag);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message
        : e instanceof Error ? e.message
          : "the rotator refused the command");
    } finally {
      setPending(null);
    }
  };

  /** Config writes are WHOLESALE REPLACE (hub-rig.md 0.4): read the block,
   *  spread, patch one field, send the whole thing, then re-read what the
   *  server actually stored. The draft is painted first so the segmented
   *  control does not lag a round trip, and is rolled BACK to the server's
   *  value if the write is refused - a config value that stays where the user
   *  put it after a 422 is a lie about the rig. */
  const persist = (patch: Partial<RotatorConfig>) =>
    run("config", async () => {
      const next = { ...draft, ...patch };
      setDraft(next);
      try {
        await setRotatorConfig(next);
        await useStore.getState().loadConfig();
      } catch (e) {
        setDraft(cfg);
        throw e;
      }
    });

  const move = (tag: string, deg: number) =>
    run(tag, () => api.post("/api/rotator/move", { position_deg: mod360(deg) }));

  // ------------------------------------------------------------- derivations
  const paNum = toNum(paText);
  const paOk = Number.isFinite(paNum);
  const mapped = paOk ? adjustedPa(paNum, rot, draft) : null;
  const dialPa = mod360(Math.round(paOk ? paNum : rot.sky_deg));
  const sweep = allowedSweepDeg(draft.range_type);
  const paOptions = useMemo(() => paStops(), []);
  const tolOptions = useMemo(() => toleranceStops(draft.tolerance_deg), [draft.tolerance_deg]);

  const inFlight = busy ? IN_FLIGHT_NOTE : null;
  // The note reflects the PERMANENT half of the lock (capability, device,
  // link), not the transient one: a command in flight is not read-only.
  const motionNote = lockNote(motion.lockedReason, MOTION_LOCK_NOTE);
  const configNote = lockNote(cfgLock.lockedReason, CONFIG_LOCK_NOTE);

  const moveReason = lockNote(motionLane.lockedReason, MOTION_LOCK_NOTE) ?? inFlight;
  const haltReason = motionNote;
  const solveReason = motionNote
    ?? lockNote(solveLane.lockedReason, MOTION_LOCK_NOTE)
    ?? inFlight;
  const goReason = moveReason ?? (paOk ? null : NO_TARGET_NOTE);
  // TEST ROTATOR exposes and turns, on the same lane and the same camera as the
  // two solving buttons, so it takes their lock; and it is locked, with the
  // reason, once the sign is measured and the follow test PASSED - a press
  // would change nothing. A FAILED follow test leaves it live (#697): the
  // server runs that test again, which is the only way back short of
  // reconnecting the whole rig.
  const preflight = rotatorPreflightLine(rot);
  const preflightPassed = (rot.sky_sign === 1 || rot.sky_sign === -1)
    && rot.trusted === true;
  const preflightReason = solveReason ?? (preflightPassed ? PREFLIGHT_DONE_NOTE : null);
  const romReason = configNote ?? inFlight;
  const startReason = romReason ?? (draft.range_type === "full" ? FULL_RANGE_NOTE : null);

  const rangeSub = draft.range_type === "full"
    ? "360° - no start angle"
    : `start ${draft.range_start_deg}° · ${sweep}° sweep`;

  return (
    <>
      <ReadoutGrid cols={4} data-testid="rotator-tiles">
        <ReadoutTile
          label="SKY PA"
          value={`${rot.sky_deg.toFixed(1)}°`}
          sub={rot.synced ? "synced" : "not synced - sync to sky"}
          tone={rot.synced ? undefined : "warn"}
          selected={dialTile === "pa"}
          onSelect={() => setDialTile("pa")}
          ariaLabel={`Sky position angle ${rot.sky_deg.toFixed(1)} degrees, set the move target`}
          data-testid="tile-sky-pa"
        />
        <ReadoutTile
          label="MECHANICAL"
          value={`${rot.mech_deg.toFixed(1)}°`}
          // offset = mechanical - sky (`lib/rotation.ts:108`). It is NOT on the
          // wire; it is derived from the two live values, the same way
          // `adjustedPa` derives it, which is why an unsynced rotator has one
          // at all and why it is meaningless until a sync lands.
          sub={`offset ${mod360(rot.mech_deg - rot.sky_deg).toFixed(1)}°`}
          data-testid="tile-mechanical"
        />
        <ReadoutTile
          label="RANGE"
          value={RANGE_LABEL[draft.range_type] ?? String(draft.range_type).toUpperCase()}
          sub={rangeSub}
          data-testid="tile-range"
        />
        <ReadoutTile
          label="TOLERANCE"
          value={`${draft.tolerance_deg}°`}
          sub="close enough, mod 180"
          selected={dialTile === "tolerance"}
          onSelect={() => setDialTile("tolerance")}
          ariaLabel={`Rotate tolerance ${draft.tolerance_deg} degrees, edit on the dial`}
          data-testid="tile-tolerance"
        />
      </ReadoutGrid>

      {dialTile === "pa" && (
        <Dial<number>
          label={`MOVE TO · ${dialPa}°`}
          hint="drag or tap - then GO"
          options={paOptions}
          value={dialPa}
          onChange={(v) => setPaText(String(v))}
          lockedReason={moveReason}
          onExplain={explain}
          data-testid="rotator-dial"
        />
      )}
      {dialTile === "tolerance" && (
        <Dial<number>
          label={`TOLERANCE · ${draft.tolerance_deg}°`}
          hint="drag or tap"
          options={tolOptions}
          value={draft.tolerance_deg}
          onChange={(v) => void persist({ tolerance_deg: v })}
          lockedReason={romReason}
          onExplain={explain}
          data-testid="rotator-dial"
        />
      )}

      <Card className="nx-rot-stack" data-testid="rotator-motion">
        <div className="nx-rot-arcrow">
          <RotatorArc rot={rot} cfg={draft} />
          <div className="nx-rot-legend">
            <Label size={11}>MOVE THE ROTATOR</Label>
            <Mono size={10.5} tone="dim" data-testid="rotator-arc-legend">
              {sweep === 360
                ? `the whole circle is reachable · ${rot.moving ? "turning now" : "at rest"}`
                : `reachable ${draft.range_start_deg}° to `
                  + `${mod360(draft.range_start_deg + sweep)}° mechanical · `
                  + `${rot.moving ? "turning now" : "at rest"}`}
            </Mono>
          </div>
        </div>

        <div className="nx-rot-pair">
          <Field label="MOVE TO" hint="sky position angle, degrees" htmlFor={paId}
            className="nx-rot-grow">
            <TextInput
              id={paId}
              value={paText}
              onChange={setPaText}
              onEnter={() => { if (mapped) void move("move", mapped.target); }}
              placeholder="PA°"
              mono
              ariaLabel="Target position angle, degrees"
              className="nx-rot-pa"
              lockedReason={moveReason}
              data-testid="rotator-pa"
            />
          </Field>
          <ActionButton
            kind="primary"
            onPress={() => { if (mapped) void move("move", mapped.target); }}
            busy={pending === "move"}
            lockedReason={goReason}
            onExplain={explain}
            data-testid="rotator-move"
          >
            GO
          </ActionButton>
        </div>

        {mapped?.adjusted && (
          <p className="nx-rot-warn" data-testid="rotator-outofrange">
            {outOfRangeLine(paNum, mapped.target)}
          </p>
        )}

        <div className="nx-rot-row">
          <ActionButton
            kind="secondary"
            onPress={() => void move("nudge", rot.sky_deg - 1)}
            busy={pending === "nudge"}
            lockedReason={moveReason}
            onExplain={explain}
            ariaLabel="Turn one degree back"
            data-testid="rotator-nudge-minus"
          >
            -1°
          </ActionButton>
          <ActionButton
            kind="secondary"
            onPress={() => void move("nudge", rot.sky_deg + 1)}
            busy={pending === "nudge"}
            lockedReason={moveReason}
            onExplain={explain}
            ariaLabel="Turn one degree forward"
            data-testid="rotator-nudge-plus"
          >
            +1°
          </ActionButton>
          {/* Single-tap, and live while every control beside it is not. */}
          <ActionButton
            kind="danger"
            onPress={() => void run("halt", () => api.post("/api/rotator/halt"))}
            lockedReason={haltReason}
            onExplain={explain}
            data-testid="rotator-halt"
          >
            HALT
          </ActionButton>
        </div>

        <div className="nx-rot-row">
          <ActionButton
            kind="secondary"
            onPress={() => void run("solve", () => api.post("/api/rotator/rotate-to-pa",
              { target_pa_deg: paOk ? mod360(paNum) : rot.sky_deg }))}
            busy={pending === "solve"}
            lockedReason={solveReason}
            onExplain={explain}
            data-testid="rotator-solve"
          >
            ROTATE TO PA (PLATE SOLVE)
          </ActionButton>
          <ActionButton
            kind="secondary"
            onPress={() => void run("sync", () => api.post("/api/rotator/sync-to-sky", {}))}
            busy={pending === "sync"}
            lockedReason={solveReason}
            onExplain={explain}
            data-testid="rotator-sync"
          >
            SYNC TO SKY (NO MOVEMENT)
          </ActionButton>
          <ActionButton
            kind="secondary"
            onPress={() => void run("preflight", () => api.post("/api/rotator/preflight", {}))}
            busy={pending === "preflight"}
            lockedReason={preflightReason}
            onExplain={explain}
            data-testid="rotator-preflight"
          >
            TEST ROTATOR
          </ActionButton>
        </div>
        <p className="nx-rot-note" data-testid="rotator-sync-note">{SYNC_NOTE}</p>
        <p
          className={preflight.failed ? "nx-rot-err" : "nx-rot-note"}
          data-testid="rotator-preflight-line"
        >
          {preflight.text}
        </p>
        <p className="nx-rot-note" data-testid="rotator-preflight-note">{PREFLIGHT_NOTE}</p>

        {/* Only when the device says it can. A reverse switch on a rotator that
            cannot reverse is a control with a promise nothing keeps. */}
        {rot.can_reverse && (
          <Switch
            checked={rot.reverse}
            onChange={(reverse) => void run("reverse",
              () => api.post("/api/rotator/reverse", { reverse }))}
            label="REVERSE"
            note="turn this on once if the sky angle counts the opposite way to the camera's frame"
            lockedReason={moveReason}
            onExplain={explain}
            data-testid="rotator-reverse"
          />
        )}

        <LockNote reason={motionNote} data-testid="rotator-lock-motion" />
      </Card>

      <Card className="nx-rot-stack" data-testid="rotator-rom">
        <Label size={11}>RANGE OF MOTION</Label>
        <Mono size={10.5} tone="dim">
          how far the rotator may turn before a cable stops it
        </Mono>
        <Segmented<RotatorConfig["range_type"]>
          label="Mechanical range"
          // One source of truth for the three ranges and the sweep each one
          // means: the sub comes from `allowedSweepDeg`, the same function the
          // arc and the reachable-span line are drawn from.
          options={RANGE_OPTIONS.map((v) => ({
            value: v, label: RANGE_LABEL[v], sub: `${allowedSweepDeg(v)}°`,
          }))}
          value={draft.range_type}
          onChange={(range_type) => void persist({ range_type })}
          lockedReason={romReason}
          onExplain={explain}
          data-testid="rotator-range"
        />

        <div className="nx-rot-pair">
          <NumberField
            label="START"
            className="nx-rot-grow"
            value={draft.range_start_deg}
            onCommit={(v) => void persist({ range_start_deg: mod360(v) })}
            unit="deg"
            min={0}
            max={360}
            hint="mechanical degrees where the sweep begins"
            ariaLabel="Range start, mechanical degrees"
            lockedReason={startReason}
            onExplain={explain}
            data-testid="rotator-start"
          />
          <ActionButton
            kind="secondary"
            onPress={() => void persist({ range_start_deg: mod360(rot.mech_deg) })}
            lockedReason={startReason}
            onExplain={explain}
            data-testid="rotator-start-current"
          >
            SET TO CURRENT POSITION
          </ActionButton>
        </div>

        <NumberField
          label="TOLERANCE"
          value={draft.tolerance_deg}
          onCommit={(v) => void persist({ tolerance_deg: v })}
          unit="deg"
          min={0}
          max={90}
          hint="how close ROTATE TO PA has to get before it stops, mod 180"
          ariaLabel="Rotate tolerance, degrees"
          lockedReason={romReason}
          onExplain={explain}
          data-testid="rotator-tolerance"
        />

        <LockNote reason={configNote} data-testid="rotator-lock-config" />
      </Card>

      {err && (
        <p className="nx-rot-err" data-testid="rotator-error">{err}</p>
      )}
    </>
  );
}

export default RotatorPanel;
