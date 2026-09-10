// QuickDefaultsSheet.tsx - QUICK SESSION DEFAULTS (plan section C.3).
//
// THERE IS NO SERVER STORE FOR THIS. It is this phone's memory of the last
// quick session it generated, under `astrodeck-next-quick`, and the Sky hub's
// quick-session sheet is what writes it on GENERATE FLOW. This sheet owns the
// READ and the EDIT, which is why the reader and the writer both live in this
// file and are exported: one shape, one parser, one place for it to be wrong.
//
// NOTHING IS INVENTED. With no stored value the sheet says so and stops - it
// does NOT seed itself from the wheel and then present the seed as "learned
// from your last session", because a default nobody chose, labelled as a
// choice, is the shape of every "the app changed my settings" bug report.
//
// FILTER NAMES COME FROM THE WHEEL (README "Ground rules": never ask the user
// to type one). The rows are the union of the wheel's current slot names and
// whatever the stored value already knows, so a night's defaults do not vanish
// from the screen because the wheel is unplugged right now.

import { useMemo, useState, type JSX } from "react";
import {
  ActionButton, Card, Checkbox22, EmptyCard, Label, Mono, Segmented, Sheet,
  Stepper2, Switch,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useStore } from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import type { SheetProps } from "../../sheets";

export const QUICK_KEY = "astrodeck-next-quick";

export type QuickHours = 1 | 2 | 3 | 4 | "dawn";

export interface QuickExtras {
  af: boolean;
  guide: boolean;
  dither: boolean;
  ditherN: number;
  cloud: boolean;
  hfr: boolean;
  liveStack: boolean;
}

export interface QuickDefaults {
  hours: QuickHours;
  /** Keyed by the WHEEL's filter names, never by an index: a slot that moves
   *  must not silently take another filter's exposure with it. */
  filters: Record<string, boolean>;
  exp: Record<string, number>;
  extras: QuickExtras;
}

function coerceHours(v: unknown): QuickHours {
  if (v === "dawn") return "dawn";
  const n = Number(v);
  return n === 1 || n === 2 || n === 3 || n === 4 ? (n as QuickHours) : 2;
}

function coerceBoolMap(v: unknown): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) out[k] = !!val;
  }
  return out;
}

function coerceNumMap(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      const n = Number(val);
      if (Number.isFinite(n) && n > 0) out[k] = n;
    }
  }
  return out;
}

/** null means NOTHING HAS BEEN LEARNED - which is not the same claim as "the
 *  defaults happen to be empty", and the sheet renders the two differently. */
