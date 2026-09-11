// QuickDefaultsSheet.tsx - QUICK SESSION DEFAULTS (plan section C.3).
//
// THE DEFAULTS LIVE ON THE RIG (D-FU-1). They were this phone's memory of the
// last quick session it generated; they are `config.planning.quick` now, read
// and written through `next/lib/planning.ts`, so the tablet in the warm room
// and the phone at the scope are looking at one answer - and the rig itself can
// finally tell a client what it has learned. On an engine with no
// `/api/planning` the store falls back to the browser key and this screen
// behaves exactly as it shipped.
//
// THIS FILE OWNS EXACTLY ONE THING: the screen. It used to own a key as well -
// `astrodeck-next-quick`, with its own shape and its own coercions - while the
// sheet that actually generates the night used `astrodeck-next-sky-quick`. The
// two never met (review #4): this sheet said "nothing learned yet" forever no
// matter how many sessions had been generated, and every edit made here was
// ignored by the sheet it claimed to be the defaults for. Both directions were
// broken, and both were green in their own tests, because each test asserted the
// sheet against the key the sheet itself owned. There is one store now, and the
// test grades this screen against what the SKY sheet reads.
//
// NOTHING IS INVENTED. With nothing learned the sheet says so and stops - it
// does NOT seed itself from the wheel and then present the seed as "learned from
// your last session", because a default nobody chose, labelled as a choice, is
// the shape of every "the app changed my settings" bug report. The rig's
// `quick.learned` flag is what distinguishes that from "the defaults happen to
// be empty" (`config.py:1124-1128`), and it is set by the sheet that learns -
// the Sky hub's - never implicitly by the route.
//
// A ROLE THAT CANNOT WRITE STILL SEES ALL OF IT, honest-disabled: the pool and
// the defaults read at `view.status`, and hiding what a viewer cannot change
// would hide what the rig is about to do tonight.
//
// FILTER NAMES COME FROM THE WHEEL (README "Ground rules": never ask the user to
// type one). The rows are the union of the wheel's current slot names and
// whatever the stored value already knows, so a night's defaults do not vanish
// from the screen because the wheel is unplugged right now.

