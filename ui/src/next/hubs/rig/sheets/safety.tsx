// safety.tsx - the SAFETY MONITOR device sheet (plan hub-rig.md B.9, T-RIG-6).
//
// WHAT THIS SHEET IS FOR. Everything on it answers one of two questions: what
// is the rig measuring right now, and what will it DO when that measurement
// turns. Both answers are built from the engine's own state, never drawn as a
// picture of a rig we wish we had:
//
//   * The inputs card renders ONE BAR PER KEY the monitor actually sent
//     (`reading.detail`). The design's five bars - rain, wind, cloud, power,
//     humidity - are fixtures in the prototype; five bars over a device that
//     reports one number is a claim outrunning its evidence, and the DOM test
//     asserts that RAIN/WIND/CLOUD are absent when `detail` is.  (E11)
//   * The trip chain is assembled from `on_unsafe`, `close_dome_on_unsafe` plus
//     a connected dome, `cooling.warm_ramp` and whether any alert sink exists.
//     A node the engine will not run is dim AND SAYS WHY - "CLOSE is off - no
//     roof is connected" - rather than quietly vanishing.  (E12)
//
// WRITE MODEL, and why it is two models. A control writes IMMEDIATELY when it is
// the only control that owns its field: a switch that needs a separate Save is
// how an interlock nobody armed ends up LOOKING armed, which is the failure this
// sheet exists to prevent. A field two controls share - the pier floor's switch
// and its degrees, the exclusion cone's angle - goes through a draft with its
// card's own SAVE, because posting each intermediate stepper press would drive a
// mount limit through values nobody asked for. Every write echoes the WHOLE
// safety block (the server replaces it wholesale) and every card confirms AT ITS
// OWN CONTROL: one panel-wide "Saved" line used to sit under the wrong control
// and tell somebody their 45 degree cone was stored while the mount still
// enforced 30 (inventory-settings-weather.md 9.1).
//
// REBASE, DON'T REPLACE (item 11). Several cards write the same block, and the
// sun card's save comes back as a genuinely changed block. A plain re-seed on
// every config frame would throw away a half-entered altitude floor, so the
// effect diffs the fresh block against the last SERVER-seeded baseline and
// carries forward only the keys the user actually changed
// (SafetyLimitsPanel.tsx:120-143).
//
// THE ONE FIELD-LEVEL DOUBLE GATE. Writing `solar_avoidance` or
// `solar_exclusion_deg` needs `config.solar_override` ON TOP of `config.safety`,
// enforced by the server inside the single /api/config route
// (app.py:3218-3230). Only admin holds the override, so an operator with
// config.safety sees this ONE card read-only while the rest of the sheet stays
// editable - two `useLock` calls, not one.

import { useEffect, useMemo, useRef, useState, type JSX, type ReactNode } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { useBreakpoint } from "../../../breakpoint";
import {
  ActionButton, Bar, Card, Divider, Label, ListRow, Mono, Segmented, Sheet,
  Stepper2, Switch,
} from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import {
  useConfig, useSafety, useStatus, useStore,
} from "../../../../store";
import { accessPhrase, useCan } from "../../../../lib/caps";
import { FLIP_SITE_REASON } from "../../monitor/live/FlipTile";
import { api, ApiError } from "../../../../api";
import {
  closeDome, getDomeState, setSafetyConfig, type DomeState,
} from "../../../../api/backends";
import { getAlertHealth } from "../../../../api/alerts";
import { domeStatusLabel } from "../../../../lib/dome";
import { useBusyOrPending } from "../../../../lib/useBusy";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { EscalationEditor } from "../../settings/tuning/safety";
import type {
  AlertHealth, EscalationConfig, NoGoWedge, SafetyConfig,
} from "../../../../types";
import {
  CHAIN_NOTE, chainReasons, safetyChain, type ChainNode,
} from "../lib/safetyChain";
import {
  NO_LIMIT_SUB, STALE_NOTE, readingAgeS, safetyBars, safetyLimitsFromConfig,
  safetyLive, streakLine, type SafetyBarRow,
} from "../lib/safetyBars";

// `sky_fallback_hold` is a real server field (config.py:142) and `SafetyConfig`
// in `ui/src/types.ts` now declares it, so this alias is the real type and no
// longer a local widening. It stays as a NAME because every wholesale-replace
// echo below is typed against it.
type SafetyBlock = SafetyConfig;

// ---------------------------------------------------------------- copy, verbatim

const DISARM_BODY =
  "This turns OFF the sun-exclusion cone. The mount will be allowed to slew AT and "
  + "track the Sun. Only do this for a SOLAR scope with a proper solar filter "
  + "installed - pointing unfiltered optics at the Sun can destroy the camera and "
  + "instantly blind anyone looking through the gear.";

const CONE_INERT_BANNER =
  "Exclusion cone is 0 degrees - sun avoidance is switched on but INERT: a 0 degree "
  + "cone blocks nothing, so the mount can still slew at the Sun. Set an angle above "
  + "0 to re-arm.";

const DISARMED_BANNER =
  "SUN AVOIDANCE OFF - the mount is allowed to slew at the Sun. Confirm a solar "
  + "filter is installed before any daytime slew. Turn this back on as soon as solar "
  + "observing is done.";

const SUN_DOUBLE_GATE_NOTE =
  "The sun cone is this app's one field-level double gate: the server wants "
  + "config.solar_override on top of config.safety before it will store either value, "
  + "and only an admin holds the override.";

const SAFETY_OFF_BANNER =
  "SAFETY OFF - nothing is watching the WEATHER. An unattended run will keep imaging "
  + "through rain and cloud. The altitude floor, zenith keep-out and pier guard below "
  + "are still enforced on every slew.";

const PAUSE_FOREVER_WARNING =
  "Set to 0 - a paused run will wait forever with the gear out under an open sky. "
  + "Give it a limit if the rig is unattended.";

const SKY_HOLD_SUB =
  "When safety is armed and no monitor is assigned, a cloudy verdict from the frames "
  + "themselves holds the run - stand down the guider, probe, resume on a clear streak "
  + "- instead of parking. It reads the sky, not the forecast.";

const FLOOR_SUB =
  "The altitude the mount must stay above so it cannot reach its own tripod. This is "
  + "not a 'worth shooting' minimum - that lives in the plan and the site horizon.";

const CEILING_SUB =
  "The altitude the mount must stay below. A mount can foul its own tripod at HIGH "
  + "altitude while still pointing at open sky.";

