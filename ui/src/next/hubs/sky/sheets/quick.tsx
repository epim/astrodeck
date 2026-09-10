// quick.tsx - IMAGE THIS: one target, tonight, in one screen
// (hub-sky plan D.1-D.4, design README section 3, screenshot 05).
//
// WHAT THIS SHEET DECIDES, and what it deliberately does not.
//
// It decides how long, which filters, how long each sub, and which automation
// stages ride along. It does NOT decide the graph: `POST /api/flows/quick` takes
// four answers and `server/astrodeck/flows/wizard.py` builds the night, because
// a "quick" generator written in TypeScript would be a second implementation of
// the same rule set and the two would drift on the first deviation the doctor
// forces on one of them.
//
// THE ARITHMETIC ON SCREEN IS THE ENGINE'S ARITHMETIC. `subs` is per filter and
// there is no way to send a different count per slot, so the sheet counts
// PASSES and every checked row shows the same number - see `quickModel.ts` for
// the whole argument. A screen showing four different counts over a payload
// carrying one number would be two descriptions of one night.
//
// BOTH COORDINATES ALWAYS TRAVEL. `to_plan` reads ra/dec and never the name, so
// a flow carrying a name and no coordinates slews to the TARGET node's shipped
// M31 and files the frames under the wrong object. `targetFromEntry`/`raHms`/
// `decDms` are imported from `session/flows/create/quickPayload` - next's own
// copy of those legacy helpers - rather than rewritten for that reason.

