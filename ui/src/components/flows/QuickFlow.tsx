// QuickFlow.tsx -- the one-screen night: pick a target, say how many subs, tick
// the filters, go.
//
// WHY IT EXISTS ALONGSIDE THE WIZARD. The guided sheet asks "what kind of
// night", then hands over a canvas. That is the right shape for a flow somebody
// intends to shape. It is the wrong shape for the case that comes up every
// clear evening -- this object, this many subs, these filters -- where the canvas
// is a step between the operator and the sky.
//
// THE GRAPH IS STILL THE SERVER'S. `server/astrodeck/flows/wizard.py`'s `quick`
// wraps the same `generate()` the wizard uses; this sheet sends four answers and
// never a graph. wizard.py's header names two implementations of one rule set as
// the drift this project keeps re-finding, and a "quick" generator written in
// TypeScript would be the third.
//
// THE FILTER ROWS ARE THE RIG'S, never typed. cyclePlanRows.ts opens on why: a
// filter name is not a label -- it lands in the FITS `FILTER` header, in the
// filename and in the calibration matcher's key -- so a row that cannot be typed
// cannot name a slot the wheel does not have. `resolveWheel` is shared with the
// FILTER CYCLE inspector so both offer exactly the same slots.
import { useMemo, useState, type CSSProperties } from "react";
import { Overlay } from "../Overlay";
import { HonestButton, LockedNote } from "../ui";
import { CatalogSearch } from "../atlas/CatalogSearch";
import { accessPhrase, useCanControlCapture, useCanControlMount } from "../../lib/caps";
import { apiErrorPayload } from "../../lib/apiError";
import { flowsApi, type QuickFlowAnswers } from "../../lib/flowsApi";
import { useStore } from "../../store";
import type { CatalogEntry } from "../../types";
import { defaultExposureFor, resolveWheel } from "./cyclePlanRows";

/** One exported string per failure, so a button's reason and any test asserting
 *  on it cannot drift apart. */
export const QUICK_SAVE_FAILED = "Could not create the quick flow";
export const QUICK_RUN_FAILED = "The flow was saved, but it did not start";

/** What the sheet calls the single channel of a rig with no filter wheel. It is
 *  a LABEL, not a slot: the payload's `filters` is empty in that case and the
 *  server builds a capture loop with no filter name, because an invented name is
 *  what would land in the FITS header. */
export const OSC_LABEL = "OSC";

/** Subs per filter, before anyone touches it. Ten of each is a night that
 *  produces something at every prefix and finishes inside a few hours at
 *  ordinary sub lengths; it is a starting point, not a recommendation. */
export const DEFAULT_SUBS = 10;

/** RA in hours -> `20h 34m 52s`, the format the TARGET node stores.
 *
 *  Sexagesimal rather than the decimal hours the catalog row carries, because
 *  the string ends up in a node field an operator reads and edits, and
 *  `20.581111` is not a coordinate anybody checks at a glance. The server's
 *  `parse_ra` accepts both; this is the one it renders back. */