import { useMemo, type JSX } from "react";
import {
  ActionButton, Card, Checkbox22, EmptyCard, Label, LockNote, Mono, Segmented, Sheet,
  Stepper2, Switch,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { explainLock } from "../../../shell/explain";
import { useStore } from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import type { SheetProps } from "../../sheets";
import { usePlanning } from "../../../lib/planning";
import { type QuickPrefs } from "../../sky/finder";

export type QuickHours = 1 | 2 | 3 | 4 | "dawn";

/** The five stops, as the segmented control offers them. "dawn" is a CHOICE,
 *  not a number of hours: dawn is a different length every night, so storing
 *  tonight's 5.2 h and replaying it in December would turn "all night" into
 *  "5h 12m" with the screen saying 5h 12m. `QuickPrefs.dawn` carries the
 *  choice; the Sky sheet resolves it against tonight's dawn. */
const HOURS: { value: QuickHours; label: string }[] = [
  { value: 1, label: "1 h" },
  { value: 2, label: "2 h" },
  { value: 3, label: "3 h" },
  { value: 4, label: "4 h" },
  { value: "dawn", label: "To dawn" },
];

export function hoursValue(q: QuickPrefs): QuickHours {
  if (q.dawn) return "dawn";
  const n = Math.round(q.hours);
  return n === 1 || n === 3 || n === 4 ? (n as QuickHours) : 2;
}

/** The CHANGED KEYS for a night-length pick, not a whole block: the rig merges
 *  the quick block field by field, and sending the rest of it back would
 *  overwrite whatever another client learned in the meantime. */
export function withHours(v: QuickHours): Partial<QuickPrefs> {
  return v === "dawn" ? { dawn: true } : { hours: v, dawn: false };
}

/** The automation rows, keyed EXACTLY as `QuickPrefs.extras` and the Sky
 *  sheet's own chips key them. The old copy of this list called live stacking
 *  `liveStack`; the chip that writes it calls it `stack`, so the row and the
 *  chip were two different settings with one label. */
const EXTRA_ROWS: { key: string; label: string; note: string }[] = [
  { key: "af", label: "Autofocus", note: "sweeps on a filter change and when the temperature drifts" },
  { key: "guide", label: "Guiding", note: "calibrates once, then holds the star all night" },
  { key: "dither", label: "Dither", note: "nudges between subs so the stack cancels sensor pattern" },
  { key: "cloud", label: "Cloud hold", note: "pauses at a frame boundary while the forecast is over the threshold" },
  { key: "hfr", label: "HFR watchdog", note: "refocuses when stars swell past the run's own baseline" },
  { key: "stack", label: "Live stack", note: "builds the running picture you watch on Session - Now" },
];

const NOTE = {
  margin: "6px 0 0", fontSize: "11.5px", lineHeight: 1.45, color: "var(--text-faint)",
} as const;

const FILTER_ROW = {
  display: "flex", alignItems: "center", gap: "12px",
  padding: "8px 0", minHeight: "44px",
} as const;

export function QuickDefaultsSheet(_p: SheetProps): JSX.Element {
  const planning = usePlanning();
  // `learned` is the rig's answer to "has anything ever been learned here", so
  // `q` is null for exactly the case the empty face is written for.
  const q: QuickPrefs | null = planning.learned ? planning.quick : null;
  const enqueueToast = useStore((s) => s.enqueueToast);
  const wheelNames = useStore((s) => s.status?.filterwheel?.names ?? null);
  const wheelOpaque = useStore((s) => s.status?.filterwheel?.opaque ?? null);

  // The wheel's own light-passing slots, in slot order, plus anything the
  // stored value already names. A blackout slot is not a filter you image with,
  // so it is not offered here.
  const names = useMemo(() => {
    const out: string[] = [];
    (wheelNames ?? []).forEach((n, i) => {
      const name = (n ?? "").trim();
      if (!name) return;
      if (wheelOpaque && wheelOpaque[i]) return;
      if (!out.includes(name)) out.push(name);
    });
    for (const k of Object.keys(q?.on ?? {})) if (!out.includes(k)) out.push(k);
    return out;
  }, [wheelNames, wheelOpaque, q]);

  // EVERY control takes this, not just the footer's RESET. `save()` already
  // refused a write from a role without `control.capture` and explained itself
  // - but only AFTER the press, from a control that looked live, with the row
  // visibly moving under the finger before the toast arrived. ARCHITECTURE
  // section 8's rule is the opposite: dim it, say why, fire nothing.
  const locked = planning.lockedReason;
  const save = (patch: Partial<QuickPrefs>) => {
    if (planning.lockedReason) { explainLock(planning.lockedReason); return; }
    planning.putQuick(patch);
  };

  const reset = () => {
    if (planning.lockedReason) { explainLock(planning.lockedReason); return; }
    void confirmDialog({
      title: "Forget these defaults?",
      body:
        "The next quick session starts from the wheel's own slot exposures again, " +
        "and becomes the new default here once you generate it.",
      tone: "warn",
      mode: "confirm",
      confirmLabel: "FORGET",
    }).then((ok) => {
      if (!ok) return;
      planning.forgetQuick();
      enqueueToast({ level: "info", title: "Quick session defaults forgotten." });
    });
  };

  return (
    <Sheet
      data-testid="settings-quick-defaults"
      title="QUICK SESSION DEFAULTS"
      sub={planning.mode === "rig"
        ? "learned from your last session, kept on the rig"
        : "learned from your last session, kept on this phone"}
      icon={<NxIcon name="clock" />}
      onBack={nav.back}
      footer={
        q ? (
          <ActionButton
            kind="secondary"
            full
            onPress={reset}
            data-testid="quick-reset"
            lockedReason={locked}
            onExplain={explainLock}
          >
            RESET TO THE WHEEL&rsquo;S DEFAULTS
          </ActionButton>
        ) : undefined
      }
    >
      {/* One sentence for a role that can read the rig's plan but not change
          it, in the same words every locked press repeats. */}
      <LockNote reason={locked} />
      {!q ? (
        <EmptyCard
          title="NOTHING LEARNED YET"
          hint="The first quick session you generate becomes the default here."
          data-testid="quick-empty"
        />
      ) : (
        <>
          <Label>NIGHT LENGTH</Label>
          <Card>
            <Segmented<QuickHours>
              options={HOURS}
              value={hoursValue(q)}
              onChange={(v) => save(withHours(v))}
              label="How long the quick session runs"
              lockedReason={locked}
              onExplain={explainLock}
              data-testid="quick-hours"
            />
            <p style={NOTE}>
              How much of the night a generated flow claims. To dawn ends the run
              at the end of astronomical night, whatever length that is tonight.
            </p>
          </Card>

          <Label>FILTERS AND EXPOSURE</Label>
          <Card data-testid="quick-filters">
            {names.length === 0 ? (
              <Mono size={11}>
                No filter names yet - connect the filter wheel and its slot names
                appear here.
              </Mono>
            ) : (
              names.map((f) => {
                // ABSENT MEANS CHECKED, which is `wheelModel`'s own rule for a
                // slot the operator has never touched. Reading `=== true` here
                // showed every fresh slot as unticked on this screen while the
                // Sky sheet shot it.
                const on = q.on[f] !== false;
                const exp = q.exp[f] ?? 60;
                return (
                  <div key={f} style={FILTER_ROW}>
                    <Checkbox22
                      checked={on}
                      onChange={(next) =>
                        save({ on: { ...q.on, [f]: next } })}
                      label={f}
                      lockedReason={locked}
                      onExplain={explainLock}
                      data-testid={`quick-filter-${f}`}
                    />
                    <span style={{ flex: 1 }} />
                    <Stepper2
                      value={exp}
                      onChange={(v) => save({ exp: { ...q.exp, [f]: v } })}
                      step={30}
                      min={1}
                      max={1800}
                      format={(v) => `${v} s`}
                      label={`${f} exposure`}
                      lockedReason={locked}
                      onExplain={explainLock}
                      data-testid={`quick-exp-${f}`}
                    />
                  </div>
                );
              })
            )}
          </Card>

          <Label>AUTOMATION</Label>
          <Card>
            {EXTRA_ROWS.map((r) => (
              <Switch
                key={r.key}
                checked={q.extras[r.key] !== false}
                onChange={(v) => save({ extras: { ...q.extras, [r.key]: v } })}
                label={r.label}
                note={r.note}
                lockedReason={locked}
                onExplain={explainLock}
                data-testid={`quick-extra-${r.key}`}
              />
            ))}
            <div style={FILTER_ROW}>
              <span style={{ flex: 1 }}>
                <Mono size={11}>Dither every</Mono>
              </span>
              <Stepper2
                value={q.ditherN}
                onChange={(v) => save({ ditherN: v })}
                step={1}
                min={1}
                max={10}
                format={(v) => `${v} subs`}
                label="Dither every N subs"
                lockedReason={locked}
                onExplain={explainLock}
                data-testid="quick-dither-n"
              />
            </div>
          </Card>
        </>
      )}
    </Sheet>
  );
}