export function readQuickDefaults(): QuickDefaults | null {
  try {
    const raw = localStorage.getItem(QUICK_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Record<string, unknown>;
    const ex = (p.extras ?? {}) as Record<string, unknown>;
    const ditherN = Number(ex.ditherN);
    return {
      hours: coerceHours(p.hours),
      filters: coerceBoolMap(p.filters),
      exp: coerceNumMap(p.exp),
      extras: {
        af: ex.af !== false,
        guide: ex.guide !== false,
        dither: ex.dither !== false,
        ditherN: Number.isFinite(ditherN) && ditherN > 0 ? Math.round(ditherN) : 3,
        cloud: ex.cloud !== false,
        hfr: ex.hfr !== false,
        liveStack: ex.liveStack !== false,
      },
    };
  } catch {
    // Private mode, cleared site data, or a value written by a build that
    // shaped it differently: forget it rather than half-apply it.
    return null;
  }
}

export function writeQuickDefaults(q: QuickDefaults): void {
  try { localStorage.setItem(QUICK_KEY, JSON.stringify(q)); } catch { /* quota */ }
}

export function clearQuickDefaults(): void {
  try { localStorage.removeItem(QUICK_KEY); } catch { /* quota */ }
}

const HOURS = [
  { value: 1 as QuickHours, label: "1 h" },
  { value: 2 as QuickHours, label: "2 h" },
  { value: 3 as QuickHours, label: "3 h" },
  { value: 4 as QuickHours, label: "4 h" },
  { value: "dawn" as QuickHours, label: "To dawn" },
];

const EXTRA_ROWS: { key: keyof QuickExtras; label: string; note: string }[] = [
  { key: "af", label: "Autofocus", note: "sweeps on a filter change and when the temperature drifts" },
  { key: "guide", label: "Guiding", note: "calibrates once, then holds the star all night" },
  { key: "dither", label: "Dither", note: "nudges between subs so the stack cancels sensor pattern" },
  { key: "cloud", label: "Cloud hold", note: "pauses at a frame boundary while the forecast is over the threshold" },
  { key: "hfr", label: "HFR watchdog", note: "refocuses when stars swell past the run's own baseline" },
  { key: "liveStack", label: "Live stack", note: "builds the running picture you watch on Session - Now" },
];

const NOTE = {
  margin: "6px 0 0", fontSize: "11.5px", lineHeight: 1.45, color: "var(--text-faint)",
} as const;

const FILTER_ROW = {
  display: "flex", alignItems: "center", gap: "12px",
  padding: "8px 0", minHeight: "44px",
} as const;

export function QuickDefaultsSheet(_p: SheetProps): JSX.Element {
  const [q, setQ] = useState<QuickDefaults | null>(readQuickDefaults);
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
    for (const k of Object.keys(q?.filters ?? {})) if (!out.includes(k)) out.push(k);
    return out;
  }, [wheelNames, wheelOpaque, q]);

  const save = (next: QuickDefaults) => { setQ(next); writeQuickDefaults(next); };

  const reset = () => {
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
      clearQuickDefaults();
      setQ(null);
      enqueueToast({ level: "info", title: "Quick session defaults forgotten." });
    });
  };

  return (
    <Sheet
      data-testid="settings-quick-defaults"
      title="QUICK SESSION DEFAULTS"
      sub="learned from your last session"
      icon={<NxIcon name="clock" />}
      onBack={nav.back}
      footer={
        q ? (
          <ActionButton
            kind="secondary"
            full
            onPress={reset}
            data-testid="quick-reset"
          >
            RESET TO THE WHEEL&rsquo;S DEFAULTS
          </ActionButton>
        ) : undefined
      }
    >
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
            <Segmented
              options={HOURS}
              value={q.hours}
              onChange={(v) => save({ ...q, hours: v })}
              label="How long the quick session runs"
              data-testid="quick-hours"
            />
            <p style={NOTE}>
              How much of the night a generated flow claims. To dawn ends the run
              at the end of astronomical night.
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
                const on = q.filters[f] === true;
                const exp = q.exp[f] ?? 60;
                return (
                  <div key={f} style={FILTER_ROW}>
                    <Checkbox22
                      checked={on}
                      onChange={(next) =>
                        save({ ...q, filters: { ...q.filters, [f]: next } })}
                      label={f}
                      data-testid={`quick-filter-${f}`}
                    />
                    <span style={{ flex: 1 }} />
                    <Stepper2
                      value={exp}
                      onChange={(v) => save({ ...q, exp: { ...q.exp, [f]: v } })}
                      step={30}
                      min={1}
                      max={1800}
                      format={(v) => `${v} s`}
                      label={`${f} exposure`}
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
                checked={q.extras[r.key] === true}
                onChange={(v) => save({ ...q, extras: { ...q.extras, [r.key]: v } })}
                label={r.label}
                note={r.note}
                data-testid={`quick-extra-${r.key}`}
              />
            ))}
            <div style={FILTER_ROW}>
              <span style={{ flex: 1 }}>
                <Mono size={11}>Dither every</Mono>
              </span>
              <Stepper2
                value={q.extras.ditherN}
                onChange={(v) => save({ ...q, extras: { ...q.extras, ditherN: v } })}
                step={1}
                min={1}
                max={10}
                format={(v) => `${v} subs`}
                label="Dither every N subs"
                data-testid="quick-dither-n"
              />
            </div>
          </Card>
        </>
      )}
    </Sheet>
  );
}