import { useEffect, useMemo, useRef, useState, type JSX, type ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, Card, Checkbox22, Chip, Label, Mono, Sheet,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { windowLabel } from "../../../lib/reach";
import { useFrameSettings, useFraming, useSite, useStore, useWeather } from "../../../../store";
import { apiErrorPayload } from "../../../../lib/apiError";
import { resolveRoleConnected } from "../../../../lib/caps";
import { flowsApi } from "../../../../lib/flowsApi";
// The six pure helpers come from the REBUILT create area, not from
// `components/flows/QuickFlow.tsx`: they are declared inside a 497-line legacy
// component, and importing them from there dragged `Overlay`, `CatalogSearch`,
// `HonestButton` and their Tailwind tree into this lazily-split sheet for the
// sake of four functions and two strings (wave R7 section 2.1's finding).
// `create/__tests__/createDom.test.tsx` pins the copy to the legacy original.
//
// The DEEP module, not the area barrel: `create/index.ts` is the area's seam and
// pulls both creation sheets and `create.css` in with it, which would trade one
// oversized import for another. `quickPayload.ts` is where these six actually
// live and it imports nothing but types.
import {
  QUICK_RUN_FAILED, QUICK_SAVE_FAILED, decDms, quickPayload, raHms, targetFromEntry,
  type QuickTarget,
} from "../../session/flows/create/quickPayload";
import type { FlowGraphRec, FlowRecordRec } from "../../../../components/flows/flowsTypes";
import { skyPrefs, type QuickPrefs } from "../finder";
import {
  ASSUMED_WHEEL_NOTE, FILTER_FOOTER, INFO, NO_FILTER_REASON,
  floorLegend, mosaicPlanNote, oscFooter,
} from "./quickCopy";
import {
  OSC_LABEL, channelLabel, filterColor, finishLabel, hourStops, hoursLabel, isDawnStop,
  nextExposure, oscCount, passesFor, planLine, quickRows, resolveColour, snapHours, wheelModel,
} from "./quickModel";
import {
  NightArc, curveFromNight, hoursToDawn, type ArcCurve, type ArcHold,
} from "./quickNightArc";
import { withDarksAfter, withDuskFlats, withRotation, withTargetPool } from "./flowGraphExtras";
import {
  commandedPa, framingMatches, mosaicBaseName, mosaicGroupId, panelsToTargets,
} from "../frame/mosaic";
import { useCatalogTarget, useCatalogTargets, type SearchRow } from "./targetsCatalog";
import { useVisibilityNight } from "./quickVisibility";

/** The six automation chips, in the design's own order (proto logic.js:198). */
const AUTOMATION: { key: string; label: string }[] = [
  { key: "af", label: "Autofocus" },
  { key: "guide", label: "Guide" },
  { key: "dither", label: "Dither" },
  { key: "cloud", label: "Cloud hold" },
  { key: "hfr", label: "HFR watchdog" },
  { key: "stack", label: "Live stack" },
];

/** The two calibration chips GAP-ANALYSIS 7 asks for. They are not automation:
 *  each one adds a REAL node to the saved graph (`flowGraphExtras.ts`), which is
 *  why they sit in their own row and say so. */
const CALIBRATION: { key: string; label: string; info: string }[] = [
  { key: "flats", label: "add dusk flats", info: "flats" },
  { key: "darks", label: "darks after", info: "darks" },
];

const DITHER_STEPS = [1, 2, 3, 5, 10];

/** Hours the arc spans when the site has no astronomical darkness tonight. A
 *  high-latitude summer is a real answer; eight hours of chart is the honest
 *  fallback, and the dawn label says "no astro-dark" rather than a time. */
const NO_DARK_SPAN_H = 8;

function targetFromParams(
  params: Record<string, string>,
  row: SearchRow | null,
): QuickTarget | null {
  const ra = Number(params.ra);
  const dec = Number(params.dec);
  if (Number.isFinite(ra) && Number.isFinite(dec)) {
    // The typed-position path. `name` is the literal "Typed position" the
    // coordinates sheet sends, and the id is the coordinate string - so the
    // TARGET node carries something an operator recognises in the report.
    return {
      name: (params.name ?? "").trim() || `${raHms(ra)} ${decDms(dec)}`,
      ra: raHms(ra),
      dec: decDms(dec),
    };
  }
  if (!row) return null;
  return targetFromEntry({
    id: row.id,
    // A solar-system row's `name` is a whole sentence and its `id` is the label,
    // the opposite way round from a DSO. `targetFromEntry` does `name || id`,
    // which is right for a DSO and wrong for a body - so the swap happens here.
    name: row.kind === "solar_system" ? row.id : (row.name || row.id),
    type: row.type,
    ra_hours: row.ra_hours,
    dec_deg: row.dec_deg,
    mag: row.mag,
    size_arcmin: row.size_arcmin,
  });
}

export function QuickSessionSheet({ params }: SheetProps): JSX.Element {
  const site = useSite();
  const frame = useFrameSettings("capture");
  const weather = useWeather();
  const enqueueToast = useStore((s) => s.enqueueToast);
  const flowsOpen = useStore((s) => s.flowsOpen);

  const poolIds = useMemo(
    () => (params.pool ?? "").split(",").map((s) => s.trim()).filter(Boolean),
    [params.pool],
  );
  const isPool = poolIds.length > 1;

  const single = useCatalogTarget(isPool ? null : (params.target ?? null));
  const poolRows = useCatalogTargets(poolIds);
  const anchor: SearchRow | null = isPool ? (poolRows.rows[0] ?? null) : single.row;

  const target = useMemo(
    () => targetFromParams(params, anchor),
    [params, anchor],
  );

  /**
   * ONE HORIZON, EVERYWHERE ON THIS CHART.
   *
   * This number is the visibility fetch's `alt_limit`, the `below` colouring on
   * the arc, the dashed floor line and (through `SkyHub`) the mosaic-night
   * card's limit. It used to be three different things: the fetch used the
   * site's `horizon_min_deg`, the chart drew a hardcoded 25 and the copy called
   * it "the 25° floor" (review #34). On a site with a 30° limit the dashed line
   * sat five degrees under the ranking's own obstruction rule and under the
   * engine's - and the red part of the curve, drawn from the site's number,
   * disagreed with the line drawn beside it.
   *
   * The inventory is explicit that there is deliberately no client-side horizon
   * constant, and the finder does not have one either: `FLOOR_DEG` there is the
   * SEEING floor, an advisory quality chip the user switches on, not the limit
   * the mount refuses below.
   */
  const horizonMin = site?.horizon_min_deg ?? 0;
  const raForVis = Number.isFinite(Number(params.ra)) ? Number(params.ra) : anchor?.ra_hours ?? null;
  const decForVis = Number.isFinite(Number(params.dec)) ? Number(params.dec) : anchor?.dec_deg ?? null;
  const night = useVisibilityNight(raForVis, decForVis, horizonMin);

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const dawnH = hoursToDawn(night, nowMs);
  const span = dawnH ?? NO_DARK_SPAN_H;

  // ------------------------------------------------------------ the wheel
  const wheelNames = useStore((s) => s.status?.filterwheel?.names);
  const wheelOpaque = useStore((s) => s.status?.filterwheel?.opaque);
  const wheelNarrow = useStore((s) => s.status?.filterwheel?.narrowband);
  const wheelExposures = useStore((s) => s.status?.filterwheel?.exposures);

  /**
   * IS THERE A RIG HERE AT ALL, which is not the same as "is there a wheel".
   *
   * The one-channel card claims something about a real sensor ("no filter
   * wheel is connected, so every sub is the same channel"). With nothing
   * connected there is no sensor to claim it about, and this sheet is a planner
   * on a laptop - which is the case `ASSUMED_WHEEL_NOTE` exists for. The house
   * helper is used rather than a raw `status.connected.camera` read because a
   * bridged rig reports its roles on `backend_links` instead.
   */
  const cameraConnected = useStore((s) => resolveRoleConnected(
    "camera", s.status?.backend_links, s.status?.connected, s.equipConnected,
  ).connected);

  /**
   * WHETHER THIS CAMERA IS COLOUR, AND HOW WE KNOW.
   *
   * The driver answers first: `status.camera.bayer_pattern` and
   * `status.camera.is_color` are on the wire (T-U7b-10), so a mono camera is
   * known to be mono before a single frame has been taken. A captured FRAME's
   * `PreviewInfo.bayer_pattern` is the fallback for a rig whose driver reports
   * neither. `resolveColour` returns which of the three answered, and `isColor`
   * is `null` when none did - an absence, not a "no".
   *
   * Read through one narrow selector rather than `usePreview()` so a preview
   * arriving every few seconds during a capture does not re-render this sheet.
   *
   * `useShallow` IS LOAD-BEARING, not tidiness. `resolveColour` builds a fresh
   * object on every call, and zustand compares a selector's RESULT by identity
   * through `useSyncExternalStore` - so a bare selector re-renders on every
   * store read, re-runs the selector, gets another new object, and the sheet
   * dies with "Maximum update depth exceeded" before it paints. The three
   * fields are flat, so a shallow compare is exact.
   */
  const bayerPattern = useStore(useShallow(
    (s) => resolveColour(s.status?.camera, s.preview?.bayer_pattern ?? null),
  ));

  const [prefs, setPrefs] = useState(() => skyPrefs.getQuick());
  const persist = (next: QuickPrefs): void => {
    setPrefs(next);
    skyPrefs.setQuick(next);
  };

  /**
   * Choosing a window, and remembering WHICH choice it was.
   *
   * The dawn stop is a different length every night, so persisting tonight's
   * 5.2 h and replaying it in December would silently turn "all night" into
   * "5h 12m" - with the screen saying 5h 12m. `dawn` records the choice; this
   * sheet resolves it against tonight's dawn every time it opens. The settings
   * sheet reads and writes the same flag through the same parser.
   */
  const chooseHours = (h: number): void =>
    persist({ ...prefs, hours: h, dawn: isDawnStop(h, dawnH) });

  const wheel = useMemo(
    () => wheelModel(
      { names: wheelNames, opaque: wheelOpaque, narrowband: wheelNarrow, exposures: wheelExposures },
      prefs.on,
      prefs.exp,
      cameraConnected,
    ),
    [wheelNames, wheelOpaque, wheelNarrow, wheelExposures, prefs.on, prefs.exp, cameraConnected],
  );

  const hours = Math.min(prefs.dawn && dawnH != null ? dawnH : prefs.hours, span);

  /**
   * THE CYCLE ARITHMETIC IS NOT COMPUTED FOR A RIG THAT HAS NO CYCLE.
   *
   * It used to be. With no wheel, `wheel.slots` was the seven ASSUMED names, so
   * `rows` had seven rows and `checked.length` was seven - none of which could
   * ever reach the payload (`filters: []`) - and that seven was then handed to
   * `planLine` as `checkedCount`, where only the one-channel head's ignoring it
   * kept "7 filters" off the button. Numbers that describe nothing are how a
   * screen and a night come apart.
   */
  const rows = useMemo(
    () => (wheel.oneChannel ? [] : quickRows(hours, wheel.slots)),
    [wheel.oneChannel, hours, wheel.slots],
  );
  const checked = rows.filter((r) => r.checked);
  const passes = wheel.oneChannel ? 0 : passesFor(hours, wheel.slots);

  /**
   * THE ONE CHANNEL, and what it is called in the FITS header.
   *
   * A rig whose wheel has exactly one clear slot posts that slot's REAL name,
   * not the `OSC` label: the payload's `filters` is what makes the engine
   * command the carousel, and a wheel left parked on its blackout slot would
   * otherwise shoot the whole night through a piece of metal. Only a rig with
   * no wheel at all posts an empty `filters` and the `OSC` label, because there
   * is nothing to command and an invented name would land in the header.
   */
  const oneSlot = wheel.source === "one-slot" ? (wheel.slots[0] ?? null) : null;
  const channelName = oneSlot?.name ?? OSC_LABEL;
  const oscExposure = oneSlot?.exposure ?? prefs.exp[OSC_LABEL] ?? 120;
  const oscSubs = oscCount(hours, oscExposure);
  const channel = channelLabel(wheel, bayerPattern);

  // ------------------------------------------------------------- the arc
  const curves: ArcCurve[] = useMemo(() => {
    const c = curveFromNight(
      night, nowMs, horizonMin, anchor?.id ?? "target", target?.name ?? "target", "#00D2FF",
    );
    return c ? [c] : [];
  }, [night, nowMs, horizonMin, anchor, target]);

  const holds: ArcHold[] = useMemo(() => {
    const f = weather?.forecast;
    if (!weather || !f || weather.ignore_tonight) return [];
    const times = f.times ?? [];
    const cloud = f.cloud ?? [];
    const thr = weather.threshold_pct ?? 100;
    const out: ArcHold[] = [];
    let start: number | null = null;
    for (let i = 0; i < times.length && i < cloud.length; i++) {
      const t = Date.parse(times[i]);
      if (Number.isNaN(t)) continue;
      const h = (t - nowMs) / 3600_000;
      const over = cloud[i] >= thr;
      if (over && start == null) start = h;
      if (!over && start != null) { out.push({ from: start, to: h }); start = null; }
    }
    if (start != null) out.push({ from: start, to: span });
    return out.filter((h) => h.to > 0 && h.from < span)
      .map((h) => ({ from: Math.max(0, h.from), to: Math.min(span, h.to) }));
  }, [weather, nowMs, span]);

  const clearHours = useMemo(() => {
    const covered = holds.reduce((sum, h) => sum + Math.max(0, Math.min(hours, h.to) - Math.min(hours, h.from)), 0);
    return Math.max(0, hours - covered);
  }, [holds, hours]);

  // ------------------------------------------------------------- gating
  const capture = useLock({ cap: "control.capture" });
  const mount = useLock({ cap: "control.mount" });
  const noFilters = !wheel.oneChannel && checked.length === 0;
  const noPasses = (wheel.oneChannel ? oscSubs : passes) < 1;
  const [busy, setBusy] = useState(false);

  const ctaReason =
    capture.lockedReason
    ?? mount.lockedReason
    ?? (target == null ? "Waiting for the catalogue to answer for this target." : null)
    ?? (noFilters ? NO_FILTER_REASON : null)
    // A one-channel rig has no pass and no filters, so the cycle's sentence
    // would name two things that are not on its screen.
    ?? (noPasses
      ? (wheel.oneChannel
        ? "One sub is longer than the window - shorten the exposure or lengthen the night."
        : "One pass of these filters is longer than the window - shorten a sub or lengthen the night.")
      : null)
    ?? (busy ? "Already creating the flow. One moment." : null);

  /**
   * THE FRAMING THIS SHEET IS ALLOWED TO USE.
   *
   * `store.framing` is ONE global session, shared with the Atlas, so a framing
   * kept for M31 was being drawn over a flow generated for M42 and (once the
   * panels were wired in) would have queued M31's panels under M42's name. It
   * counts only when its own target is the target this sheet was opened for -
   * by catalogue id for an object, by the free-roam group id for a patch.
   */
  const framing = useFraming();
  const framingId = isPool ? null : (params.target ?? params.name ?? null);
  const mine = framingMatches(framing, framingId);
  const panels = mine ? (framing?.panels ?? []) : [];
  const mosaicCols = mine ? (framing?.mosaic.cols ?? 1) : 1;
  const mosaicRows = mine ? (framing?.mosaic.rows ?? 1) : 1;
  const isMosaic = panels.length > 1;
  const addTargetsToPlan = useStore((s) => s.addTargetsToPlan);

  const label = planLine({
    oneChannel: wheel.oneChannel,
    checkedCount: checked.length,
    oscExposure,
    oscCount: oscSubs,
    hoursLabel: hoursLabel(hours, dawnH),
    poolCount: poolIds.length,
    panels: panels.length,
  });

  // ------------------------------------------------------------ generate
  const generate = async (): Promise<void> => {
    if (ctaReason || !target) return;
    setBusy(true);
    try {
      const filters = wheel.oneChannel
        ? (oneSlot ? [oneSlot.name] : [])
        : checked.map((r) => r.name);
      const exposures: Record<string, number> = wheel.oneChannel
        ? { [channelName]: oscExposure }
        : Object.fromEntries(rows.map((r) => [r.name, r.exposure]));
      const subs = wheel.oneChannel ? oscSubs : passes;
      const answers = quickPayload({
        target,
        subs,
        filters,
        exposures,
        guided: prefs.extras.guide !== false,
        run: false,
      });
      const res = await flowsApi.quick({ ...answers, name: `Quick session: ${target.name}` });
      const id = res?.flow?.id;
      if (!id) throw new Error("the server returned a flow with no id");

      // The three edits the four answers cannot express, applied to the graph
      // the server just proved, then saved back through the canvas's own route.
      const record = res.flow as unknown as FlowRecordRec;
      let graph = record.graph as FlowGraphRec;
      const before = graph;
      if (prefs.extras.flats) graph = withDuskFlats(graph);
      if (prefs.extras.darks) graph = withDarksAfter(graph);
      // THE CAMERA ANGLE, which nothing sent before. `wizard.quick` leaves the
      // node vocabulary's shipped `rotation: 23.4` on the TARGET node and only
      // replaces name/ra/dec, and `to_plan` reads that as a real position angle
      // - so every quick flow was quietly asking a connected rotator for PA
      // 23.4 while the framing card promised something else. -1 is `to_plan`'s
      // own "no angle constraint" sentinel (0 is a REAL position angle there).
      graph = withRotation(graph, commandedPa(mine ? (framing?.rotation_deg ?? 0) : 0));
      if (isPool) {
        graph = withTargetPool(graph, poolRows.rows.map((r) => (r.kind === "solar_system" ? r.id : r.name || r.id)));
      }
      if (graph !== before) await flowsApi.save(id, { ...record, graph });

      /**
       * THE MOSAIC PANELS REACH THE NIGHT (review #3, plan H.6).
       *
       * FRAME mode drew them, kept them on `framing.panels` and toasted that
       * they went into the flow - and nothing read them. `panelsToTargets` was
       * called by one test and nothing else.
       *
       * They cannot ride in the quick PAYLOAD: `FlowQuickBody.target` is ONE
       * `{name, ra, dec}` and `wizard.quick` builds a one-target night, so
       * there is no field to put N pointings in. The engine's mosaic mechanism
       * is not a stage either - `nodeDefs` has 21 node types and none is
       * `mosaic`. It is N plan targets sharing a `mosaic_group`, which is
       * exactly what `AtlasView.sendToPlan` produced and what
       * `store.addTargetsToPlan` replaces-by-group so a re-frame updates its
       * panels instead of doubling them. So the plan path is the one taken, and
       * the flow card says so on the synthetic MOSAIC row.
       */
      if (isMosaic && framing) {
        addTargetsToPlan(
          panelsToTargets(panels, mosaicBaseName(framing), mosaicGroupId(framing), framing.rotation_deg),
          mosaicGroupId(framing),
        );
      }

      await flowsOpen(id);
      // The mosaic travels in the HASH, not read back off the global framing
      // slice: the card must describe what this generate actually queued, so a
      // framing changed afterwards (or one belonging to another target) cannot
      // put a MOSAIC row on a flow that has none.
      nav.sheet("flow", isMosaic ? { id, mosaic: `${mosaicCols}x${mosaicRows}` } : { id });
    } catch (e) {
      // THE SAVE AND THE RUN ARE TWO OUTCOMES OF ONE REQUEST, and the answer is
      // read off the decoded BODY, never off the message string - FastAPI nests
      // the payload under `detail` and the human sentence is only part of it.
      const payload = apiErrorPayload((e as { body?: unknown }).body);
      const saved = payload?.saved === true && typeof payload?.flow_id === "string";
      enqueueToast({
        level: "error",
        title: saved ? QUICK_RUN_FAILED : QUICK_SAVE_FAILED,
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------- header
  const altNow = anchor?.alt;
  const minutesLeft = useMemo(() => {
    const c = curves[0];
    if (!c) return null;
    const up = c.samples.filter((s) => !s.below);
    if (up.length < 2) return up.length === 0 ? 0 : null;
    const step = c.samples.length > 1 ? (c.samples[1].h - c.samples[0].h) * 60 : 10;
    return Math.round((up.length - 1) * step);
  }, [curves]);

  const title = isPool ? `PLAN ${poolIds.length} TARGETS` : `IMAGE ${target?.name ?? "…"}`;
  const subParts = [
    isPool ? poolRows.rows.map((r) => (r.kind === "solar_system" ? r.id : r.name || r.id)).join(", ")
      : (anchor?.kind === "solar_system" ? anchor.type : anchor?.type ?? ""),
    altNow != null ? `alt ${Math.round(altNow)}°` : null,
    minutesLeft != null ? `${windowLabel(minutesLeft)} left` : null,
  ].filter((s): s is string => !!s && s !== "");

  // The hold-to-learn brief is a SHEET, not a toast: the design's own card is a
  // bottom sheet the reader dismisses, and a 2.8 s toast is not long enough to
  // read a paragraph. The current params ride along so BACK returns to a quick
  // sheet that still knows which target it was opened for - sheet params are
  // shared by the whole stack, so dropping them here would empty the screen
  // underneath.
  const openBrief = (topic: string): void => {
    if (!INFO[topic]) return;
    nav.sheet("brief", { ...params, info: topic });
  };

  return (
    <Sheet
      data-testid="sky-quick"
      title={title}
      sub={subParts.join(" · ")}
      icon={<NxIcon name="camera" size={18} />}
      backLabel="SKY"
      onBack={() => nav.back()}
      right={
        <button
          type="button"
          className="nx-chip"
          aria-label="what these mean"
          data-testid="quick-info"
          onClick={() => openBrief("setup")}
          style={{ width: 36, height: 36, padding: 0, justifyContent: "center" }}
        >
          ?
        </button>
      }
      footer={
        <ActionButton
          kind="primary"
          size="xl"
          full
          data-testid="quick-generate"
          lockedReason={ctaReason}
          onExplain={capture.onExplain}
          busy={busy}
          onPress={() => void generate()}
        >
          {`GENERATE FLOW · ${label}`}
        </ActionButton>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {/* ------------------------------------------------------ night arc */}
        <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <HeaderRow
            label="NIGHT ARC"
            onHold={() => openBrief("arc")}
            right={`${hoursLabel(hours, dawnH)} · to ${finishLabel(nowMs, hours)} · ${hoursLabel(clearHours, null)} clear`}
          />
          <NightArc
            curves={curves}
            holds={holds}
            hours={hours}
            span={span}
            floorDeg={horizonMin}
            nowLabel={`NOW ${finishLabel(nowMs, 0)}`}
            dawnLabel={dawnH == null ? "NO ASTRO-DARK" : `DAWN ${finishLabel(nowMs, dawnH)}`}
            emptyNote={
              curves.length === 0
                ? "No altitude curve yet - the ephemeris for this target has not come back."
                : null
            }
            onHours={(h) => chooseHours(snapHours(h, dawnH))}
          />
          <button
            type="button"
            data-testid="quick-arc-floor"
            onClick={() => openBrief("floor")}
            style={{
              background: "none", border: 0, padding: 0, textAlign: "left", cursor: "help",
              fontSize: 11, lineHeight: 1.45, color: "var(--text-3, #7683a5)",
            }}
          >
            {floorLegend(horizonMin)}
          </button>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {hourStops(dawnH).map((h) => (
              <Chip
                key={h}
                data-testid="quick-hour-stop"
                active={Math.abs(h - hours) < 0.05}
                onClick={() => chooseHours(h)}
              >
                {hoursLabel(h, dawnH)}
              </Chip>
            ))}
          </div>
        </section>

        {/* --------------------------------------------------- filter cycle */}
        {wheel.oneChannel ? (
          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {/* The header used to read "EXPOSURE · ONE-SHOT COLOUR" whatever the
                camera was. The identity moved into the card, where it is read
                off a bayer pattern the rig reported rather than asserted. */}
            <HeaderRow label="EXPOSURE" onHold={() => openBrief("osc")} right={null} />
            <Card tone="default">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                  <span
                    data-testid="quick-osc-title"
                    className="nx-display"
                    style={{ fontSize: 12, letterSpacing: ".1em" }}
                  >
                    {channel.title}
                  </span>
                  <Mono size={10} tone="dim" data-testid="quick-osc-sub">{channel.sub}</Mono>
                  <Mono size={10} tone="dim">
                    {`gain ${frame.gain} · bin ${frame.binning} · reject HFR 3.5″`}
                  </Mono>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <button
                    type="button"
                    className="nx-chip"
                    data-testid="quick-osc-exposure"
                    onClick={() => persist({
                      ...prefs,
                      exp: { ...prefs.exp, [channelName]: nextExposure(oscExposure) },
                    })}
                  >
                    {`${oscExposure} s`}
                  </button>
                  <span data-osc-count={oscSubs}>
                    <Mono size={11} data-testid="quick-osc-count">{`×${oscSubs}`}</Mono>
                  </span>
                </div>
              </div>
            </Card>
            <p style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>
              {oscFooter(bayerPattern)}
            </p>
          </section>
        ) : (
          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <HeaderRow
              label="FILTER CYCLE"
              onHold={() => openBrief("wheel")}
              right={
                wheel.source === "assumed"
                  ? ASSUMED_WHEEL_NOTE
                  : `${checked.length} of ${wheel.slots.length} from the wheel · tap a time to change it`
              }
            />
            <div style={{ display: "flex", height: 6, borderRadius: 999, overflow: "hidden", gap: 2, background: "rgba(120,140,200,.1)" }}>
              {rows.map((r) => (
                <div
                  key={r.name}
                  style={{ flexGrow: 1, flexShrink: 1, flexBasis: 0, background: filterColor(r.name), opacity: r.checked ? 1 : 0.12 }}
                />
              ))}
            </div>
            <Card tone="default" padding={0}>
              {rows.map((r) => (
                <div
                  key={r.name}
                  data-filter-row={r.name}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1fr) 68px 50px 58px",
                    alignItems: "center",
                    minHeight: 48,
                    borderBottom: "1px solid rgba(120,140,200,.12)",
                    opacity: r.checked ? 1 : 0.5,
                  }}
                >
                  <Checkbox22
                    checked={r.checked}
                    onChange={(next) => persist({ ...prefs, on: { ...prefs.on, [r.name]: next } })}
                    label={
                      <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                        <span data-filter-label={r.name} className="nx-display" style={{ fontSize: 12, letterSpacing: ".1em" }}>{r.name}</span>
                        <Mono size={10} tone="dim">{r.narrowband ? "narrowband" : "broadband"}</Mono>
                      </span>
                    }
                  />
                  <button
                    type="button"
                    className="nx-chip"
                    data-testid="quick-exposure"
                    data-filter={r.name}
                    onClick={() => persist({ ...prefs, exp: { ...prefs.exp, [r.name]: nextExposure(r.exposure) } })}
                  >
                    {`${r.exposure} s`}
                  </button>
                  <span data-filter-count={r.count}>
                    <Mono size={11} tone="dim">{`×${r.count}`}</Mono>
                  </span>
                  <span data-filter-total={r.checked ? r.totalS : 0}>
                    <Mono size={11}>{r.checked ? fmtBank(r.totalS) : "-"}</Mono>
                  </span>
                </div>
              ))}
            </Card>
            <p style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>
              {FILTER_FOOTER}
            </p>
          </section>
        )}

        {/* ----------------------------------------------------- automation */}
        <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <HeaderRow label="AUTOMATION" onHold={() => openBrief("setup")} right="hold a chip to learn · hold dither to set" />
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {AUTOMATION.map((a) => (
              <HoldChip
                key={a.key}
                testid="quick-auto"
                label={a.key === "dither" ? `Dither · every ${prefs.ditherN}` : a.label}
                on={prefs.extras[a.key] !== false}
                onToggle={() => persist({ ...prefs, extras: { ...prefs.extras, [a.key]: prefs.extras[a.key] === false } })}
                onHold={() => {
                  if (a.key !== "dither") { openBrief(a.key); return; }
                  const i = DITHER_STEPS.indexOf(prefs.ditherN);
                  const next = DITHER_STEPS[(i + 1) % DITHER_STEPS.length];
                  persist({ ...prefs, ditherN: next, extras: { ...prefs.extras, dither: true } });
                  enqueueToast({ level: "info", title: `Dither every ${next} frames.` });
                }}
              />
            ))}
          </div>
        </section>

        {/* ---------------------------------------------------- calibration */}
        <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <HeaderRow label="INCLUDED" onHold={() => openBrief("flats")} right="each adds a real stage to the saved flow" />
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {CALIBRATION.map((c) => (
              <HoldChip
                key={c.key}
                testid="quick-calib"
                label={c.label}
                on={prefs.extras[c.key] === true}
                dashed
                onToggle={() => persist({ ...prefs, extras: { ...prefs.extras, [c.key]: prefs.extras[c.key] !== true } })}
                onHold={() => openBrief(c.info)}
              />
            ))}
          </div>
        </section>

        {/* ------------------------------------------------------ the mosaic */}
        {isMosaic && (
          <p
            data-testid="quick-mosaic-note"
            style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}
          >
            {mosaicPlanNote(panels.length, mosaicCols, mosaicRows)}
          </p>
        )}

        {/* -------------------------------------------------- what it cannot */}
        {single.error != null && !isPool && (
          <p data-testid="quick-catalog-error" style={{ fontSize: 11.5, color: "var(--warn, #ffb454)" }}>
            {`Could not read this target from the catalogue - ${single.error}. The window and the filters below are still yours to set; the flow cannot be generated until the coordinates come back.`}
          </p>
        )}
        {single.notes.map((n) => (
          <p key={n} style={{ fontSize: 11.5, color: "var(--text-3, #7683a5)" }}>{n}</p>
        ))}
      </div>
    </Sheet>
  );
}

/** "1h 30m" for a banked total; the row is a duration, not a countdown. */
function fmtBank(seconds: number): string {
  const min = Math.round(seconds / 60);
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** A section header whose LABEL is the hold-to-learn target, per the design's
 *  "hold any control for a plain-language explanation". A press is enough here:
 *  a 450 ms hold is undiscoverable on a label, and a label has nothing else to
 *  do with a tap. */
function HeaderRow({ label, right, onHold }: {
  label: string;
  right: ReactNode;
  onHold: () => void;
}): JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
      <button
        type="button"
        onClick={onHold}
        style={{ background: "none", border: 0, padding: 0, cursor: "help", flexShrink: 0 }}
        aria-label={`What ${label.toLowerCase()} means`}
      >
        <Label size={10}>{label}</Label>
      </button>
      {right != null && (
        <Mono size={10} tone="dim">
          <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "block" }}>
            {right}
          </span>
        </Mono>
      )}
    </div>
  );
}

