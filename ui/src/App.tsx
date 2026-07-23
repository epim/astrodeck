import { useEffect, useState } from "react";
import type { JSX } from "react";
import { useStore, useBrightness, useAuthMethods, useWeatherAlertKey, type ViewName } from "./store";
import { fmtHm } from "./lib/weather";
import Logo from "./components/Logo";
import { connectWs } from "./ws";
import { Icon, type IconName } from "./components/icons";
import { Led } from "./components/ui";
import ConnectionBanner from "./components/ConnectionBanner";
import HealthLeds from "./components/HealthLeds";
import RoleBadge from "./components/RoleBadge";
import SignInButton from "./components/SignInButton";
import Toasts from "./components/Toasts";
import LogDrawer from "./components/LogDrawer";
import HeaderControls from "./components/HeaderControls";
import BottomNav from "./components/BottomNav";
import TouchGuard from "./components/TouchGuard";
import { confirmDialog, ConfirmHost } from "./components/ConfirmDialog";
import NotConnectedInterstitial from "./components/NotConnectedInterstitial";
import FirstRunWizard from "./components/FirstRunWizard";
import { useMonitorWakeLock } from "./lib/useWakeLock";
import { useShouldShowLogin, useAuthResolving } from "./lib/caps";
import Login from "./views/Login";
import EquipmentView from "./views/EquipmentView";
import CaptureView from "./views/CaptureView";
import FocusView from "./views/FocusView";
import MountView from "./views/MountView";
import PolarView from "./views/PolarView";
import GuideView from "./views/GuideView";
import SequenceView from "./views/SequenceView";
import PowerView from "./views/PowerView";
import MonitorView from "./views/MonitorView";
import AtlasView from "./views/AtlasView";
import ReportView from "./views/ReportView";
import SettingsView from "./components/settings/SettingsView";

