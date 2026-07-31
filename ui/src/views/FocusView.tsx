import { useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Icon } from "../components/icons";
import {
  useStore,
  useStatus,
  useFocus,
  useLastAutofocusResult,
  useHfrThresholds,
  useLinkDown,
  useLivePreview,
  useLivePreviewId,
  useNight,
  useOverlays,
  usePlan,
  usePreviews,
  useSelectedPreviewId,
  useStretch,
  useViewport,
} from "../store";
import type { FocusEvent } from "../types";
import { VCurve, type FocusFit } from "../components/graphs";
import {
  afResultAgeLabel, deriveAutofocusParams, plainFocusVerdict, focusButtonState,
} from "../lib/autofocus";
import { ProviderBadge } from "../components/ProviderBadge";
import { PreviewStage } from "../components/preview/PreviewStage";
import { FocusVerdict, AutofocusVerdict } from "../components/preview/FocusVerdict";
import { BahtinovAid } from "../components/preview/BahtinovAid";
import { FrameStats } from "../components/preview/FrameStats";
import { Field, LockedChip, LockedNote, Panel, Stat } from "../components/ui";
import { accessPhrase, useCanControlCapture } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { HELP } from "../help";
import StepDial from "../components/ui/StepDial";
import { nudgeLabel } from "../lib/stepDial";

/** The magnitudes the dial offers. 1 for a final twiddle, 1000 to cross the
 *  whole critical zone on a 30k-step EAF. */
const STEP_VALUES = [1, 10, 100, 1000] as const;

/**
 * minus · dial · plus — the thumb row (design doc §Thumb zones).
 *
 * This replaced a 3x2 grid of fixed nudges. The grid's real cost was not its
 * size but where it forced itself to live: six 44px targets cannot sit beside a
 * preview, so they sat below it, so every adjustment was scroll-down / tap /
 * scroll-up / look — the complaint that started this whole design.
 */
function StepRow({
  step,
  onStep,
  reason,
  onNudge,
  onBlocked,
}: {
  step: number;
  onStep: (v: number) => void;
  /** Why nudging is unavailable, or null. Never a bare boolean: a blocked
   *  control must be able to say why (house rule §11.8). */
  reason: string | null;
  onNudge: (delta: number) => void;
  onBlocked: (reason: string) => void;
}): JSX.Element {
  const nudge = (sign: 1 | -1) => {
    if (reason) { onBlocked(reason); return; }
    onNudge(sign * step);
  };
  const btn = (sign: 1 | -1) => (
    <button
      type="button"
      className={`btn tap min-h-[44px] mono !normal-case justify-center flex-1 ${
        reason ? "opacity-40" : ""
      }`}
      aria-disabled={reason ? true : undefined}
      aria-label={`Move focuser ${nudgeLabel(step, sign)} steps${reason ? ` — ${reason}` : ""}`}
      title={reason ?? `Move ${nudgeLabel(step, sign)} steps`}
      onClick={() => nudge(sign)}
    >
      {sign > 0 ? "+" : "−"}
    </button>
  );
  return (
    <div className="flex items-stretch gap-2 mb-3">
      {btn(-1)}
      <StepDial
        values={STEP_VALUES}
        value={step}
        onChange={onStep}
        ariaLabel="Focuser step size"
        disabled={!!reason}
        disabledReason={reason}
        onBlocked={onBlocked}
      />
      {btn(1)}
    </div>
  );
}