export function raHms(hours: number): string {
  const wrapped = ((hours % 24) + 24) % 24;
  let h = Math.floor(wrapped);
  let m = Math.floor((wrapped - h) * 60);
  let s = Math.round(((wrapped - h) * 60 - m) * 60);
  if (s === 60) { s = 0; m += 1; }
  if (m === 60) { m = 0; h = (h + 1) % 24; }
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(h)}h ${p(m)}m ${p(s)}s`;
}

/** Dec in degrees -> `+60° 09′ 14″`, the format the TARGET node stores.
 *
 *  THE SIGN IS ALWAYS WRITTEN. A dec is the one coordinate that carries one,
 *  and `41° 16′ 09″` read back as +41 by luck rather than by statement. */
export function decDms(deg: number): string {
  const sign = deg < 0 ? "-" : "+";
  const abs = Math.abs(deg);
  let d = Math.floor(abs);
  let m = Math.floor((abs - d) * 60);
  let s = Math.round(((abs - d) * 60 - m) * 60);
  if (s === 60) { s = 0; m += 1; }
  if (m === 60) { m = 0; d += 1; }
  const p = (n: number) => String(n).padStart(2, "0");
  return `${sign}${p(d)}° ${p(m)}′ ${p(s)}″`;
}

export interface QuickTarget { name: string; ra: string; dec: string }

/** A catalog row as the TARGET node holds it.
 *
 *  BOTH COORDINATES TRAVEL. `to_plan` reads ra/dec and never the name, so a
 *  quick flow carrying a name and no coordinates would slew to the TARGET node's
 *  shipped M31 and file the frames under the object that was picked. */
export function targetFromEntry(e: CatalogEntry): QuickTarget {
  return { name: e.name || e.id, ra: raHms(e.ra_hours), dec: decDms(e.dec_deg) };
}

/** The exact body `POST /api/flows/quick` receives.
 *
 *  Pure and exported so the payload can be graded without a DOM: the failure
 *  worth guarding against here is a handler that posts DEFAULTS -- ten subs, all
 *  filters, guided -- which returns a perfectly valid 200 and the wrong night. */
export function quickPayload(a: {
  target: QuickTarget;
  subs: number;
  /** Ticked slot names. Empty in the one-channel case, and empty is an ANSWER
   *  there, not a missing one. */
  filters: readonly string[];
  /** Keyed by slot name, or by OSC_LABEL when there is no wheel. */
  exposures: Readonly<Record<string, number>>;
  guided: boolean;
  run: boolean;
}): QuickFlowAnswers {
  const keys = a.filters.length > 0 ? a.filters : [OSC_LABEL];
  const exposures: Record<string, number> = {};
  for (const k of keys) {
    const v = a.exposures[k];
    if (Number.isFinite(v) && v > 0) exposures[k] = v;
  }
  return {
    target: a.target,
    subs: a.subs,
    filters: [...a.filters],
    exposures,
    guided: a.guided,
    run: a.run,
  };
}

const LABEL = "font-display font-semibold text-[9.5px] tracking-[0.18em] text-dim";

export default function QuickFlow() {
  const open = useStore((s) => s.flows.ui.quickOpen);
  const setUi = useStore((s) => s.flowsSetUi);
  const loadLibrary = useStore((s) => s.flowsLoadLibrary);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const pushConfirm = useStore((s) => s.pushConfirm);
  // Narrow selectors, one field each -- the sheet must not re-render on every
  // status frame the rig publishes.
  const wheelNames = useStore((s) => s.status?.filterwheel?.names);
  const wheelOpaque = useStore((s) => s.status?.filterwheel?.opaque);
  const canSave = useCanControlCapture();
  const canRun = useCanControlMount();

  const { filters: wheel, fromRig } = resolveWheel(wheelNames, wheelOpaque);
  // NO WHEEL IS ONE CHANNEL. `fromRig` is false both when nothing is attached
  // and when the attached wheel reports only unnamed slots, and both answers are
  // the same here: this sheet cannot name this rig's filters, so it does not
  // pretend to. The FILTER CYCLE inspector's assumed seven are a PLANNING
  // fallback on a canvas the operator can then edit; a sheet whose next button
  // says "save and run" must describe the rig in front of it.
  const oneChannel = !fromRig;
  const slots = oneChannel ? [OSC_LABEL] : wheel;

  const [target, setTarget] = useState<QuickTarget | null>(null);
  const [subsText, setSubsText] = useState(String(DEFAULT_SUBS));
  const [ticked, setTicked] = useState<readonly string[] | null>(null);
  const [exposures, setExposures] = useState<Record<string, number>>({});
  const [guided, setGuided] = useState(true);
  const [busy, setBusy] = useState(false);

  // The slots start ticked, so the common case is "pick a target and press
  // Save". `null` means "the operator has not touched this yet", which is what
  // lets the default follow a wheel that connects while the sheet is open
  // instead of freezing whatever was on screen at mount.
  const chosen = ticked ?? (oneChannel ? [] : wheel);
  const expFor = (f: string) => exposures[f] ?? defaultExposureFor(f);

  const subs = Math.floor(Number(subsText));
  const subsOk = Number.isFinite(subs) && subs >= 1;
  const frames = subsOk ? subs * (chosen.length || 1) : 0;

  const close = () => setUi({ quickOpen: false });
  const explain = (reason: string, why?: string) =>
    enqueueToast({ level: "warning", title: reason, detail: why });

  const saveReason = !canSave
    ? `Creating a flow needs ${accessPhrase("control.capture")}.`
    : busy ? "Already creating the flow. One moment."
    : !target ? "Pick a target first: search for it by name or catalogue id."
    : !subsOk ? "Set how many subs of each filter to take (at least 1)."
    : (!oneChannel && chosen.length === 0) ? "Tick at least one filter."
    : null;
  const runReason = saveReason
    ?? (!canRun ? `Starting a run needs ${accessPhrase("control.mount")}.` : null);

  const payload = useMemo(() => (target && subsOk
    ? quickPayload({ target, subs, filters: chosen,
                     exposures: Object.fromEntries(
                       slots.map((f) => [f, expFor(f)])),
                     guided, run: false })
    : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [target, subs, subsOk, chosen, exposures, guided, slots.join("|")]);

  const submit = async (run: boolean) => {
    if (!payload) return;
    setBusy(true);
    try {
      const res = await flowsApi.quick({ ...payload, run });
      const id = res?.flow?.id;
      if (!id) throw new Error("the server returned a flow with no id");
      close();
      // The library reloads under the operator and this flow lands somewhere in
      // a folder that may already hold thirty, so the card says which is theirs.
      setUi({ highlightId: id });
      await loadLibrary();
      enqueueToast({
        level: "info",
        title: run ? `Started ${res.flow.name}` : `Saved ${res.flow.name}`,
        detail: run
          ? `${res.run?.frames ?? frames} frames on the engine.`
          : "It is in My flows, ready to open or run.",
      });
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      // THE SAVE AND THE RUN ARE TWO OUTCOMES OF ONE REQUEST. The server saves
      // first and, when the run is then refused, puts `saved: true` and the id
      // in the refusal's own payload -- so a failure to START must not be
      // reported as a failure to save, and the flow it did save must still
      // reach the library.
      //
      // Read off the decoded BODY, not off the message. FastAPI nests the
      // payload under `detail` and `parseApiError` renders only the human
      // sentence out of it, so a message-substring test would be true or false
      // depending on whether the refusal happened to be a dict or a string --
      // and "already running" is a string.
      const payload = apiErrorPayload((e as { body?: unknown }).body);
      const saved = payload?.saved === true && typeof payload?.flow_id === "string";
      if (run && saved) {
        setUi({ highlightId: payload!.flow_id as string });
        close();
        await loadLibrary();
      }
      enqueueToast({
        level: "error",
        title: run && saved ? QUICK_RUN_FAILED : QUICK_SAVE_FAILED,
        detail,
      });
    } finally {
      setBusy(false);
    }
  };

  /** SAVE AND RUN asks first, and the question carries the numbers.
   *
   *  This is the only control in the app that can open the shutter from a
   *  library screen, and the sheet it sits on was designed to be fast. "Are you
   *  sure" alone would be a speed bump; the frame count, the filters and whether
   *  the guider is in the loop are what an operator actually checks before
   *  walking away from the rig. */
  const saveAndRun = async () => {
    const ok = await pushConfirm({
      title: `Start ${frames} frames on ${target?.name ?? "this target"} now?`,
      body: (
        <ul className="flex flex-col gap-1.5 text-[12px] text-dim">
          <li>{chosen.length > 0
            ? `${subs} subs each of ${chosen.join(", ")}`
            : `${subs} subs, one channel`}</li>
          <li>{guided ? "Guided" : "Unguided: no GUIDE stage in the flow"}</li>
          <li>The night starts at dusk and ends parked.</li>
        </ul>
      ),
      confirmLabel: "SAVE AND RUN",
      cancelLabel: "CANCEL",
      tone: "warn",
      mode: "confirm",
      confirmPrimary: true,
    });
    if (ok) await submit(true);
  };

  return (
    <Overlay
      open={open}
      variant="center"
      label="Quick flow"
      onClose={close}
      surfaceStyle={{ "--ov-max-h": "90dvh" } as CSSProperties}
      head={(
        <header
          data-flows-quick
          className="flex items-center justify-between gap-3 px-[18px] py-[13px]"
        >
          <span className="font-display font-semibold text-[11px] tracking-[0.22em]">
            QUICK FLOW
          </span>
          <button
            type="button"
            aria-label="Close the quick flow sheet"
            onClick={close}
            className="flex-none min-w-[40px] min-h-[34px] rounded-lg border border-line2
              bg-transparent text-dim hover:text-accent hover:border-accent
              transition-colors cursor-pointer"
          >
            <span aria-hidden>✕</span>
          </button>
        </header>
      )}
      foot={(
        <footer className="flex flex-wrap gap-2.5 px-[18px] py-[13px]">
          <HonestButton
            reason={saveReason}
            onClick={() => { void submit(false); }}
            onExplain={(r) => explain(r)}
            className="flex-1 min-w-[110px] min-h-[46px] rounded-[10px] border border-line2
              bg-transparent text-ink font-display font-semibold text-[12px]
              tracking-[0.14em] cursor-pointer hover:border-accent"
          >
            {busy ? "SAVING…" : "SAVE"}
          </HonestButton>
          <HonestButton
            reason={runReason}
            onClick={() => { void saveAndRun(); }}
            onExplain={(r) => explain(r)}
            className="flex-1 min-w-[140px] min-h-[46px] rounded-[10px] border border-accent2
              bg-accent-fill text-accent font-display font-semibold text-[12px]
              tracking-[0.14em] cursor-pointer"
          >
            SAVE AND RUN
          </HonestButton>
        </footer>
      )}
      bodyClassName="px-[18px] py-4 flex flex-col gap-4"
    >
      {/* ── 1. the target */}
      <div className="flex flex-col gap-1.5">
        <span className={LABEL}>TARGET</span>
        {/* No `placeholder` override on the search below: CatalogSearch's own
            default carries the "M 31" example, and its header calls that example
            load-bearing (whatever it shows, a beginner types, and "M 31" only
            started finding M31 when the server learned to squash designations).
            A second copy here is a second place for it to go stale. */}
        {target ? (
          <div className="flex items-center gap-2 flex-wrap" data-quick-target>
            <span className="font-display font-semibold text-[12.5px] tracking-[0.08em]
                             uppercase">{target.name}</span>
            <span className="font-mono text-[10.5px] text-faint">
              {target.ra} · {target.dec}
            </span>
            <button
              type="button"
              onClick={() => setTarget(null)}
              className="ml-auto min-h-[34px] px-2.5 rounded-lg border border-line2
                         bg-transparent text-dim font-mono text-[10.5px]
                         cursor-pointer hover:border-accent hover:text-accent"
            >
              CHANGE
            </button>
          </div>
        ) : (
          <CatalogSearch
            className="w-full"
            onPick={(e) => {
              setTarget(targetFromEntry(e));
            }}
          />
        )}
      </div>

      {/* ── 2. how many */}
      <label className="flex items-center gap-3">
        <span className={`${LABEL} flex-1`}>
          {oneChannel ? "SUBS" : "SUBS PER FILTER"}
        </span>
        {/* THE WIDTH GOES ON THE WRAPPER. `.field` sets `width: 100%` in
            unlayered CSS, which beats any Tailwind width utility on the input
            itself -- measured: a `w-[86px]` input rendered 330px wide and ate the
            row. CatalogSearch carries the same note for the same reason. The
            font and padding DO need `!` for the same cascade. */}
        <span className="flex-none w-[86px]">
          <input
            value={subsText}
            onChange={(e) => setSubsText(e.target.value)}
            inputMode="numeric"
            aria-label={oneChannel ? "Subs" : "Subs per filter"}
            className="field !text-[13px] !py-2 !px-2.5 min-h-[44px] text-right"
          />
        </span>
      </label>

      {/* ── 3. the filters, read off the wheel */}
      <div className="flex flex-col gap-1.5">
        <span className={LABEL}>{oneChannel ? "CHANNEL" : "FILTERS"}</span>
        {oneChannel ? (
          <div className="flex items-center gap-2" data-quick-osc>
            <span className="font-mono text-[11px] flex-1">{OSC_LABEL}</span>
            <ExposureBox
              filter={OSC_LABEL}
              value={expFor(OSC_LABEL)}
              onChange={(n) => setExposures((p) => ({ ...p, [OSC_LABEL]: n }))}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {wheel.map((f) => {
              const on = chosen.includes(f);
              return (
                <div key={f} className="flex items-center gap-2 min-w-0">
                  <label className="flex items-center gap-2 min-w-0 flex-1 cursor-pointer">
                    <input
                      type="checkbox"
                      className="flex-none accent-[var(--accent)] w-[15px] h-[15px]"
                      checked={on}
                      aria-label={`${f} in the cycle`}
                      onChange={() => setTicked(
                        on ? chosen.filter((x) => x !== f)
                           // Wheel order, not click order: the server sorts it
                           // anyway, and a list that reordered under the
                           // operator would read as a bug.
                           : wheel.filter((x) => x === f || chosen.includes(x)))}
                    />
                    <span className="font-mono text-[11px] truncate">{f}</span>
                  </label>
                  {/* No box on an unticked row. An exposure for a filter nobody
                      selected is a number with nothing to spend it on, and it
                      reads as though the filter were in the night. */}
                  {on && (
                    <ExposureBox
                      filter={f}
                      value={expFor(f)}
                      onChange={(n) => setExposures((p) => ({ ...p, [f]: n }))}
                    />
                  )}
                </div>
              );
            })}
          </div>
        )}
        <p className="text-[10.5px] text-faint leading-[1.5]">
          {oneChannel
            ? "No filter wheel is reporting named slots, so this is one channel and the frames carry no filter name."
            : "Rows are this rig's wheel. A filter it does not have cannot be typed here."}
        </p>
      </div>

      {/* ── 4. guiding */}
      <button
        type="button"
        aria-pressed={guided}
        onClick={() => setGuided((g) => !g)}
        className={`flex items-center justify-between gap-3 min-h-[44px] px-3
          rounded-[10px] border font-mono text-[11px] cursor-pointer
          transition-colors ${guided
            ? "border-accent text-accent bg-accent-fill"
            : "border-line2 text-dim bg-transparent"}`}
      >
        <span>Guide</span>
        <span className="font-display font-semibold text-[10px] tracking-[0.16em]">
          {guided ? "ON" : "OFF"}
        </span>
      </button>

      {/* The night this will produce, in the numbers the operator gave. */}
      <p className="text-[11px] text-dim leading-[1.5] [text-wrap:pretty]"
         data-quick-summary>
        {subsOk && (oneChannel || chosen.length > 0)
          ? `${frames} frames: dusk window, slew and center, autofocus, `
            + `${guided ? "guiding, " : ""}`
            + `${oneChannel ? "capture" : "filter cycle"}, session report. `
            + "A refocus fires if HFR drifts, and an unsafe reading aborts and parks."
          : "Pick a target, set the subs and tick at least one filter."}
      </p>

      {!canSave && (
        <LockedNote
          reason={`Creating a flow needs ${accessPhrase("control.capture")}.`}
          className="!items-start leading-[1.5] [text-wrap:pretty]"
        />
      )}
    </Overlay>
  );
}

function ExposureBox({ filter, value, onChange }: {
  filter: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <span className="flex items-center gap-1 flex-none">
      {/* Width on the wrapper, not the input -- `.field`'s unlayered
          `width: 100%` beats a `w-*` utility on the input itself. */}
      <span className="block w-[58px]">
      <input
        type="text"
        inputMode="numeric"
        className="field !text-[11px] !py-[7px] !px-[7px] text-right"
        value={String(value)}
        aria-label={`${filter} exposure, seconds`}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          // Unparseable input leaves the stored value alone rather than writing
          // 0. The operator is mid-keystroke clearing "60" to type "180", and a
          // zero-second slot is a run-time refusal on a flow that looks fine.
          if (Number.isFinite(n) && n > 0) onChange(n);
        }}
      />
      </span>
      <span className="font-mono text-[10px] text-faint">s</span>
    </span>
  );
}
