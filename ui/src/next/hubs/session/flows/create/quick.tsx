// quick.tsx - QUICK FLOW: one target, this many subs, these filters, go
// (wave R7 row A17; rebuild of `components/flows/QuickFlow.tsx` in the design's
// vocabulary, proto `05-quick-session-setup.html`).
//
// WHY IT EXISTS ALONGSIDE THE WIZARD. The guided sheet asks "what kind of
// night", then hands over a canvas. That is the right shape for a flow somebody
// intends to shape. It is the wrong shape for the case that comes up every
// clear evening - this object, this many subs, these filters - where the canvas
// is a step between the operator and the sky.
//
// THIS IS NOT `sky/sheets/quick.tsx`. That one is the PHONE's IMAGE THIS, opened
// on a target the finder already knows, and it owns a night arc, a passes
// calculation and a persisted per-phone default set. This one is the flows
// library's creation door at tablet and desktop: it starts from no target, so it
// carries the catalogue search, and it asks for subs PER FILTER because that is
// what `POST /api/flows/quick` takes. Sharing the screen would mean one of the
// two lying about what it sends.
//
// THE GRAPH IS STILL THE SERVER'S. `server/astrodeck/flows/wizard.py`'s `quick`
// wraps the same `generate()` the wizard uses; this sheet sends four answers and
// never a graph.
//
// THE FILTER ROWS ARE THE RIG'S, never typed. `cyclePlanRows.ts` opens on why: a
// filter name is not a label - it lands in the FITS `FILTER` header, in the
// filename and in the calibration matcher's key - so a row that cannot be typed
// cannot name a slot the wheel does not have. `resolveWheel` is shared with the
// FILTER CYCLE inspector so both offer exactly the same slots.

import { useMemo, useState, type JSX } from "react";

import { CatalogSearch } from "../../../../../components/atlas/CatalogSearch";
import { defaultExposureFor, resolveWheel } from "../../../../../components/flows/cyclePlanRows";
import { apiErrorPayload } from "../../../../../lib/apiError";
import { accessPhrase, useCanControlCapture, useCanControlMount } from "../../../../../lib/caps";
import { flowsApi } from "../../../../../lib/flowsApi";
import { useStore } from "../../../../../store";
import { NxIcon } from "../../../../icons";
import { nav } from "../../../../router";
import { explainLock } from "../../../../shell/explain";
import {
  ActionButton, Card, Checkbox22, Label, LockNote, Mono, NumberField, Sheet, Switch,
} from "../../../../ui";
import {
  DEFAULT_SUBS, OSC_LABEL, QUICK_RUN_FAILED, QUICK_SAVE_FAILED, quickPayload, targetFromEntry,
  type QuickTarget,
} from "./quickPayload";

/** The two footers under the channel list. One of them is a claim about the rig
 *  in front of the operator, so which one shows is decided by `fromRig` and
 *  never by a guess. */
export const ONE_CHANNEL_NOTE =
  "No filter wheel is reporting named slots, so this is one channel and the "
  + "frames carry no filter name.";
export const WHEEL_NOTE =
  "Rows are this rig's wheel. A filter it does not have cannot be typed here.";

/** What the sheet cannot do yet, said as the thing to do next rather than as a
 *  refusal. */
export const PICK_TARGET_REASON = "Pick a target first: search for it by name or catalogue id.";
export const NO_FILTER_REASON = "Tick at least one filter.";
export const SUBS_REASON = "Set how many subs of each filter to take (at least 1).";
export const BUSY_REASON = "Already creating the flow. One moment.";