const NOGO_NOTE =
  "A wedge may wrap through north: 350 to 10 is the 20 degree span across due north. "
  + "Setting both azimuths the same covers the whole sky.";

const NO_PIER_SIDE_REASON = "this mount does not report pier side";

const MERIDIAN_SUB =
  "Lead time for the flip countdown. The flip itself is scheduled by the plan; this "
  + "only sets how much warning you get.";

const WATCHDOG_HINT =
  "Treat the run as unsafe if no frame has been saved for this long. What happens next "
  + "is whatever your 'When conditions are unsafe' action says.";

const REOPEN_NEEDS_CLOSE = 'Turn on "Close roof on unsafe" first - reopening requires closing';

const EMPTY_SINKS_WARNING =
  "Nothing is watching this rig. With no channel here, a cloud pause at 01:15 or a "
  + "mount that stops tracking reaches a browser tab and nowhere else - you find out in "
  + "the morning. Add one channel (ntfy is two taps and needs no account) and the rig "
  + "can wake you.";

const FOOTER_NOTE =
  "Red ticks are the limits the rig enforces. Everything else is the monitor's own "
  + "threshold - change those on the device.";

const TWILIGHT: { value: number; label: string; sub: string }[] = [
  { value: -6, label: "CIVIL", sub: "-6 - earliest start, bright sky" },
  { value: -12, label: "NAUTICAL", sub: "-12 - the usual compromise" },
  { value: -18, label: "ASTRO", sub: "-18 - truly dark, shortest window" },
];

// Labels from SafetyLimitsPanel.tsx:75-85, shortened to the segmented control's
// width; the blurb under the picker carries the full sentence.
const ON_UNSAFE: { value: SafetyConfig["on_unsafe"]; label: string; blurb: string }[] = [
  { value: "warn", label: "WARN", blurb: "Keep shooting, just say so. For testing a new monitor." },
  { value: "pause", label: "PAUSE", blurb: "Stop starting new frames, hold, and resume when it clears." },
  { value: "park", label: "PARK", blurb: "Park the mount. The camera stays cold, so you can restart quickly." },
  {
    value: "abort_park_warm", label: "PARK + WARM",
    blurb: "End the night: park the mount now, then warm the camera down to ambient on a "
      + "slow ramp instead of switching the cooler off.",
  },
];

const PRESET_BLURB: Record<string, string> = {
  backyard: "You are nearby. A cloud or rain trip pauses and waits, and picks back up when it clears.",
  remote: "Nobody is there. A trip ends the night: park the mount and ramp the camera's cooler back to ambient rather than wait.",
  custom: "Your own combination of the settings below.",
};

// The five values SAFETY_PRESETS owns (config.py:107-114) plus the dome flag the
// "remote" entry sets. Editing any of them means the preset no longer describes
// the rig, so the label goes to "custom" in the SAME write rather than lying
// until the server re-derives it.
const PRESET_OWNED: readonly (keyof SafetyBlock)[] = [
  "on_unsafe", "unsafe_consecutive", "resume_when_safe", "resume_safe_consecutive",
  "max_pause_min", "close_dome_on_unsafe",
];

const ACTION_LABEL: Record<string, string> = {
  warn: "Log it and carry on",
  skip: "Skip this target",
  abort: "End the run",
};

const HFR_LABEL: Record<string, string> = {
  warn: "keep and warn",
  discard: "discard",
  retake: "shoot a replacement",
};

// ---------------------------------------------------------------- small pieces

function Note({ children, tone = "dim", testid }: {
  children: ReactNode;
  tone?: "dim" | "warn" | "bad" | "good";
  testid?: string;
}): JSX.Element {
  const color = tone === "warn" ? "var(--warn)"
    : tone === "bad" ? "var(--bad)"
      : tone === "good" ? "var(--good)" : "var(--text-faint)";
  return (
    <p style={{ fontSize: 11.5, lineHeight: 1.5, color, margin: 0, padding: "0 2px" }}
      data-testid={testid}>{children}</p>
  );
}

function Banner({ tone, children, testid }: {
  tone: "warn" | "bad";
  children: ReactNode;
  testid?: string;
}): JSX.Element {
  const c = tone === "bad" ? "var(--bad)" : "var(--warn)";
  return (
    <div
      role="status"
      data-testid={testid}
      style={{
        border: `1px solid ${c}`, borderRadius: 12, padding: "8px 10px",
        background: "color-mix(in srgb, var(--bg-raise) 80%, transparent)",
        fontSize: 11.5, lineHeight: 1.5, color: "var(--text)",
      }}
    >{children}</div>
  );
}

/** A card's own save row: the button, the unsaved note naming the SERVER's
 *  value, and the confirmation - all beside the control they belong to. */
function SaveRow({ dirty, busy, saved, err, onSave, lockedReason, onExplain, unsavedNote, testid }: {
  dirty: boolean;
  busy: boolean;
  saved: boolean;
  err: string | null;
  onSave: () => void;
  lockedReason: string | null;
  onExplain: (r: string) => void;
  unsavedNote?: string;
  testid: string;
}): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <ActionButton
        kind={dirty ? "primary" : "secondary"}
        onPress={onSave}
        busy={busy}
        lockedReason={lockedReason ?? (dirty ? null : "Nothing has changed here yet")}
        onExplain={onExplain}
        data-testid={testid}
      >
        SAVE
      </ActionButton>
      {dirty && !busy && unsavedNote && <Mono size={10.5} tone="warn">{unsavedNote}</Mono>}
      {saved && !dirty && !busy && !err && <Mono size={10.5} tone="good">Saved</Mono>}
      {err && <Mono size={10.5} tone="bad">{err}</Mono>}
    </div>
  );
}

function ChainRow({ nodes }: { nodes: ChainNode[] }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}
      data-testid="safety-chain-nodes">
      {nodes.map((n, i) => (
        <div key={n.id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div
            data-testid={`safety-chain-${n.id}`}
            data-lit={n.lit ? "true" : "false"}
            title={n.reason ?? undefined}
            style={{
              padding: "6px 9px", borderRadius: 8,
              border: "1px solid var(--line-bright)", background: "var(--bg)",
              fontFamily: "'Chakra Petch', sans-serif", fontWeight: 600,
              fontSize: 10, letterSpacing: ".12em",
              color: n.lit ? "var(--text)" : "var(--text-faint)",
              opacity: n.lit ? 1 : 0.55,
              borderStyle: n.lit ? "solid" : "dashed",
            }}
          >{n.label}</div>
          {i < nodes.length - 1 && (
            <div aria-hidden="true" style={{ width: 10, height: 2, background: "var(--bad)" }} />
          )}
        </div>
      ))}
    </div>
  );
}