export default function FocusView() {
  const status = useStatus();
  const focus = useFocus();
  // Canonical latest-completed-run record (F5: R2-FOC-01/DOC-FOC-01) — lives in
  // the store (not local state), so it rehydrates on mount and survives
  // navigating away and back; only a NEW run's own terminal event replaces it.
  const afResult = useLastAutofocusResult();
  const showToast = useStore((s) => s.showToast);
  const canFocus = useCanControlCapture(); // focuser/autofocus is imaging-control class

  // Live preview so manual focus is not blind (spec §10). Read-only here: zoom/pan
  // + verdict, no stretch/overlay controls (those are the Capture surface).
  const shown = useLivePreview();
  const previews = usePreviews();
  const selectedId = useSelectedPreviewId();
  const liveId = useLivePreviewId();
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();
  const setViewport = useStore((s) => s.setViewport);
  const selectPreview = useStore((s) => s.selectPreview);
  const focusControls = useRef<{ fit: () => void; hundred: () => void; zoomIn: () => void; zoomOut: () => void } | null>(
    null,
  );
  const pinned = selectedId != null && selectedId !== liveId;
  const prevFrame = useMemo(() => {
    if (!shown) return null;
    const idx = previews.findIndex((p) => p.id === shown.id);
    return idx > 0 ? previews[idx - 1] : null;
  }, [shown, previews]);
  const [absTarget, setAbsTarget] = useState("");
  // The dial's magnitude. 100 is the useful default on a 30k-step EAF: 10 is a
  // twiddle, 1000 crosses the whole critical zone.
  const [step, setStep] = useState<number>(100);
  const [afExposure, setAfExposure] = useState("2");
  const [afStep, setAfStep] = useState("350");
  // UX-25: per-filter / per-binning autofocus. "" filter = leave the wheel where
  // it is. Binning options track the camera's reported ceiling (UX-27 shape).
  const [afFilter, setAfFilter] = useState("");
  const [afBin, setAfBin] = useState("2");
  const [afAdvanced, setAfAdvanced] = useState(false);

  const plan = usePlan();
  const foc = status?.focuser;
  const pos = foc?.position ?? 0;
  const running = focus?.state === "running";
  // NOV-12 Bahtinov aid armed state (server truth via poll_status).
  const bahtOn = status?.bahtinov_active ?? false;
  // UX-25: filter/binning for the autofocus sweep.
  const filterNames = status?.filterwheel?.names ?? [];
  const afMaxBin = Math.min(8, Math.max(1, status?.camera?.max_bin ?? 4));
  const afBinOptions = Array.from({ length: afMaxBin }, (_, i) => i + 1);

  // The additive `fit` (method/R²/curve/trendlines) rides on the raw `focus`
  // event; types.ts FocusEvent stays untouched, so read it via a cast — same
  // pattern the store uses for `status.providers` (useProviders). Only the
  // LIVE V-curve chart below reads this (it needs the in-progress sweep's
  // fit while `running`); the Result panel reads the store's persisted
  // `afResult` instead (see above) so it survives navigation.
  const focusExt = focus as (FocusEvent & { fit?: FocusFit | null }) | null;
  const focusFit = focusExt?.fit ?? null;
  // Pixel scale for the arcsec HFR in the verdict: prefer computed optics, fall
  // back to the live frame's scale when the sensor reports it.
  const pixelScale = status?.optics?.image_scale_arcsec_px ?? shown?.pixel_scale_arcsec ?? null;
  const refocusArmed = plan.autofocus_every > 0 || plan.refocus_on_temp_delta_c > 0;

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };
  // UX-29: clamp both the relative nudge buttons and Go-to into [0, max] instead
  // of relying on a silent server clamp — a negative or past-max target is a
  // user error we can catch before the round-trip.
  const focMax = typeof foc?.max === "number" && foc.max > 0 ? foc.max : null;
  const clampPos = (p: number) => {
    const r = Math.max(0, Math.round(p));
    return focMax != null ? Math.min(focMax, r) : r;
  };
  const moveTo = (p: number) => act(() => api.post("/api/focuser/move", { position: clampPos(p) }));

  // "Go to position" only checked non-empty string; non-numeric input (e.g.
  // "abc") produced NaN -> JSON.stringify serializes NaN to null, sending a
  // null position to the server. Guard on numeric validity too, matching
  // Capture's exposure guard (lib/exposure.ts).
  const absTargetNum = Number(absTarget);
  const absTargetInvalid = absTarget.trim() === "" || !Number.isFinite(absTargetNum);

  // ------------------------------------------------- UX #24: stated reasons
  // Every blocked focuser control resolves to a sentence here and is rendered
  // through the house `LockedChip` — dim + lock glyph + aria-disabled +
  // aria-label + a tooltip that opens on TAP. The native `disabled` attribute
  // is not used: it removes the control and its reason from the a11y tree, and
  // `title=` never fires on the tablet this rig is driven from.
  const readOnlyReason = canFocus
    ? null
    : `Read-only session — ${accessPhrase("control.capture")} required`;
  // Shared by every control that commands the focuser.
  const focuserReason =
    readOnlyReason
    ?? (!foc ? "No focuser is connected — connect one on the Equipment page"
      : running ? "Autofocus is running — let the sweep finish first"
        : null);
  const goReason =
    focuserReason ?? (absTargetInvalid
      ? "Type a position number in the box first"
      : null);
  const haltReason = readOnlyReason;

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_300px]">
      <div className="flex flex-col gap-4">
        {/* live preview so manual focus is not blind (spec §10) */}
        <Panel title="Live Preview" right={<FocusVerdict preview={shown} prev={prevFrame} hfrGood={hfrGood} hfrWarn={hfrWarn} />}>
          <div className="flex flex-col gap-2">
            <PreviewStage
              compact
              preview={shown}
              viewport={viewport}
              setViewport={setViewport}
              stretch={stretch}
              overlays={overlays}
              hfrGood={hfrGood}
              hfrWarn={hfrWarn}
              night={night}
              linkDown={linkDown}
              pinned={pinned}
              newSincePinned={pinned && selectedId != null ? previews.filter((p) => p.id > selectedId).length : 0}
              onReturnToLive={() => selectPreview(null)}
              onControls={(c) => (focusControls.current = c)}
            />
            <div className="flex items-center gap-1 preview-toolbar">
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom out" onClick={() => focusControls.current?.zoomOut()}>−</button>
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom in" onClick={() => focusControls.current?.zoomIn()}>+</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.fit()}>Fit</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.hundred()}>100%</button>
            </div>
            {shown && <FrameStats preview={shown} hfrGood={hfrGood} hfrWarn={hfrWarn} compact />}
          </div>
        </Panel>

        {/* NOV-12 Bahtinov focus aid — arm/disarm + the live signed offset and a
            go/stop verdict, sitting under the live preview it reads from. */}
        <Panel title="Bahtinov Focus" right={!canFocus && <ReadOnlyBadge />}>
          <BahtinovAid preview={shown} />
          {/* UX #24: the read-only reason was `title=` only — on a tablet the
              button was simply dim and mute. LockedChip speaks it. */}
          <button
            className={`btn w-full tap min-h-11 mt-3 ${!canFocus ? "opacity-40" : ""} ${bahtOn ? "btn-accent" : ""}`}
            aria-disabled={!canFocus || undefined}
            aria-pressed={bahtOn}
            aria-label={readOnlyReason
              ? `${bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"} — ${readOnlyReason}`
              : undefined}
            onClick={
              !canFocus
                ? undefined
                : () =>
                    act(() =>
                      api.post(
                        bahtOn ? "/api/focuser/bahtinov/stop" : "/api/focuser/bahtinov/start",
                        bahtOn ? {} : { exposure_s: 1, gain: 100, binning: 1 },
                      ),
                    )
            }
          >
            {bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"}
          </button>
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mt-2" />}
          <p className="text-[11px] text-dim mt-2 leading-relaxed">
            Put a Bahtinov mask on the scope and point at a bright star, then watch
            the middle spike offset drop to zero.
          </p>
        </Panel>

        <Panel title="V-Curve · HFR vs Position"
          right={running && <span className="text-accent text-[11px] blink tracking-widest uppercase">measuring…</span>}>
          <VCurve points={focus?.points ?? []} best={focus?.best ?? null} fit={focusFit} running={running} />
        </Panel>
      </div>

      <div className="flex flex-col gap-4">
        {/* Result panel — the verdict-first outcome lives in the right column
            above the Focuser, matching the design reference (F5). */}
        <Panel title="Result" right={<ProviderBadge cap="autofocus" />}>
          {running ? (
            <div className="text-accent text-sm blink">Measuring…</div>
          ) : afResult ? (
            <>
              {(() => {
                const pv = plainFocusVerdict({
                  state: afResult.state, hfr: afResult.best?.hfr ?? null,
                  r2: afResult.fit?.r2 ?? null, hfrGood, hfrWarn,
                });
                const tone = pv.tone === "good" ? "text-good" : pv.tone === "warn" ? "text-warn"
                  : pv.tone === "bad" ? "text-bad" : "text-dim";
                return (
                  <div className="mb-2" role="status" aria-live="polite">
                    <div className={`text-base font-semibold ${tone}`}>{pv.headline}</div>
                    <div className="text-xs text-dim">{pv.detail}</div>
                  </div>
                );
              })()}
              <AutofocusVerdict
                state={afResult.state}
                hfr={afResult.best?.hfr ?? null}
                r2={afResult.fit?.r2 ?? null}
                method={afResult.fit?.method ?? null}
                pixelScaleArcsec={pixelScale}
                hfrGood={hfrGood}
                hfrWarn={hfrWarn}
                message={afResult.message}
              />
              {afResult.state === "done" && afResult.best && (
                <div className="mt-3">
                  <Stat label="best position" value={afResult.best.position} />
                </div>
              )}
              {/* Persisted-run provenance (F5: R2-FOC-01/DOC-FOC-01) — this
                  line, like the verdict above, reads store.lastAutofocusResult
                  (not local state), so it is still here after navigating away
                  and back; only a NEW run's own terminal event replaces it. */}
              <p className="text-[11px] text-dim mt-2">
                {afResult.provider ? `${afResult.provider.label} · ` : ""}
                {afResult.filter ? `${afResult.filter} · ` : ""}
                {afResultAgeLabel(afResult.ts, Date.now())}
              </p>
            </>
          ) : (
            <div className="text-faint text-sm">Run autofocus to measure focus quality.</div>
          )}
        </Panel>

        {/* Rail order (design doc): READOUT at the top nearest the image,
            the ACTION next, and the thumb row LAST because that is where a
            hand actually rests. Autofocus used to be the bottom-most panel,
            which on a phone is off the end of a scroll. */}
        <Panel title="Autofocus" right={!canFocus && <ReadOnlyBadge />}>
          {(() => {
            const bs = focusButtonState({ canFocus, hasFocuser: !!foc, running });
            const onTap = () => {
              const d = deriveAutofocusParams({
                focuserMax: focMax,
                maxBin: status?.camera?.max_bin ?? null,
                maxGain: status?.camera?.max_gain ?? null,
                liveExposureS: shown?.exposure_s ?? null,
                liveGain: shown?.gain ?? null,
                liveStars: shown?.stars ?? null,
                liveHfr: shown?.hfr ?? null,
              });
              // With the settings panel OPEN the user is explicitly steering, so
              // their fields win; closed, the derived values do. One button,
              // one rule, and the panel says which is in force. (Before, a
              // SECOND button carried the manual params and the two were
              // indistinguishable at a glance.)
              const manual = afAdvanced;
              return act(() => api.post("/api/focuser/autofocus", manual ? {
                exposure_s: Number(afExposure) || d.exposure_s,
                step: Number(afStep) || d.step,
                binning: Number(afBin) || d.binning,
                gain: d.gain,
                steps_each_side: d.steps_each_side,
                ...(afFilter !== "" ? { filter: Number(afFilter) } : {}),
              } : {
                exposure_s: d.exposure_s, gain: d.gain, step: d.step,
                steps_each_side: d.steps_each_side, binning: d.binning,
              }));
            };
            // UX #24: `focusButtonState` already computes excellent gating copy —
            // it was just handed to `title=`, which a fingertip never fires. The
            // hero keeps its own chrome (a lock-glyph 56px button, not a chip) and
            // the reason is now spoken by aria-label AND printed underneath.
            // ONE line: the action and its settings (design doc §Landscape —
            // rail). The button used to span the whole screen with a separate
            // "▸ Advanced" row beneath it, in a rail only 300px wide; the gear
            // now sits on the same line, which is both smaller and closer to
            // the thing it configures.
            return (
              <>
                <div className={`flex items-stretch gap-2 ${bs.disabled ? "mb-1.5" : "mb-3"}`}>
                  <button
                    className={`btn btn-accent flex-1 tap-lg min-h-[56px] ${bs.disabled ? "opacity-40" : ""}`}
                    aria-disabled={bs.disabled || undefined}
                    aria-label={bs.reason ? `${bs.label} — ${bs.reason}` : undefined}
                    onClick={bs.disabled ? undefined : onTap}
                  >
                    {bs.locked && <Icon name="lock" size={13} className="inline -mt-0.5 mr-1.5" />}
                    {!bs.locked && <Icon name="focus" size={14} className="inline -mt-0.5 mr-1.5" />}
                    {bs.label}
                  </button>
                  <button
                    className={`btn tap min-h-[56px] px-3 ${afAdvanced ? "border-accent text-accent" : ""}`}
                    aria-expanded={afAdvanced}
                    aria-label="Autofocus settings"
                    title="Autofocus settings"
                    onClick={() => setAfAdvanced((v) => !v)}
                  >
                    <Icon name="settings" size={16} />
                  </button>
                </div>
                {bs.reason && <LockedNote reason={bs.reason} className="mb-3" />}
              </>
            );
          })()}

          {afAdvanced && (
            <>
              <div className="grid grid-cols-2 gap-3 mb-4">
                <Field label="Exposure (s)">
                  <input className="field" value={afExposure}
                    readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                    onChange={(e) => setAfExposure(e.target.value)} />
                </Field>
                <Field label="Step size" hint={HELP.stepSize}>
                  <input className="field" value={afStep}
                    readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                    onChange={(e) => setAfStep(e.target.value)} />
                </Field>
                {filterNames.length > 0 && (
                  <Field label="Filter">
                    {/* <select> has no `readOnly`; the locked state shows the value
                        through the house locked stand-in instead of a native
                        `disabled` select with no reachable reason. */}
                    {readOnlyReason ? (
                      <LockedChip reason={readOnlyReason} className="btn w-full">
                        {afFilter === "" ? "current" : filterNames[Number(afFilter)] ?? "current"}
                      </LockedChip>
                    ) : (
                      <select className="field" value={afFilter}
                        onChange={(e) => setAfFilter(e.target.value)}>
                        <option value="">current</option>
                        {filterNames.map((name, i) => <option key={`${i}-${name}`} value={i}>{name}</option>)}
                      </select>
                    )}
                  </Field>
                )}
                <Field label="Binning">
                  {readOnlyReason ? (
                    <LockedChip reason={readOnlyReason} className="btn w-full">
                      {afBin}×{afBin}
                    </LockedChip>
                  ) : (
                    <select className="field" value={afBin}
                      onChange={(e) => setAfBin(e.target.value)}>
                      {afBinOptions.map((b) => <option key={b} value={b}>{b}×{b}</option>)}
                    </select>
                  )}
                </Field>
              </div>
              {/* NO second run button here. There used to be one an inch below
                  the hero, and two buttons that both say "run autofocus" is a
                  question the user has to answer before every run. These fields
                  now feed the ONE button above — see the note below. */}
              <p className="text-[11px] text-dim leading-snug">
                These override what the button above would have chosen. Close this
                panel to go back to automatic settings.
              </p>
            </>
          )}
          <p className="text-[11px] text-dim mt-3 leading-relaxed">
            Sweeps 4 steps each side of current position, measures star HFR,
            fits the V-curve and drives to its minimum.
          </p>
          <p className="mono text-[11px] text-faint mt-2">
            {refocusArmed
              ? `Refocus armed:${plan.autofocus_every > 0 ? ` every ${plan.autofocus_every} frames` : ""}` +
                `${plan.autofocus_every > 0 && plan.refocus_on_temp_delta_c > 0 ? " ·" : ""}` +
                `${plan.refocus_on_temp_delta_c > 0 ? ` Δtemp ${plan.refocus_on_temp_delta_c}°C` : ""}`
              : "Refocus: manual only (set cadence in the plan)"}
          </p>
        </Panel>

        <Panel title="Focuser" right={!canFocus && <ReadOnlyBadge />}>
          {/* The header pill keeps its WHY in a `title=`; say it out loud here. */}
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mb-3" />}
          <div className="flex items-end justify-between mb-4">
            <Stat label="position" value={foc ? pos : "—"} />
            <Stat label="max" value={foc?.max ?? "—"} />
            <Stat label="temp" value={foc?.temperature?.toFixed(1) ?? "—"} unit="°C" />
          </div>
          {/* The thumb row (design doc §Thumb zones). Six buttons became three
              controls: minus, the magnitude dial, plus. Held in two hands the
              thumbs rest at the bottom corners, so that is where - and + go,
              with the rarely-changed dial between them as the one thing you
              have to look at. */}
          <StepRow
            step={step}
            onStep={setStep}
            reason={focuserReason}
            onNudge={(d) => moveTo(pos + d)}
            onBlocked={(r) => showToast("warning", r)}
          />
          <div className="grid grid-cols-[1fr_auto_auto] gap-2 items-end">
            <Field label="Go to position">
              <input className="field" placeholder={String(pos)} value={absTarget}
                readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                onChange={(e) => setAbsTarget(e.target.value)} />
            </Field>
            {/* UX #24 — GO was the finding's named example: four different
                blockers collapsed into one native `disabled`, with no title, no
                aria-label and no note. It now names the one that applies. */}
            {goReason ? (
              <LockedChip reason={goReason} className="btn tap min-h-[44px] justify-center">
                Go
              </LockedChip>
            ) : (
              <button className="btn tap min-h-[44px]"
                onClick={() => moveTo(absTargetNum)}>Go</button>
            )}
            {/* Halt is urgent motion-stop -> stays 1-tap (R9). Disabled for viewers
                (they can't have a focuser move in flight to halt).

                UX #14 / S3: the SAME non-hue danger encoding Capture's Stop now
                carries (filled-square glyph + 2px border + a capped-luminance
                fill), so "stop the thing" has one silhouette across the app
                instead of a different red outline per view. */}
            {haltReason ? (
              <LockedChip reason={haltReason}
                className="btn btn-danger tap min-h-[44px] !border-2 justify-center">
                Halt
              </LockedChip>
            ) : (
              <button
                className="btn btn-danger tap min-h-[44px] !border-2 inline-flex items-center justify-center gap-1.5"
                style={{ background: "color-mix(in srgb, var(--danger-ink) 15%, transparent)" }}
                onClick={() => act(() => api.post("/api/focuser/halt"))}>
                <Icon name="stop" size={13} className="shrink-0 fill-current" aria-hidden />
                Halt
              </button>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