export function FlowQuickSheet(): JSX.Element {
  const setUi = useStore((s) => s.flowsSetUi);
  const loadLibrary = useStore((s) => s.flowsLoadLibrary);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const pushConfirm = useStore((s) => s.pushConfirm);
  // Narrow selectors, one field each - the sheet must not re-render on every
  // status frame the rig publishes.
  const wheelNames = useStore((s) => s.status?.filterwheel?.names);
  const wheelOpaque = useStore((s) => s.status?.filterwheel?.opaque);
  const wheelNarrow = useStore((s) => s.status?.filterwheel?.narrowband);
  const canSave = useCanControlCapture();
  const canRun = useCanControlMount();

  const { filters: wheel, fromRig } = resolveWheel(wheelNames, wheelOpaque);
  // NO WHEEL IS ONE CHANNEL (D-SKY-3). `fromRig` is false both when nothing is
  // attached and when the attached wheel reports only unnamed slots, and both
  // answers are the same here: this sheet cannot name this rig's filters, so it
  // does not pretend to. The FILTER CYCLE inspector's assumed seven are a
  // PLANNING fallback on a canvas the operator can then edit; a sheet whose next
  // button says SAVE AND RUN must describe the rig in front of it.
  const oneChannel = !fromRig;
  const slots = oneChannel ? [OSC_LABEL] : wheel;

  const [target, setTarget] = useState<QuickTarget | null>(null);
  const [subs, setSubs] = useState<number>(DEFAULT_SUBS);
  const [ticked, setTicked] = useState<readonly string[] | null>(null);
  const [exposures, setExposures] = useState<Record<string, number>>({});
  const [guided, setGuided] = useState(true);
  const [busy, setBusy] = useState(false);

  // The slots start ticked, so the common case is "pick a target and press
  // SAVE". `null` means "the operator has not touched this yet", which is what
  // lets the default follow a wheel that connects while the sheet is open
  // instead of freezing whatever was on screen at mount.
  const chosen = ticked ?? (oneChannel ? [] : wheel);
  const expFor = (f: string): number => exposures[f] ?? defaultExposureFor(f);

  /** Narrowband is the RIG's answer where it has one.
   *
   *  Index back into the rig's own array by NAME, not by position: `resolveWheel`
   *  has already removed the opaque and unnamed slots, so slot 4 of the usable
   *  list is not slot 4 of the carousel and reading `narrowband[4]` would label a
   *  broadband slot narrowband. With no answer the fallback is the same rule that
   *  picks the default exposure, so the word and the seconds cannot disagree. */
  const isNarrow = (f: string): boolean => {
    const i = fromRig ? (wheelNames ?? []).findIndex((n) => n === f) : -1;
    const flag = i >= 0 ? wheelNarrow?.[i] : undefined;
    return typeof flag === "boolean" ? flag : defaultExposureFor(f) === 180;
  };

  // `NumberField` clamps at `min: 1` and REJECTS a blank rather than reading it
  // as 0, so this cannot be false through the UI. It is kept because it is the
  // guard that decides whether a payload may be built at all, and a caller that
  // ever seeds a bad default must be refused rather than post `subs: 0` (the
  // server 422s it, `Field(ge=1)`).
  const subsOk = Number.isFinite(subs) && subs >= 1;
  const frames = subsOk ? subs * (chosen.length || 1) : 0;

  const close = (): void => {
    // Idempotent, and not decoration: `FlowsScreen` still sets this flag to open
    // the legacy overlay and `FlowsCanvasHost` still mounts the component that
    // reads it, until T-R7-20 cuts both over.
    setUi({ quickOpen: false });
    nav.back();
  };

  const saveReason = !canSave
    ? `Creating a flow needs ${accessPhrase("control.capture")}.`
    : busy ? BUSY_REASON
      : !target ? PICK_TARGET_REASON
        : !subsOk ? SUBS_REASON
          : (!oneChannel && chosen.length === 0) ? NO_FILTER_REASON
            : null;
  const runReason = saveReason
    ?? (!canRun ? `Starting a run needs ${accessPhrase("control.mount")}.` : null);

  const payload = useMemo(
    () => (target && subsOk
      ? quickPayload({
        target,
        subs,
        filters: chosen,
        exposures: Object.fromEntries(slots.map((f) => [f, expFor(f)])),
        guided,
        run: false,
      })
      : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [target, subs, subsOk, chosen, exposures, guided, slots.join("|")],
  );

  const submit = async (run: boolean): Promise<void> => {
    if (!payload) return;
    setBusy(true);
    try {
      const res = await flowsApi.quick({ ...payload, run });
      const id = res?.flow?.id;
      if (!id) throw new Error("the server returned a flow with no id");
      // The library reloads under the operator and this flow lands somewhere in
      // a folder that may already hold thirty, so the card says which is theirs.
      setUi({ highlightId: id });
      close();
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
      // in the refusal's own payload - so a failure to START must not be
      // reported as a failure to save, and the flow it did save must still reach
      // the library.
      //
      // Read off the decoded BODY, not off the message. FastAPI nests the
      // payload under `detail` and `parseApiError` renders only the human
      // sentence out of it, so a message-substring test would be true or false
      // depending on whether the refusal happened to be a dict or a string - and
      // "already running" is a string.
      const body = apiErrorPayload((e as { body?: unknown }).body);
      const saved = body?.saved === true && typeof body?.flow_id === "string";
      if (run && saved) {
        setUi({ highlightId: body!.flow_id as string });
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
   *  This is the only control on this screen that can open the shutter, and the
   *  sheet it sits on was designed to be fast. "Are you sure" alone would be a
   *  speed bump; the frame count, the filters and whether the guider is in the
   *  loop are what an operator actually checks before walking away from the
   *  rig. */
  const saveAndRun = async (): Promise<void> => {
    const ok = await pushConfirm({
      title: `Start ${frames} frames on ${target?.name ?? "this target"} now?`,
      body: (
        <ul className="nx-create-confirm">
          <li>
            {chosen.length > 0
              ? `${subs} subs each of ${chosen.join(", ")}`
              : `${subs} subs, one channel`}
          </li>
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

  const toggleFilter = (f: string, on: boolean): void => {
    setTicked(on
      ? chosen.filter((x) => x !== f)
      // Wheel order, not click order: the server sorts it anyway, and a list
      // that reordered under the operator would read as a bug.
      : wheel.filter((x) => x === f || chosen.includes(x)));
  };

  return (
    <Sheet
      data-testid="session-flow-quick"
      title="QUICK FLOW"
      sub={target ? `${target.name} · ${target.ra} · ${target.dec}` : "pick a target, set the subs, go"}
      icon={<NxIcon name="camera" size={18} />}
      backLabel="FLOWS"
      onBack={close}
      footer={(
        <div className="nx-create-foot">
          <ActionButton
            kind="secondary"
            size="lg"
            full
            data-testid="flow-quick-save"
            lockedReason={saveReason}
            onExplain={explainLock}
            busy={busy}
            onPress={() => { void submit(false); }}
          >
            SAVE
          </ActionButton>
          <ActionButton
            kind="primary"
            size="lg"
            full
            data-testid="flow-quick-run"
            lockedReason={runReason}
            onExplain={explainLock}
            busy={busy}
            onPress={() => { void saveAndRun(); }}
          >
            SAVE AND RUN
          </ActionButton>
        </div>
      )}
    >
      <div className="nx-create-stack">
        {/* 1. the target */}
        <section className="nx-create-group">
          <Label size={10}>TARGET</Label>
          {target ? (
            <Card tone="default">
              <div className="nx-create-target" data-testid="quick-target">
                <span className="nx-create-name" data-big="true">{target.name}</span>
                <Mono size={10} tone="dim">{`${target.ra} · ${target.dec}`}</Mono>
                {/* Not a `Chip`: a chip publishes `aria-pressed`, which would
                    announce this as a toggle that is currently off. It is a
                    verb, and it takes the search box back. */}
                <ActionButton
                  kind="ghost"
                  data-testid="quick-target-change"
                  onPress={() => setTarget(null)}
                >
                  CHANGE
                </ActionButton>
              </div>
            </Card>
          ) : (
            // No `placeholder` override: `CatalogSearch`'s own default carries
            // the "M 31" example, and its header calls that example
            // load-bearing. A second copy here is a second place for it to go
            // stale.
            <CatalogSearch className="w-full" onPick={(e) => setTarget(targetFromEntry(e))} />
          )}
        </section>

        {/* 2. how many */}
        <section className="nx-create-group">
          <NumberField
            data-testid="quick-subs"
            label={oneChannel ? "SUBS" : "SUBS PER FILTER"}
            ariaLabel={oneChannel ? "Subs" : "Subs per filter"}
            value={subs}
            onCommit={setSubs}
            min={1}
            integer
            hint={oneChannel
              ? "this rig shoots one channel, so this is the whole frame count"
              : "the engine takes this many of EVERY ticked filter"}
          />
        </section>

        {/* 3. the channels, read off the wheel */}
        <section className="nx-create-group">
          <div className="nx-create-head">
            <Label size={10}>{oneChannel ? "CHANNEL" : "FILTERS"}</Label>
            <Mono size={10} tone="dim">
              {oneChannel
                ? "one channel · no wheel to command"
                : `${chosen.length} of ${wheel.length} from the wheel`}
            </Mono>
          </div>
          <Card tone="default" padding={0}>
            {slots.map((f) => {
              const on = oneChannel || chosen.includes(f);
              return (
                <div
                  key={f}
                  className="nx-create-row"
                  data-testid="quick-channel"
                  data-channel={f}
                  data-checked={on ? "true" : "false"}
                >
                  {oneChannel ? (
                    <span className="nx-create-rowname">
                      <span className="nx-create-name">{f}</span>
                      <Mono size={10} tone="dim">every sub, same channel</Mono>
                    </span>
                  ) : (
                    <Checkbox22
                      data-testid="quick-channel-tick"
                      checked={on}
                      onChange={() => toggleFilter(f, on)}
                      label={(
                        <span className="nx-create-rowname">
                          <span className="nx-create-name">{f}</span>
                          <Mono size={10} tone="dim">
                            {isNarrow(f) ? "narrowband" : "broadband"}
                          </Mono>
                        </span>
                      )}
                    />
                  )}
                  {/* No box on an unticked row. An exposure for a filter nobody
                      selected is a number with nothing to spend it on, and it
                      reads as though the filter were in the night. */}
                  {on ? (
                    <NumberField
                      data-testid="quick-exposure"
                      className="nx-create-exp"
                      label="SUB"
                      ariaLabel={`${f} exposure, seconds`}
                      value={expFor(f)}
                      onCommit={(n) => setExposures((p) => ({ ...p, [f]: n }))}
                      min={1}
                      integer
                      unit="s"
                    />
                  ) : null}
                </div>
              );
            })}
          </Card>
          <p className="nx-create-note">{oneChannel ? ONE_CHANNEL_NOTE : WHEEL_NOTE}</p>
        </section>

        {/* 4. guiding */}
        <section className="nx-create-group">
          <Switch
            data-testid="quick-guide"
            checked={guided}
            onChange={setGuided}
            label="Guide"
            note={guided
              ? "a GUIDE stage rides along, so the subs can be longer than the mount tracks"
              : "no GUIDE stage: the run is unguided and the subs are limited by the mount"}
          />
        </section>

        {/* The night this will produce, in the numbers the operator gave. */}
        <p className="nx-create-note" data-testid="quick-summary">
          {subsOk && (oneChannel || chosen.length > 0)
            ? `${frames} frames: dusk window, slew and center, autofocus, `
              + `${guided ? "guiding, " : ""}`
              + `${oneChannel ? "capture" : "filter cycle"}, session report. `
              + "A refocus fires if HFR drifts, and an unsafe reading aborts and parks."
            : "Pick a target, set the subs and tick at least one filter."}
        </p>

        <LockNote
          data-testid="flow-quick-lock"
          reason={canSave ? null : `Creating a flow needs ${accessPhrase("control.capture")}.`}
        />
      </div>
    </Sheet>
  );
}