function BarRow({ row }: { row: SafetyBarRow }): JSX.Element {
  const color = row.tone === "bad" ? "var(--bad)" : "var(--accent)";
  return (
    <div
      data-testid={`safety-bar-${row.key}`}
      style={{
        display: "flex", alignItems: "center", gap: 12, padding: "10px 2px",
        minHeight: 60, borderBottom: "1px solid var(--line)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 34, height: 34, borderRadius: 10, border: "1px solid var(--line)",
          background: "var(--bg)", color, display: "flex",
          alignItems: "center", justifyContent: "center", flexShrink: 0,
        }}
      ><NxIcon name={row.glyph} size={18} /></span>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
          <Label>{row.label}</Label>
          <Mono size={11} tone={row.tone === "bad" ? "bad" : undefined}>{row.text}</Mono>
        </div>
        <div style={{ position: "relative" }}>
          <Bar value={row.pct} height={6} tone={row.tone === "bad" ? "bad" : "accent"}
            label={`${row.label} ${row.text}`} />
          {row.limPct != null && (
            <span
              aria-hidden="true"
              data-testid={`safety-tick-${row.key}`}
              style={{
                position: "absolute", left: `${row.limPct * 100}%`, top: -3,
                width: 2, height: 12, background: "var(--bad)", borderRadius: 1,
              }}
            />
          )}
        </div>
        <Mono size={10} tone="dim">{row.sub}</Mono>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the sheet

export function SafetySheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const server = (config?.safety ?? null) as SafetyBlock | null;
  const cooling = config?.cooling;
  const escalation = config?.escalation ?? null;
  const sinks = config?.alerts ?? [];
  const safety = useSafety();
  const status = useStatus();
  const canSiteDerived = useCan("view.site_derived");
  const bp = useBreakpoint();

  // Two locks, because the sun cone is two capabilities (server-routes.md 4.15).
  const safetyLock = useLock({ cap: "config.safety" });
  const overrideLock = useLock({ cap: "config.solar_override" });
  const alertsLock = useLock({ cap: "config.alerts" });
  const roofCloseLock = useLock({ cap: "control.mount", busyLane: "dome" });
  const solarReason = safetyLock.lockedReason ?? overrideLock.lockedReason;
  const explain = safetyLock.onExplain;

  const [draft, setDraft] = useState<SafetyBlock | null>(null);
  const baseRef = useRef<SafetyBlock | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ where: string; msg: string } | null>(null);
  const [savedWhere, setSavedWhere] = useState<string | null>(null);
  const [dome, setDome] = useState<DomeState | null>(null);
  const [health, setHealth] = useState<AlertHealth | null>(null);
  const [closePhase, setClosePhase] = useState<"idle" | "sending" | "closing">("idle");

  // REBASE, DON'T REPLACE. Keyed on the SERIALISED block, never the object:
  // `config.safety` is a fresh object on every config reload, so an object dep
  // fires on reloads that changed nothing here and wipes what is half-entered.
  const sig = server ? JSON.stringify(server) : null;
  useEffect(() => {
    if (!sig) return;
    const fresh = JSON.parse(sig) as SafetyBlock;
    const base = baseRef.current;
    baseRef.current = fresh;
    setDraft((d) => {
      if (!d || !base) return fresh;
      const mine: Record<string, unknown> = {};
      for (const k of Object.keys(fresh) as (keyof SafetyBlock)[]) {
        if (JSON.stringify(d[k]) !== JSON.stringify(base[k])) mine[k] = d[k];
      }
      return { ...fresh, ...mine };
    });
    setErr(null);
  }, [sig]);

  // The roof is outside the 2 s status frame, so it gets its own slow poll. It
  // used to be read once on mount, and a rain trip five minutes later left the
  // badge saying "Roof open" for the rest of the visit.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const pull = () => {
      getDomeState().then((d) => { if (live.current) setDome(d); }).catch(() => { /* no dome */ });
      getAlertHealth().then((h) => { if (live.current) setHealth(h); }).catch(() => { /* view.status only */ });
    };
    pull();
    const id = window.setInterval(pull, 15000);
    return () => { live.current = false; window.clearInterval(id); };
  }, []);

  const { busy: gotoBusy, arm: armRoofClose } = useBusyOrPending("goto");
  const ourClose = closePhase !== "idle";
  useEffect(() => {
    if (closePhase !== "closing" || gotoBusy) return;
    setClosePhase("idle");
    getDomeState().then((d) => { if (live.current) setDome(d); }).catch(() => { /* no dome */ });
  }, [closePhase, gotoBusy]);

  const reading = safety?.reading ?? null;
  const nowMs = Date.now();
  const liveLine = safetyLive(!!safety?.connected, reading, nowMs);
  const bars = useMemo(
    () => safetyBars(reading?.detail, safetyLimitsFromConfig()),
    [reading?.detail],
  );

  if (!server || !draft) {
    return (
      <Sheet
        data-testid="rig-safety"
        title="SAFETY MONITOR"
        icon={<NxIcon name="safety" size={18} />}
        live="waiting for the rig's safety configuration"
        onBack={() => nav.back()}
        backLabel="RIG"
      >
        <Note>The safety block has not arrived yet. Nothing here is editable until it does.</Note>
      </Sheet>
    );
  }

  // ------------------------------------------------------------------ writes

  /** Echo the WHOLE block with `patch` applied. Returns true only when the
   *  server took it - the switches use that to put themselves back where the
   *  SERVER is, because a switch here is a readout of what the rig will do when
   *  the rain sensor trips, not a draft. */
  const post = async (where: string, patch: Partial<SafetyBlock>): Promise<boolean> => {
    if (busy) return false;
    setErr(null);
    setBusy(where);
    try {
      const flips = (Object.keys(patch) as (keyof SafetyBlock)[])
        .some((k) => PRESET_OWNED.includes(k) && JSON.stringify(patch[k]) !== JSON.stringify(server[k]));
      const body: SafetyBlock = { ...server, ...patch };
      // A preset is a LABEL for five numbers (config.py:107-114). Edit one and
      // the label no longer describes the rig, so it goes to custom in the same
      // write rather than lying until the server re-derives it on the next read.
      if (flips) body.preset = "custom";
      await setSafetyConfig(body);
      await useStore.getState().loadConfig();
      setSavedWhere(where);
      return true;
    } catch (e) {
      const msg = e instanceof ApiError
        ? (e.status === 403 ? `Refused - ${accessPhrase("config.safety")} is needed here.` : e.message || "Could not save.")
        : "Could not save.";
      setErr({ where, msg });
      return false;
    } finally {
      setBusy(null);
    }
  };

  /** A card's SAVE: only the fields that card owns, so one card's unsaved edit
   *  can never ride out on another card's write. */
  const saveCard = (where: string, keys: (keyof SafetyBlock)[]) => {
    const patch: Partial<SafetyBlock> = {};
    for (const k of keys) (patch as Record<string, unknown>)[k as string] = draft[k];
    return post(where, patch);
  };
  const cardDirty = (keys: (keyof SafetyBlock)[]) =>
    keys.some((k) => JSON.stringify(draft[k]) !== JSON.stringify(server[k]));

  const patch = (p: Partial<SafetyBlock>) => setDraft((d) => (d ? { ...d, ...p } : d));

  // ------------------------------------------------------------------ derived

  const domeConnected = !!dome?.connected;
  const chain = safetyChain({
    onUnsafe: server.on_unsafe,
    closeDomeOnUnsafe: !!server.close_dome_on_unsafe,
    domeConnected,
    // An older server omits the whole cooling block. Degrade to the shipped
    // default rather than claiming the ramp is off (types.ts CoolingConfig).
    warmRamp: cooling?.warm_ramp ?? true,
    hasSink: sinks.length > 0,
  });
  const dimReasons = chainReasons(chain);

  const coneDeg = draft.solar_exclusion_deg ?? 0;
  const effectiveArmed = !!draft.solar_avoidance && coneDeg > 0;
  const floorOn = (draft.min_alt_deg ?? 0) > 0;
  const ceilingOn = (draft.max_alt_deg ?? 90) < 90;
  const wedges: NoGoWedge[] = draft.nogo_box ?? [];
  const pierSide = status?.meridian?.pier_side ?? "unknown";
  const pierKnown = pierSide === "east" || pierSide === "west";
  const horizonPts = server.horizon ?? [];
  const streak = streakLine(safety?.streak ?? 0, server.unsafe_consecutive);

  const TRIP_KEYS: (keyof SafetyBlock)[] = ["unsafe_consecutive", "resume_safe_consecutive", "max_pause_min"];
  const LIMIT_KEYS: (keyof SafetyBlock)[] = ["min_alt_deg", "max_alt_deg", "nogo_box"];

  return (
    <Sheet
      data-testid="rig-safety"
      title="SAFETY MONITOR"
      icon={<NxIcon name="safety" size={18} />}
      live={<span data-testid="safety-live" data-tone={liveLine.tone}>{liveLine.text}</span>}
      onBack={() => nav.back()}
      backLabel="RIG"
      footer={<Note testid="safety-footer">{FOOTER_NOTE}</Note>}
    >
      {/* ------------------------------------------------------------ 1. inputs */}
      <div
        data-testid="safety-inputs"
        style={{
          border: `1px solid ${reading?.stale ? "var(--warn)" : "var(--line)"}`,
          borderRadius: 16, padding: "12px 14px", background: "var(--bg-panel)",
          display: "flex", flexDirection: "column", gap: 6,
        }}
      >
        <Label>INPUTS</Label>
        {reading?.stale && <Note tone="warn" testid="safety-stale">{STALE_NOTE}</Note>}
        {bars.length > 0 ? (
          bars.map((r) => <BarRow key={r.key} row={r} />)
        ) : (
          // No `detail` means no per-input readings exist. One honest row -
          // never five bars whose numbers nobody sent.
          <div data-testid="safety-status-row" style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 6 }}>
            <Mono size={12} tone={!reading ? "warn" : reading.is_safe ? "good" : "bad"}>
              {!reading ? "NO READING" : reading.is_safe ? "SAFE" : "UNSAFE"}
            </Mono>
            <Mono size={10.5} tone="dim">
              {reading
                ? `${reading.reason || "no reason given"} · ${reading.source || "unnamed monitor"}`
                : "no monitor is assigned, so there is nothing to read"}
            </Mono>
            <Mono size={10} tone="dim">
              {(() => {
                const age = readingAgeS(reading, nowMs);
                return age == null
                  ? "this monitor reports no per-input values"
                  : `last read ${age}s ago · this monitor reports no per-input values`;
              })()}
            </Mono>
          </div>
        )}
        {streak && <Mono size={10.5} tone="warn">{streak}</Mono>}
      </div>

      {/* ------------------------------------------------- 2. when a limit trips */}
      <div
        data-testid="safety-chain"
        style={{
          border: "1px solid var(--bad)", borderRadius: 16, padding: "12px 14px",
          background: "var(--bg-panel)", display: "flex", flexDirection: "column", gap: 10,
        }}
      >
        <div style={{
          fontFamily: "'Chakra Petch', sans-serif", fontWeight: 600, fontSize: 10,
          letterSpacing: ".2em", color: "var(--bad)",
        }}>WHEN A LIMIT TRIPS</div>

        <ChainRow nodes={chain} />
        <Mono size={10} tone="dim">{CHAIN_NOTE}</Mono>
        {dimReasons.length > 0 && (
          <div data-testid="safety-chain-reasons" style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {dimReasons.map((r) => <Mono key={r} size={10} tone="warn">{r}</Mono>)}
            {/* Each dim node's fix lives on a different screen, so the reason
                comes with the way to it rather than leaving the user to search. */}
            {chain.some((n) => n.id === "warm" && !n.lit) && (
              <ListRow
                icon={<NxIcon name="camera" size={16} />}
                title="WARM RAMP"
                sub="the ramp rate lives on the camera sheet, beside the cooler it describes"
                chevron
                onPress={() => nav.sheet("camera")}
                data-testid="safety-chain-warm-link"
              />
            )}
            {chain.some((n) => n.id === "notify" && !n.lit) && (
              <ListRow
                icon={<NxIcon name="info" size={16} />}
                title="ALERT CHANNELS"
                sub="ntfy, webhook, email and the dead-man's switch live on Monitor - Alerts"
                chevron
                onPress={() => nav.hub("monitor", "alerts")}
                data-testid="safety-chain-alerts-link"
              />
            )}
          </div>
        )}

        <Divider />

        <Switch
          checked={draft.sky_fallback_hold !== false}
          onChange={(v) => post("skyhold", { sky_fallback_hold: v })}
          label="HOLD ON A CLOUDY SKY"
          note={SKY_HOLD_SUB}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-sky-hold"
        />

        <Divider />

        <Label>WHEN CONDITIONS TURN UNSAFE</Label>
        <Segmented
          options={ON_UNSAFE.map((o) => ({ value: o.value, label: o.label }))}
          value={draft.on_unsafe}
          onChange={(v) => post("onunsafe", { on_unsafe: v })}
          label="When conditions turn unsafe"
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-on-unsafe"
        />
        <Mono size={10.5} tone="dim">
          {ON_UNSAFE.find((o) => o.value === draft.on_unsafe)?.blurb ?? ""}
        </Mono>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <Label>AFTER THIS MANY READINGS</Label>
          <Stepper2
            value={draft.unsafe_consecutive}
            onChange={(v) => patch({ unsafe_consecutive: v })}
            step={1} min={1} max={20}
            label="After this many readings"
            lockedReason={safetyLock.lockedReason}
            onExplain={explain}
            data-testid="safety-unsafe-consecutive"
          />
        </div>

        <Switch
          checked={draft.resume_when_safe}
          onChange={(v) => post("resume", { resume_when_safe: v })}
          label="RESUME WHEN IT CLEARS"
          note={draft.resume_when_safe
            ? "The run picks up again once readings are safe for long enough."
            : "The run stays stopped even if conditions recover. You restart it yourself."}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-resume"
        />

        {draft.resume_when_safe && (
          <>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
              <Label>SAFE READINGS NEEDED</Label>
              <Stepper2
                value={draft.resume_safe_consecutive}
                onChange={(v) => patch({ resume_safe_consecutive: v })}
                step={1} min={1} max={20}
                label="Safe readings needed"
                lockedReason={safetyLock.lockedReason}
                onExplain={explain}
                data-testid="safety-resume-consecutive"
              />
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
              <Label>GIVE UP AFTER (MIN)</Label>
              <Stepper2
                value={draft.max_pause_min}
                onChange={(v) => patch({ max_pause_min: v })}
                step={5} min={0} max={1440}
                label="Give up after minutes"
                lockedReason={safetyLock.lockedReason}
                onExplain={explain}
                data-testid="safety-max-pause"
              />
            </div>
            {draft.max_pause_min === 0 && (
              <Note tone="warn" testid="safety-pause-forever">{PAUSE_FOREVER_WARNING}</Note>
            )}
          </>
        )}

        <SaveRow
          dirty={cardDirty(TRIP_KEYS)}
          busy={busy === "trip"}
          saved={savedWhere === "trip"}
          err={err?.where === "trip" ? err.msg : null}
          onSave={() => void saveCard("trip", TRIP_KEYS)}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          unsavedNote={`Unsaved - the rig still acts after ${server.unsafe_consecutive} readings`}
          testid="safety-trip-save"
        />

        <Divider />

        <Switch
          checked={draft.enabled}
          onChange={(v) => post("enabled", { enabled: v })}
          label="SAFETY MONITORING"
          note={draft.enabled
            ? "On - a connected safety monitor can stop the run."
            : "OFF - rain and cloud stop nothing."}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-enabled"
        />
        {!draft.enabled && <Banner tone="bad" testid="safety-off-banner">{SAFETY_OFF_BANNER}</Banner>}
      </div>

      {/* --------------------------------------------------------- 3. sun cone */}
      <Card data-testid="safety-sun">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Label>SUN AVOIDANCE</Label>
          <span data-testid="safety-sun-badge">
            <Mono size={10} tone={effectiveArmed ? "good" : "bad"}>
              {effectiveArmed ? "ARMED" : "DISARMED"}
            </Mono>
          </span>
        </div>

        <Switch
          checked={!!draft.solar_avoidance}
          onChange={(v) => void onSunToggle(v)}
          label="SUN AVOIDANCE"
          note={effectiveArmed
            ? "On - the mount refuses to point within the exclusion cone of the Sun."
            : draft.solar_avoidance
              ? "Cone set to 0 degrees - protection is OFF until you set an angle above 0."
              : "OFF - SOLAR ASTRONOMY MODE. Use only with a proper solar filter installed."}
          lockedReason={solarReason}
          onExplain={explain}
          data-testid="safety-sun-switch"
        />
        {solarReason && <Note tone="warn" testid="safety-sun-lock">{SUN_DOUBLE_GATE_NOTE}</Note>}

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <Label>EXCLUSION ANGLE</Label>
          <Stepper2
            value={coneDeg}
            onChange={(v) => patch({ solar_exclusion_deg: v })}
            step={5} min={0} max={90}
            format={(v) => `${v} deg`}
            label="Exclusion angle"
            lockedReason={solarReason}
            onExplain={explain}
            data-testid="safety-cone"
          />
        </div>
        <SaveRow
          dirty={cardDirty(["solar_exclusion_deg"])}
          busy={busy === "cone"}
          saved={savedWhere === "cone"}
          err={err?.where === "cone" ? err.msg : null}
          onSave={() => void saveCard("cone", ["solar_exclusion_deg"])}
          lockedReason={solarReason}
          onExplain={explain}
          unsavedNote={`Unsaved - the mount still has ${server.solar_exclusion_deg} deg`}
          testid="safety-cone-save"
        />

        {!draft.solar_avoidance && <Banner tone="bad" testid="safety-sun-off">{DISARMED_BANNER}</Banner>}
        {draft.solar_avoidance && coneDeg <= 0 && (
          <Banner tone="bad" testid="safety-cone-inert">{CONE_INERT_BANNER}</Banner>
        )}
        {err?.where === "sun" && <Note tone="bad">{err.msg}</Note>}
      </Card>

      {/* ---------------------------------------------------- 4. pier and limits */}
      <Card data-testid="safety-limits">
        <Label>PIER AND LIMITS</Label>

        <Switch
          checked={floorOn}
          onChange={(v) => patch({ min_alt_deg: v ? 10 : 0 })}
          label="PIER FLOOR"
          note={FLOOR_SUB}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-floor-switch"
        />
        {floorOn && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <Label>FLOOR (DEG)</Label>
            <Stepper2
              value={draft.min_alt_deg}
              onChange={(v) => patch({ min_alt_deg: v })}
              step={1} min={0} max={89}
              label="Pier floor degrees"
              lockedReason={safetyLock.lockedReason}
              onExplain={explain}
              data-testid="safety-min-alt"
            />
          </div>
        )}

        <Switch
          checked={ceilingOn}
          onChange={(v) => patch({ max_alt_deg: v ? 80 : 90 })}
          label="ZENITH KEEP-OUT"
          note={CEILING_SUB}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-ceiling-switch"
        />
        {ceilingOn && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <Label>CEILING (DEG)</Label>
            <Stepper2
              value={draft.max_alt_deg ?? 90}
              onChange={(v) => patch({ max_alt_deg: v })}
              step={1} min={1} max={90}
              label="Zenith keep-out degrees"
              lockedReason={safetyLock.lockedReason}
              onExplain={explain}
              data-testid="safety-max-alt"
            />
          </div>
        )}

        {/* Obstruction wedges. Edges are hard: one degree outside the wedge the
            floor is gone, which is why each wedge shows all three numbers. */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Label>OBSTRUCTIONS</Label>
          <ActionButton
            kind="secondary"
            onPress={() => patch({ nogo_box: [...wedges, { az_min: 170, az_max: 190, alt_max: 30 }] })}
            lockedReason={safetyLock.lockedReason}
            onExplain={explain}
            data-testid="safety-wedge-add"
          >ADD</ActionButton>
        </div>
        {wedges.length === 0 ? (
          <Mono size={10.5} tone="dim">None - only the altitude floor and the site horizon apply.</Mono>
        ) : (
          <>
            {wedges.map((w, i) => {
              const setW = (p: Partial<NoGoWedge>) =>
                patch({ nogo_box: wedges.map((b, j) => (j === i ? { ...b, ...p } : b)) });
              return (
                <div key={i} data-testid={`safety-wedge-${i}`}
                  style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <Stepper2 value={w.az_min} onChange={(v) => setW({ az_min: v })} step={5} min={0} max={360}
                    format={(v) => `az ${v}`} label={`Obstruction ${i + 1} azimuth from`}
                    lockedReason={safetyLock.lockedReason} onExplain={explain} />
                  <Stepper2 value={w.az_max} onChange={(v) => setW({ az_max: v })} step={5} min={0} max={360}
                    format={(v) => `to ${v}`} label={`Obstruction ${i + 1} azimuth to`}
                    lockedReason={safetyLock.lockedReason} onExplain={explain} />
                  <Stepper2 value={w.alt_max} onChange={(v) => setW({ alt_max: v })} step={1} min={0} max={89}
                    format={(v) => `alt ${v}`} label={`Obstruction ${i + 1} minimum altitude`}
                    lockedReason={safetyLock.lockedReason} onExplain={explain} />
                  <ActionButton kind="ghost"
                    onPress={() => patch({ nogo_box: wedges.filter((_, j) => j !== i) })}
                    lockedReason={safetyLock.lockedReason} onExplain={explain}
                    ariaLabel={`Remove obstruction ${i + 1}`}
                  >REMOVE</ActionButton>
                </div>
              );
            })}
            <Mono size={10} tone="dim">{NOGO_NOTE}</Mono>
          </>
        )}

        <SaveRow
          dirty={cardDirty(LIMIT_KEYS)}
          busy={busy === "limits"}
          saved={savedWhere === "limits"}
          err={err?.where === "limits" ? err.msg : null}
          onSave={() => void saveCard("limits", LIMIT_KEYS)}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          unsavedNote={`Unsaved - the mount still stops below ${server.min_alt_deg} deg`}
          testid="safety-limits-save"
        />

        <Divider />

        <Switch
          checked={draft.enforce_pier_limits}
          onChange={(v) => post("pier", { enforce_pier_limits: v })}
          label="ENFORCE MOUNT PIER LIMITS"
          note="Honour the pier-side limits the mount itself reports."
          lockedReason={safetyLock.lockedReason ?? (pierKnown ? null : NO_PIER_SIDE_REASON)}
          onExplain={explain}
          data-testid="safety-pier-limits"
        />
        <Switch
          checked={draft.poll_each_frame}
          onChange={(v) => post("poll", { poll_each_frame: v })}
          label="CHECK BEFORE EVERY FRAME"
          note={draft.poll_each_frame
            ? "The monitor is read before each exposure starts."
            : "OFF - an unsafe condition can be up to one exposure late."}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-poll-each-frame"
        />

        <Divider />

        <Label>NIGHT STARTS AT</Label>
        <Segmented
          options={TWILIGHT.map((t) => ({ value: t.value, label: t.label, sub: t.sub }))}
          value={draft.twilight_deg}
          onChange={(v) => post("twilight", { twilight_deg: v })}
          label="Night starts at"
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          data-testid="safety-twilight"
        />

        <ListRow
          icon={<NxIcon name="horizon" size={16} />}
          title="HORIZON"
          sub={horizonPts.length
            ? `${horizonPts.length} points · up to ${Math.max(...horizonPts.map((p) => p[1]))} deg`
            : "open - nothing drawn"}
          chevron
          onPress={() => nav.sheet("horizon")}
          data-testid="safety-horizon"
        />
      </Card>

      {/* --------------------------------------------------- 5. meridian flip */}
      <Card data-testid="safety-meridian">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <Label>WARN N MIN BEFORE THE FLIP</Label>
          <Stepper2
            value={draft.meridian_flip_warn_min ?? 15}
            onChange={(v) => patch({ meridian_flip_warn_min: v })}
            step={5} min={0} max={240}
            format={(v) => `${v} min`}
            label="Warn minutes before the flip"
            lockedReason={safetyLock.lockedReason}
            onExplain={explain}
            data-testid="safety-flip-warn"
          />
        </div>
        <Mono size={10.5} tone="dim">{MERIDIAN_SUB}</Mono>
        <span data-testid="safety-flip-state">
          <Mono size={10.5} tone="dim">{meridianLine()}</Mono>
        </span>
        <SaveRow
          dirty={cardDirty(["meridian_flip_warn_min"])}
          busy={busy === "flip"}
          saved={savedWhere === "flip"}
          err={err?.where === "flip" ? err.msg : null}
          onSave={() => void saveCard("flip", ["meridian_flip_warn_min"])}
          lockedReason={safetyLock.lockedReason}
          onExplain={explain}
          unsavedNote={`Unsaved - the rig still warns ${server.meridian_flip_warn_min ?? 15} min ahead`}
          testid="safety-flip-save"
        />
      </Card>

      {/* ------------------------------------------------------------ 6. preset */}
      <ListRow
        icon={<NxIcon name="gauge" size={16} />}
        title={`PRESET · ${String(server.preset).toUpperCase()}`}
        sub={`${PRESET_BLURB[server.preset] ?? PRESET_BLURB.custom} Picking a different preset is on the tablet.`}
        chevron
        onPress={() => nav.sheet("safetyTuning")}
        data-testid="safety-preset"
      />

      {/* ------------------------------------------------- 7. escalation policy */}
      <Card data-testid="safety-escalation">
        <Label>ESCALATION POLICY</Label>
        {bp === "phone" ? (
          <>
            {escalationRows(escalation).map((r) => (
              <ListRow key={r} title={r} data-testid="safety-escalation-row" />
            ))}
            <Mono size={10} tone="dim">{WATCHDOG_HINT}</Mono>
            <ListRow
              icon={<NxIcon name="settings" size={16} />}
              title="EDIT ON A TABLET"
              sub="Recovery policy is edited on a wider screen."
              chevron
              onPress={() => nav.sheet("safetyTuning")}
              lockedReason={alertsLock.lockedReason}
              onExplain={alertsLock.onExplain}
              data-testid="safety-escalation-edit"
            />
          </>
        ) : (
          // THE SCROLL BOX THAT USED TO BE HERE IS GONE (wave R7, T-R7-9).
          //
          // `EscalationPanel` was a settings-page panel mounted in a 360 px
          // sheet: its rows were Tailwind grids (`sm:grid-cols-[1fr_13rem]`,
          // `sm:grid-cols-2`) and `sm:` asks the VIEWPORT, not the container, so
          // from 640 px up it laid out a fixed 13 rem column inside
          // `.nx-sheet-panel` (`min(420px, 44vw)` - 360.8 px at an 820 px
          // viewport). Every ancestor is `overflow: visible`, so that overflow
          // reached the page: the browser probe measured 100 px of horizontal
          // PAGE scroll at 820 and none at 390, which was the tell, since 390 is
          // the narrower sheet and simply does not render this branch. The panel
          // was shared, so its grids were not this sheet's to change, and a
          // scroll container was the only boundary available.
          //
          // `EscalationEditor` is a single column with no fixed track, so there
          // is nothing left to contain.
          <EscalationEditor />
        )}
      </Card>

      {/* ---------------------------------------------------------- 8. dead-man */}
      <Card data-testid="safety-deadman">
        <Label>WHO GETS TOLD</Label>
        <ListRow
          icon={<NxIcon name="clock" size={16} />}
          title="DEAD-MAN SWITCH"
          sub={deadmanLine(health)}
          right={<Mono size={10} tone={deadmanTone(health)}>{deadmanWord(health)}</Mono>}
          chevron
          onPress={() => nav.hub("monitor", "alerts")}
          data-testid="safety-deadman-row"
        />
        <ListRow
          icon={<NxIcon name="info" size={16} />}
          title="ALERT CHANNELS"
          sub={sinks.length
            ? `${sinks.length} configured · ${sinks.filter((s) => s.enabled).length} enabled`
            : "none configured"}
          chevron
          onPress={() => nav.hub("monitor", "alerts")}
          data-testid="safety-sinks-row"
        />
        {sinks.length === 0 && <Banner tone="warn" testid="safety-no-sinks">{EMPTY_SINKS_WARNING}</Banner>}
      </Card>

      {/* ------------------------------------------------------- 9. roof / dome */}
      {domeConnected && (
        <Card data-testid="safety-roof">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <Label>ROOF</Label>
            <span data-testid="safety-roof-status">
              <Mono size={10.5}>{domeStatusLabel(dome?.shutter ?? "unknown")}</Mono>
            </span>
          </div>
          <Switch
            checked={draft.close_dome_on_unsafe}
            onChange={(v) => post("roof", { close_dome_on_unsafe: v })}
            label="CLOSE ROOF ON UNSAFE"
            note="A rain or cloud trip closes the roof over the parked gear."
            lockedReason={safetyLock.lockedReason}
            onExplain={explain}
            data-testid="safety-close-on-unsafe"
          />
          <Switch
            checked={draft.reopen_dome_when_safe}
            onChange={(v) => post("roof", { reopen_dome_when_safe: v })}
            label="REOPEN ROOF WHEN SAFE AGAIN"
            note="Instead of ending the night, wait for safe-again, reopen and resume."
            lockedReason={safetyLock.lockedReason
              ?? (draft.close_dome_on_unsafe ? null : REOPEN_NEEDS_CLOSE)}
            onExplain={explain}
            data-testid="safety-reopen"
          />
          <Switch
            checked={draft.close_dome_when_done}
            onChange={(v) => post("roof", { close_dome_when_done: v })}
            label="CLOSE ROOF AT END OF NIGHT"
            note="Close the roof at a normal end-of-night. Needs park-on-finish."
            lockedReason={safetyLock.lockedReason}
            onExplain={explain}
            data-testid="safety-close-when-done"
          />
          <ActionButton
            kind="danger"
            onPress={() => void onCloseRoof()}
            busy={ourClose}
            // A DIFFERENT capability from the three flags above on purpose: the
            // close MOVES THE MOUNT, so it is control.mount - which an operator
            // holds and no config.* cap is. Folding them together used to hide
            // the app's only manual roof close from the person standing at the rig.
            lockedReason={ourClose ? "Your close is already running - it parks the mount first" : roofCloseLock.lockedReason}
            onExplain={roofCloseLock.onExplain}
            data-testid="safety-close-now"
          >CLOSE ROOF NOW</ActionButton>
        </Card>
      )}

      {/* ------------------------------------------------------ 10. simulator */}
      {status?.mode === "sim" && (
        <Card data-testid="safety-simulate">
          <Label>SIMULATE</Label>
          <Mono size={10.5} tone="dim">
            The only way to walk the chain above end to end without waiting for weather.
          </Mono>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <ActionButton
              kind="danger"
              onPress={() => void simulate(true)}
              lockedReason={safetyLock.lockedReason}
              onExplain={explain}
              data-testid="safety-sim-unsafe"
            >SIMULATE UNSAFE</ActionButton>
            <ActionButton
              kind="secondary"
              onPress={() => void simulate(false)}
              lockedReason={safetyLock.lockedReason}
              onExplain={explain}
              data-testid="safety-sim-safe"
            >SIMULATE SAFE</ActionButton>
          </div>
        </Card>
      )}
    </Sheet>
  );

  // ------------------------------------------------------------- handlers

  /** Disarming is the DANGER path: a hold-confirm that names the consequence
   *  BEFORE anything is posted. Re-arming is always safe. Either way a refused
   *  write snaps the switch back to what the server actually has - an interlock
   *  must fail visibly closed. */
  async function onSunToggle(wantOn: boolean): Promise<void> {
    // Hoisted, so the early return above has not narrowed `server` for the
    // compiler; re-state it rather than assert past it.
    if (busy || !server) return;
    if (!wantOn) {
      const ok = await confirmDialog({
        title: "Disable sun avoidance?",
        body: DISARM_BODY,
        tone: "danger",
        mode: "hold",
        confirmLabel: "Disable sun avoidance",
        cancelLabel: "Keep protection on",
      });
      if (!ok) return;
    }
    patch({ solar_avoidance: wantOn });
    const took = await post("sun", { solar_avoidance: wantOn, solar_exclusion_deg: coneDeg });
    if (!took) patch({ solar_avoidance: server.solar_avoidance });
  }

  async function onCloseRoof(): Promise<void> {
    if (roofCloseLock.lockedReason || ourClose) return;
    if (gotoBusy) {
      // The protective action must not wait on the hazard: rain does not wait
      // for a slew. So it stays reachable, behind a hold that names the cost.
      const ok = await confirmDialog({
        title: "Stop the slew and close the roof?",
        body: "The mount is moving right now. Closing the roof cancels that move, parks the "
          + "mount and then shuts the shutter - whatever it was slewing to is abandoned and "
          + "has to be started again.",
        tone: "warn",
        mode: "hold",
        confirmLabel: "Stop the mount and close",
        cancelLabel: "Leave the mount moving",
      });
      if (!ok) return;
    }
    setClosePhase("sending");
    try {
      await closeDome();
      armRoofClose();
      setClosePhase("closing");
      useStore.getState().enqueueToast({ level: "info", title: "Closing the roof - parking the mount first." });
    } catch (e) {
      setClosePhase("idle");
      useStore.getState().enqueueToast({
        level: "error",
        title: e instanceof ApiError ? e.message || "Could not close the roof." : "Could not close the roof.",
      });
    }
  }

  async function simulate(unsafe: boolean): Promise<void> {
    try {
      await api.post("/api/safety/simulate", { unsafe, reason: unsafe ? "simulated trip" : "" });
    } catch (e) {
      useStore.getState().enqueueToast({
        level: "error",
        title: e instanceof ApiError ? e.message || "The rig refused the simulation." : "The rig refused the simulation.",
      });
    }
  }

  function meridianLine(): string {
    const m = status?.meridian;
    // The cap comes first: the server nulls `hours_to_flip` and collapses
    // `status` to "unknown" for a principal without `view.site_derived`,
    // because the countdown inverts to the rig's longitude. Printing the
    // no-mount-data sentence over that blames the hardware for a redaction.
    // One sentence, shared with the Monitor flip tile and the NOW band.
    if (!canSiteDerived) return FLIP_SITE_REASON;
    if (!m || m.status === "unknown") return "no mount data - the flip clock is not running";
    const side = m.pier_side === "unknown" ? "pier side unknown" : `pier ${m.pier_side}`;
    if (m.status === "counting" && m.hours_to_flip != null) {
      const h = Math.floor(m.hours_to_flip);
      const min = Math.round((m.hours_to_flip - h) * 60);
      return `${side} · flip in ${h}h ${min}m`;
    }
    if (m.status === "due") return `${side} · flip is due now`;
    if (m.status === "flip_disabled") return `${side} · the plan has the flip switched off`;
    return `${side} · no flip tonight`;
  }
}