/** A chip that toggles on a tap and explains itself on a hold.
 *
 *  The hold is a real timer rather than a `title=`, because `title` never fires
 *  on a touch screen and this row's audience is a first-timer on a phone. A hold
 *  that has fired swallows the release, so learning about a chip does not also
 *  turn it off. */
function HoldChip({ label, on, dashed = false, onToggle, onHold, testid }: {
  label: string;
  on: boolean;
  dashed?: boolean;
  onToggle: () => void;
  onHold: () => void;
  testid: string;
}): JSX.Element {
  const held = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clear = (): void => {
    if (timer.current != null) { clearTimeout(timer.current); timer.current = null; }
  };
  useEffect(() => clear, []);
  return (
    <button
      type="button"
      className="nx-chip"
      data-testid={testid}
      data-chip={label}
      data-active={on ? "true" : undefined}
      aria-pressed={on}
      style={dashed && !on ? { borderStyle: "dashed" } : undefined}
      onPointerDown={() => {
        held.current = false;
        clear();
        timer.current = setTimeout(() => { held.current = true; timer.current = null; onHold(); }, 450);
      }}
      onPointerUp={clear}
      onPointerLeave={clear}
      onPointerCancel={clear}
      onClick={() => { if (held.current) { held.current = false; return; } onToggle(); }}
    >
      {label}
    </button>
  );
}