// IA reorder (master-plan Risk-10 canonical 8-entry order, Align before Mount) +
// header/nav entries for Settings (placeholder) and Monitor (real this batch).
// Risk-10: "land the order once, append entries thereafter" — Monitor is an
// APPENDED entry (no reorder, no Power eviction; monitor spec E1/E2). Nav glyphs
// resolve through the single icons.tsx module (Batch-2 2B icon plan).
const NAV: { id: ViewName; label: string; icon: IconName }[] = [
  { id: "connect", label: "Equipment", icon: "rig" },
  { id: "polar", label: "Align", icon: "align" },
  { id: "mount", label: "Mount", icon: "mount" },
  { id: "focus", label: "Focus", icon: "focus" },
  { id: "capture", label: "Capture", icon: "capture" },
  { id: "guide", label: "Guide", icon: "guide" },
  // Atlas slots immediately BEFORE Plan (spec §8 IA): …Guide → Atlas → Plan → Power.
  { id: "atlas", label: "Atlas", icon: "atlas" },
  { id: "sequence", label: "Plan", icon: "plan" },
  { id: "power", label: "Power", icon: "power" },
  { id: "monitor", label: "Monitor", icon: "monitor" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const VIEWS: Record<ViewName, () => JSX.Element> = {
  connect: EquipmentView,
  capture: CaptureView,
  focus: FocusView,
  mount: MountView,
  polar: PolarView,
  guide: GuideView,
  sequence: SequenceView,
  power: PowerView,
  monitor: MonitorView,
  settings: SettingsView,
  atlas: AtlasView,
  // "report" is NOT a primary-nav entry (Batch-4b §2.5 / report viewer spec §1.4):
  // ReportView has landed, reached from the run-complete "View session report →"
  // link (SequenceView) + the mobile overflow sheet (NavMoreSheet), never from
  // primary nav.
  report: ReportView,
};

// Nav gating (onboarding §3b/§7b): equipment-dependent views show the
// NotConnectedInterstitial when !equipConnected (the sticky flag, NOT wsConnected —
// a WS drop shows the reconnecting banner, never this). Rig is never gated; Plan is
// only partially gated (the builder stays usable offline, just Run is disabled —
// that nuance lives inside SequenceView, so `sequence` is NOT gated here). Settings/
// Atlas/Monitor are informational shells — not equipment-gated at the route level.
const GATED: Partial<Record<ViewName, boolean>> = {
  capture: true,
  focus: true,
  mount: true,
  polar: true,
  guide: true,
  power: true,
};

// ============================================================================
// Global brightness dimmer — STORE-OWNED (F-dimmer). The store holds day/night
// brightness, persists localStorage, and is the single writer of the
// --screen-brightness / --scrim-opacity CSS vars (index.css consumes them; the
// values mirror index.html's pre-paint script so first paint never flashes).
// App retains ONLY the always-reachable reset affordance + the Shift+B keybind,
// both routed through store.resetBrightness — no MutationObserver/getComputedStyle
// round-trip, no private brightness copy that could drift from HeaderControls.
// `locked` is NEVER persisted (a lock must not survive reload, design-system §3.3).
// ============================================================================

export default function App() {
  // Split selectors (reliability §13 / Risk-14 perf P0): each subscription is a
  // single slice, so a guide tick (mutates only `guide`) no longer re-renders the
  // whole tree. Chrome components self-subscribe to their own slices.
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const sequence = useStore((s) => s.sequence);
  const status = useStore((s) => s.status);
  const linkDown = useStore((s) => s.wsPhase !== "up");
  const telemetryStale = useStore((s) => s.telemetryStale);
  const equipConnected = useStore((s) => s.equipConnected);
  const runBanner = useStore((s) => s.runBanner);
  const dismissRunBanner = useStore((s) => s.dismissRunBanner);

  // Login gate (W2.6). True ONLY when a login method is enabled AND the caller is
  // unauthenticated (or a first admin still needs creating). When no method is
  // enabled this is ALWAYS false, so the open LAN UI is byte-for-byte unchanged.
  const showLogin = useShouldShowLogin();

  // Auth-resolving guard (H1): once /api/auth/methods reports a method IS enabled
  // but the principal hasn't resolved yet, keep showing the neutral splash rather
  // than flashing the operational shell + a false DISPLAY DISCONNECTED banner
  // before the Login gate decides. Open LAN (methods == []) never triggers this,
  // so today's default is unchanged. Bounded by the same 4s grace below so a
  // wedged /api/me can never trap the tablet behind a spinner (fail-open).
  const authResolving = useAuthResolving();

  // Auth-resolving splash: until the login signal (authMethods) has loaded once,
  // show a neutral splash instead of flashing the console shell before the gate
  // decides (W2.6 polish). Fail OPEN after a short grace so a transport blip never
  // traps the LAN tablet behind a spinner — matching the gate's load-time posture.
  const authMethods = useAuthMethods();
  const [authGraceElapsed, setAuthGraceElapsed] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setAuthGraceElapsed(true), 4000);
    return () => window.clearTimeout(t);
  }, []);

  // Hold a screen wake lock while a sequence is running OR monitorAwake is on —
  // NOT while locked (touch §8.3, R13). Reads its own narrow selectors.
  useMonitorWakeLock();

  // --- brightness reset hatch (app-owned escape hatch; see header comment) ------
  // HeaderControls (3B) owns the in-header dimmer UI; App retains ONLY the
  // always-reachable reset affordance + Shift+B keybind. Both route through the
  // store-owned dimmer slice (F-dimmer), so the reset hatch reads the same live
  // value HeaderControls writes — one source, no MutationObserver round-trip.
  const brightness = useBrightness();
  const resetBrightness = useStore((s) => s.resetBrightness);

  // Always-reachable escape hatch: Shift+B resets brightness to 1.0 (§7.2). Never
  // trapped behind a dimmed-out thumb.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey && (e.key === "B" || e.key === "b") && !e.repeat) {
        const tag = (e.target as HTMLElement | null)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return; // don't hijack typing
        e.preventDefault();
        resetBrightness();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [resetBrightness]);

  useEffect(() => {
    connectWs();
  }, []);

  const Active = VIEWS[view];
  const camConnected = !!status?.connected?.camera?.connected;
  const mountConnected = !!status?.connected?.telescope?.connected;
  const seqRunning = sequence.state === "running" || sequence.state === "paused";
  const seqError = sequence.state === "error";
  const dim = linkDown || telemetryStale;
  // Route-level gating (onboarding §3b): show the interstitial on equipment-gated
  // views while !equipConnected. The view's content is otherwise rendered.
  const gatedOut = !equipConnected && !!GATED[view];
  // Surface the run banner only when a run is active and the user is NOT already on
  // the Monitor (no point nagging while they watch it). Auto-SELECT is handled in
  // the store's guarded rising-edge logic (monitor §3.2) — the banner is the
  // never-forced, always-dismissible affordance (resolves A3/B6).
  const showRunBanner = !!runBanner?.active && view !== "monitor";

  // Auth gate (W2.6): when a method is enabled and the caller is unauthenticated,
  // the whole app is replaced by the full-screen Login. The WS effect above still
  // runs (hooks are unconditional), so loadAuthMethods/loadPrincipal keep polling
  // and the gate dissolves the moment a session is minted — no reload. Toasts +
  // the confirm host stay mounted so login errors and dialogs still surface.
  // High-cloud night warning popup (weather spec §12): fires once per server-
  // side once-per-night latch (weatherAlertKey bumps only on the alert
  // null -> non-null edge, store §9). Acknowledge-only (mode "ok",
  // ConfirmDialog.tsx:140-144) — the user-required hard notice; the
  // ignore-tonight override lives on the Sky Conditions / Sessions cards, not
  // in this dialog. Non-holders never receive weather events (spec §8), so
  // this can never fire for them.
  const weatherAlertKey = useWeatherAlertKey();
  useEffect(() => {
    if (weatherAlertKey === 0) return;
    const w = useStore.getState().weather;
    const a = w?.alert;
    if (!w || !a) return;
    void confirmDialog({
      title: "High cloud forecast tonight",
      body:
        `Forecast peak ${a.peak_pct}% total cloud (${a.dominant_layer} layer ` +
        `dominant) between ${fmtHm(a.start_iso)} and ${fmtHm(a.end_iso)} — ` +
        `at/above your ${w.threshold_pct}% threshold. Auto-resume will hold ` +
        `unless "ignore weather tonight" is set.`,
      tone: "warn",
      mode: "ok",
    });
  }, [weatherAlertKey]);

  if ((authMethods === null || authResolving) && !authGraceElapsed) {
    return (
      <div className="h-full dim-content flex items-center justify-center">
        <div className="flex flex-col items-center gap-3 text-dim">
          <Logo className="w-10 h-10 opacity-80" />
          <span className="text-[11px] tracking-[0.22em] uppercase">Loading…</span>
        </div>
        <div className="overlay-top">
          <Toasts />
        </div>
        <ConfirmHost />
      </div>
    );
  }

  if (showLogin) {
    return (
      <div className="h-full">
        <div className="dim-content h-full">
          <Login />
        </div>
        <div className="overlay-top">
          <Toasts />
        </div>
        <ConfirmHost />
      </div>
    );
  }

  return (
    <div className="h-full">
      {/* ============================================================ .dim-content
          header + nav + main + log drawer; gets filter:brightness via CSS. The
          starfield (day + night) is on .dim-content per 2B index.css, so it dims
          WITH the UI — no body/#root starfield here. */}
      <div className="dim-content h-full flex flex-col">
        {/* UX-33: first focusable element — lets keyboard users skip the nav. */}
        <a href="#main-content" className="skip-link">Skip to content</a>
        {/* ---------------------------------------------- top status strip */}
        <header className="relative z-20 flex items-center gap-3 px-4 h-12 border-b border-line bg-raise/70 backdrop-blur shrink-0">
          <h1 className="font-display font-semibold tracking-[0.3em] text-accent text-sm select-none">
            ASTRO<span className="text-ink">DECK</span>
          </h1>
          {status?.mode && status.mode !== "none" && (
            <span className="hidden sm:inline px-2 py-0.5 border border-line2 text-[9px]
              tracking-[0.18em] uppercase text-accent font-display font-medium"
              title={`Backend: ${status.mode}`}>
              {status.mode === "nina" ? "NINA" : status.mode === "alpaca" ? "ALPACA" : "SIM"}
            </span>
          )}
          {/* Unobtrusive current-role chip (W2.5). Silent for admin (the default
              `none`-provider LAN posture), a small VIEW ONLY / OPERATOR badge
              otherwise — read by glyph + text, never color alone. */}
          <RoleBadge />
          {/* Top status strip. Narrow-width priority (R2-WEA-03 / smoke): a STATUS
              caption for identity, then sequence state, then mount state + a
              compact RA/Dec summary, then ALT/temp/RMS as space widens. Only the
              RA/Dec summary is shrinkable (truncate → ellipsis); every other item
              is shrink-0, so nothing is ever clipped mid-glyph into a stray
              fragment (the "- L" / clipped-cyan bug). */}
          <div className={`hidden md:flex items-center gap-2.5 lg:gap-4 text-xs mono text-dim min-w-0 overflow-hidden
            ${dim ? "opacity-40 saturate-50 transition-opacity" : "transition-opacity"}`}>
            <span className="label !text-[10px] !tracking-[0.18em] shrink-0">STATUS</span>
            {seqRunning && sequence.progress && (
              <span className="text-accent shrink-0 whitespace-nowrap">
                SEQ {sequence.progress.frames_done}/{sequence.progress.frames_total}
              </span>
            )}
            {status?.mount && (
              <>
                <span className={`shrink-0 whitespace-nowrap ${status.mount.tracking ? "text-good" : "text-warn"}`}>
                  {status.mount.parked ? "PARKED" : status.mount.slewing ? "SLEWING"
                    : status.mount.tracking ? "TRACKING" : "IDLE"}
                </span>
                <span className="min-w-0 truncate">{status.mount.ra_str} {status.mount.dec_str}</span>
                <span className="hidden lg:inline shrink-0 whitespace-nowrap">ALT {status.mount.alt.toFixed(0)}°</span>
              </>
            )}
            {status?.camera?.temperature != null && (
              <span className="hidden lg:inline shrink-0 whitespace-nowrap">{status.camera.temperature.toFixed(1)}°C</span>
            )}
            {status?.guider?.guiding && (
              <span className="hidden xl:inline shrink-0 whitespace-nowrap text-good">
                RMS {status.guider.rms_total.toFixed(2)}"
              </span>
            )}
          </div>
          <div className="flex-1" />

          {/* Header controls (touch §11, R16/R27): a narrow-selector child that owns
              the in-header dimmer steppers/slider, the NIGHT 44px icon toggle (shows
              the TARGET state; header-only per R16), and the LOG button with an
              outline-ring error badge. Mounting it does NOT widen App's subscription.
              NIGHT's verb-shaped text button is gone — the icon + state-describing
              aria-label is the affordance (onboarding §6 / C10/C11). */}
          {/* Google sign-in / signed-in email + sign-out (W2.5). Renders NOTHING
              unless a Google provider is configured, so the LAN tablet is
              unchanged. Hidden on the narrowest widths to protect the dimmer/log
              controls; the full affordance also lives in Settings → Account. */}
          <span className="hidden lg:inline-flex"><SignInButton /></span>
          <HeaderControls />
          <HealthLeds />
        </header>

        {/* ConnectionBanner renders null when the link is up and telemetry fresh. */}
        <ConnectionBanner />

        {/* Persistent run banner (monitor §3.2 / B7): never-forced, always
            dismissible affordance to open the Monitor while a run is active.
            >=44px action target. Auto-SELECT (if enabled) happens in the store. */}
        {showRunBanner && (
          <div className="flex items-center gap-3 px-4 py-2 border-b border-line bg-accent2/15 shrink-0 text-xs">
            {/* Global strip truth (R2-PLN-01 partial, minimal): the banner stays up
                through a PAUSED engine (store §monitor 3.2, runBanner survives
                "paused"), so its own state must be read here too — otherwise a
                paused run keeps blinking "RUNNING" against the Plan card's own
                honest PAUSED badge (SeqStateBadge / stateMeta) two clicks away. */}
            <span className={sequence.state === "paused" ? "shrink-0" : "blink shrink-0"}>
              <Led state={sequence.state === "paused" ? "warn" : "busy"}
                label={sequence.state === "paused" ? "Sequence paused" : "Sequence running"} />
            </span>
            <span className="min-w-0 truncate text-ink">
              <span className={`font-display tracking-wider ${sequence.state === "paused" ? "text-warn" : "text-accent"}`}>
                {sequence.state === "paused" ? "SEQUENCE PAUSED" : "SEQUENCE RUNNING"}
              </span>
              {runBanner?.plan_name && <span className="text-dim"> · {runBanner.plan_name}</span>}
              {typeof runBanner?.percent === "number" && (
                <span className="mono text-dim"> · {Math.round(runBanner.percent)}%</span>
              )}
            </span>
            <div className="flex-1" />
            <button
              className="btn btn-accent !py-1.5 !px-3 text-[10px] min-h-[44px] sm:min-h-0"
              onClick={() => setView("monitor")}
            >
              OPEN LIVE
            </button>
            <button
              className="btn !py-1.5 !px-2.5 min-h-[44px] sm:min-h-0 inline-flex items-center"
              onClick={dismissRunBanner}
              aria-label="Dismiss sequence-running banner"
              title="Dismiss"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        )}

        <div className="flex flex-1 min-h-0">
          {/* -------------------------------------------------- left rail */}
          {/* Gating model (onboarding §3b/E16; unified per F-D3): ONE model across
              desktop + mobile — equipment-dependent tabs are always TAPPABLE; an
              equipment-gated tab navigates and the main pane shows the
              NotConnectedInterstitial (route-level `gatedOut`). The rail no longer
              hard-disables (no aria-disabled / no-op onClick); instead a small lock
              icon REPLACES the connected-Led slot as a passive "needs connection"
              hint. This matches BottomNav/NavMoreSheet, which never hard-disabled. */}
          <nav className="hidden sm:flex flex-col w-[72px] border border-line bg-panel rounded-[16px] my-4 ml-4 py-2 shrink-0 overflow-y-auto backdrop-blur-md shadow-[inset_0_1px_1px_rgba(255,255,255,0.05),inset_0_-1px_2px_rgba(0,0,0,0.5)]" aria-label="Primary">
            {NAV.map((n) => {
              const gated = !equipConnected && !!GATED[n.id];
              return (
                <button
                  key={n.id}
                  onClick={() => setView(n.id)}
                  aria-current={view === n.id ? "page" : undefined}
                  title={gated ? "Connect equipment to use this" : undefined}
                  className={`flex flex-col items-center gap-1 py-3 transition-colors relative cursor-pointer
                    ${view === n.id ? "text-accent" : gated ? "text-dim/60 hover:text-ink" : "text-dim hover:text-ink"}`}
                >
                  {view === n.id && <span className="absolute left-0 top-2 bottom-2 w-[2px] bg-accent shadow-[0_0_8px_var(--glow)]" />}
                  <Icon name={n.icon} size={20} />
                  <span className="text-[9px] tracking-[0.18em] font-display font-medium uppercase">{n.label}</span>
                  {/* lock icon REPLACES the connected-Led when gated — a passive hint,
                      NOT a disabled state (the tab still navigates to the interstitial). */}
                  {gated ? (
                    <span className="absolute top-2 right-2.5 text-dim/50" aria-hidden>
                      <Icon name="lock" size={11} />
                    </span>
                  ) : (
                    <>
                      {n.id === "capture" && camConnected && <span className="absolute top-2 right-3"><Led state="on" /></span>}
                      {n.id === "mount" && mountConnected && <span className="absolute top-2 right-3"><Led state="on" /></span>}
                      {n.id === "sequence" && seqError && (
                        <span className="absolute top-2 right-3 led led-bad blink-alert" />
                      )}
                      {n.id === "sequence" && !seqError && seqRunning && (
                        <span className="absolute top-2 right-3 blink">
                          <Led state={sequence.state === "paused" ? "warn" : "busy"} />
                        </span>
                      )}
                    </>
                  )}
                </button>
              );
            })}
          </nav>

          {/* ----------------------------------------------- main content */}
          <main
            id="main-content"
            tabIndex={-1}
            className={`flex-1 overflow-y-auto overflow-x-hidden p-4 pb-20 sm:pb-4 outline-none ${dim ? "opacity-60 transition-opacity" : "transition-opacity"}`}
            key={view}
          >
            <div className="view-enter max-w-[1500px] mx-auto w-full min-h-full flex flex-col">
              {gatedOut ? <NotConnectedInterstitial view={view} /> : <Active />}
            </div>
          </main>

          {/* Log drawer: docked column lg+, bottom sheet below — self-manages via
              store.logOpen; renders nothing when closed. */}
          <LogDrawer />
        </div>

        {/* mobile bottom nav: 5 primary + More (touch §5). BottomNav is a narrow-
            selector child (R27) and renders its own NavMoreSheet; App only mounts it. */}
        <BottomNav />
      </div>

      {/* ================================================================ .dim-scrim
          fixed multiply darkener above content, below overlays; opacity tracks the
          dimmer (--scrim-opacity). pointer-events:none via CSS. */}
      <div className="dim-scrim" aria-hidden />

      {/* ================================================================ .overlay-top
          fixed z-50, NEVER dimmed: toasts, the lock overlay, the brightness reset.
          pointer-events gated to children via CSS. */}
      <div className="overlay-top">
        {/* Toasts: top-center on phone, bottom-right desktop — self-manages. */}
        <Toasts />

        {/* Always-reachable brightness reset (the escape hatch, never dimmed). Only
            offered while the screen is meaningfully dimmed so it isn't chrome. The
            brightness value comes from the store-owned dimmer slice (F-dimmer), the
            same source HeaderControls writes — so it stays correct as it adjusts. */}
        {brightness < 0.95 && (
          <button
            className="fixed bottom-3 left-1/2 -translate-x-1/2 sm:left-auto sm:right-3 sm:translate-x-0
              btn btn-accent !py-1.5 !px-3 text-[10px] min-h-[44px] inline-flex items-center gap-1.5 z-50"
            onClick={resetBrightness}
            title="Reset screen brightness to 100% (Shift+B)"
          >
            <Icon name="sun" size={14} /> 100%
          </button>
        )}

        {/* Touch-guard / screen-lock overlay (touch §8). Self-manages off the store's
            `locked` flag via a narrow selector; renders the monitor-safe opaque
            status chip + slide-to-unlock when engaged, plus the auto-lock countdown.
            lockAvailable is true (reliability's sequence-error render shipped), so the
            lock feature is live. */}
        <TouchGuard />
      </div>

      {/* Promise-modal host (onboarding §1e): renders the active confirmDialog()
          request with focus-trap/Escape/restore. Mounted once near the root so any
          call site (MountView GOTO guard, etc.) can `await confirmDialog({...})`. */}
      <ConfirmHost />

      {/* NOV-2: non-modal first-run setup guide. Self-manages visibility off
          the store's `wizardOpen` slice (renders nothing when closed) and its
          own auto-open effect — App only mounts it. Sits as a sibling of
          .dim-content (like ConfirmHost above), so it's never brightness-
          dimmed, and deliberately does NOT block pointer events over the
          rest of the screen (see FirstRunWizard.tsx header comment). */}
      <FirstRunWizard />
    </div>
  );
}