// ---------------------------------------------------------------- read-only rows

/** The escalation block in human wording, one line per field. Read-only on the
 *  phone: the cap is `config.alerts` (the server files recovery/notification
 *  policy with the alerts caps, not safety - app.py:3175-3198) and the editors
 *  are the real `EscalationPanel` at tablet width. */
export function escalationRows(esc: EscalationConfig | null): string[] {
  if (!esc) return ["No escalation policy has arrived from the rig yet."];
  const act = (a: string) => ACTION_LABEL[a] ?? a;
  const rows: string[] = [
    esc.require_cooling
      ? `Camera must be at temperature before a run - ${act(esc.cooling_action)}`
      : "Start without waiting for temperature",
    esc.require_guiding
      ? `Guiding must be running before a run - ${act(esc.guiding_action)}`
      : "Start without guiding",
    `Autofocus failed - ${act(esc.af_failure_action)}`,
  ];
  const factor = esc.hfr_reject_factor ?? 0;
  rows.push(factor > 0
    ? `A frame worse than ${factor}x the reference - ${HFR_LABEL[esc.hfr_reject_action] ?? esc.hfr_reject_action}`
    : "No frame-quality gate");
  if (esc.hfr_reject_action === "retake") {
    rows.push(`At most ${esc.hfr_retake_limit_per_target} replacement frames per target`);
  }
  rows.push(esc.no_progress_watchdog_s > 0
    ? `Treat the run as unsafe if no frame lands for ${Math.round(esc.no_progress_watchdog_s / 60)} min`
    : "No no-progress watchdog");
  rows.push(esc.reconnect_resume
    ? `Reconnect and resume after a dropout - ${esc.reconnect_retries} attempts`
    : "Do not auto-reconnect");
  // An absent monitor is the one gap the engine can be told to treat as a trip
  // (server sequence/engine.py `_no_safety_source`). OFF is not silence: the run
  // proceeds and the gap is logged once at warning level, so say which of the two
  // this rig does rather than only printing the row when it is on.
  rows.push(esc.require_safety_monitor
    ? "A safety monitor is required before a run"
    : "A run may start with no safety monitor - the gap is logged, not enforced");
  return rows;
}

function deadmanWord(h: AlertHealth | null): string {
  const dm = h?.deadman;
  if (!dm?.configured) return "NOT SET";
  return dm.healthy ? "PINGING" : "NOT PINGING";
}

function deadmanTone(h: AlertHealth | null): "good" | "bad" | "dim" {
  const dm = h?.deadman;
  if (!dm?.configured) return "dim";
  return dm.healthy ? "good" : "bad";
}

function deadmanLine(h: AlertHealth | null): string {
  const dm = h?.deadman;
  if (!dm?.configured) return "not configured - nothing outside this rig is checking it is alive";
  if (!dm.healthy) return "configured · NOT PINGING - the monitor URL is not being reached";
  return dm.last_ping_age_s == null
    ? "configured · healthy"
    : `configured · healthy · last ping ${Math.round(dm.last_ping_age_s)}s ago`;
}

// Re-exported so the DOM test can assert against the same string the sheet
// prints rather than one it typed itself.
export { NO_LIMIT_SUB, SUN_DOUBLE_GATE_NOTE, EMPTY_SINKS_WARNING, FOOTER_NOTE };
