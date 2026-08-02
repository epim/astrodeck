import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import {
  useStore, useBrightness, useAuthMethods, useAuthGate, useWeatherAlertKey,
  type ViewName,
} from "./store";
import { getHealth } from "./api/backends";
import { getWeather } from "./api/weather";
import { backendBadge, backendBadgeIsSim } from "./lib/equipment";
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
import { useShouldShowLogin, useAuthResolving, useCanViewWeather } from "./lib/caps";
import { announcementsBlocked, type AuthGate } from "./lib/authGate";
import Login from "./views/Login";
import EquipmentView from "./views/EquipmentView";
import ViewBoundary from "./components/ViewBoundary";
import { preloadAllViews, preloadView } from "./lib/lazyViews";

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
  // APPENDED (polish grab-bag (c), same Risk-10 precedent as Monitor): "what can I
  // image tonight?" gets its own destination on this rail instead of being
  // reachable only from inside Atlas. No reorder, no eviction.
  //
  // MOBILE, stated honestly (review finding F): on a phone Tonight is in the More
  // sheet, i.e. TWO taps (More -> Tonight), which is one MORE than reaching it
  // from Atlas used to be. That is a real cost and it is accepted deliberately:
  //   - Risk-10 ("land the order once, append entries thereafter") forbids the
  //     reorder, and BottomNav's five primary slots are the setup-critical tabs
  //     (Rig/Align/Mount/Focus/Capture, touch spec R14). Tonight is pre-session
  //     planning, not setup-critical, so evicting one of those to promote it
  //     would cost a nightly-used tab to save a tap on an occasional one.
  //   - So this entry does NOT claim to fix a mobile depth problem. What it fixes
  //     is DISCOVERABILITY: on the desktop/tablet rail (the app's primary field
  //     surface) it is a named destination rather than a panel a first-timer has
  //     to already know lives inside Atlas.
  // If mobile depth is ever judged the bigger cost, the additive fix is a link
  // from NotConnectedInterstitial — not a primary-bar reorder.
  { id: "tonight", label: "Tonight", icon: "moon" },
  { id: "settings", label: "Settings", icon: "settings" },
  // APPENDED (review #13, same Risk-10 precedent as Monitor and Tonight above —
  // the rule permits appending, and appending is exactly what this is; no
  // reorder, no eviction).
  //
  // Reports had a route, a working ReportView with its own /api/reports picker,
  // AND a NavMoreSheet entry — and enumerating the rendered nav gave
  // desktop 1440 -> Reports visible: FALSE; tablet 820 -> FALSE; phone 390 ->
  // inside the MORE sheet. So the entire PixInsight hand-off (bundle.zip,
  // frames.csv, per-filter hours, the rejected-frame list) was behind a
  // phone-only door, for the one task in this product that is performed at a
  // desk on a big screen every morning of the year. Two personas hit it; one
  // concluded the feature did not exist. It stays in the MORE sheet too — that
  // is the phone's copy of this rail, not a duplicate.
  { id: "report", label: "Reports", icon: "download" },
  // APPENDED (QA re-entry blocker — same Risk-10 precedent again: append, no
  // reorder, no eviction). Help now hosts the setup guide's permanent door
  // (views/HelpView.tsx), and that door is worthless if the page itself is only
  // reachable from a phone's MORE sheet, the log-drawer footer, or a diagnosed
  // error's deep link. Enumerating the rendered nav before this entry gave the
  // same shape review #13 measured for Reports: desktop 1440 -> Help visible:
  // FALSE; tablet 820 -> FALSE; phone 390 -> inside the MORE sheet only. A lost
  // tester on the propped-up tablet had no "Help" to press at all. It stays in
  // the MORE sheet too — that is the phone's copy of this rail, not a duplicate.
  { id: "help", label: "Help", icon: "info" },
];

// ROUTING + CODE SPLITTING. Every destination except Equipment is a lazily
// imported chunk (the loader table lives in lib/lazyViews.ts, which also explains
// why). Only the views that are actually reachable at first paint are bundled
// eagerly here:
//   - connect / EquipmentView, because store.view starts at "connect" on every
//     cold start, so splitting it would buy nothing and cost a Suspense flash.
//   - Login (imported above) for the same reason on an auth-enabled box.
// Non-eager destinations render through <ViewBoundary>, and lib/lazyViews warms
// ALL of them in the background right after first paint — see the effect below.
// The rest of this table's routing notes are unchanged:
//   - "tonight" is an informational shell (like Atlas/Monitor), deliberately NOT
//     in GATED below: "what's up tonight?" is exactly the question a user asks
//     BEFORE any equipment is connected.
//   - "report" IS a primary-nav entry as of review #13 (it was previously
//     phone-overflow-only, which measured as "invisible on tablet AND desktop").
//     Still also reachable from the run-complete "View session report →" link
//     (SequenceView) and the mobile overflow sheet (NavMoreSheet).
//   - "help" IS a primary-nav entry as of the QA re-entry fix (it was previously
//     phone-overflow-only, i.e. invisible on tablet AND desktop, and it is now
//     the home of the setup guide's only durable re-entry point). Still also
//     reached from NavMoreSheet, the log drawer footer, and error deep-links
//     (store.openHelp) (NOV-9).
const EAGER_VIEWS: Partial<Record<ViewName, () => JSX.Element>> = {
  connect: EquipmentView,
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

// The build actually running on the scope, beside the wordmark.
//
// It used to appear in exactly one place — Settings → Update — which is the
// wrong place for it: "what version am I on?" is asked when something looks
// wrong, and the answer sat three taps deep behind a panel about installing a
// DIFFERENT version. It is also the first thing worth stating when reporting a
// bug, or when checking whether a deploy actually took (2026-07-30: a stale
// bundle served for hours because nothing on screen contradicted it).
//
// Sourced from /healthz, the one UNAUTHENTICATED route, so the chip fills in
// before sign-in and while the WebSocket is down — which is exactly when the
// question gets asked. Failure is silent: an unreachable server has louder
// problems than a missing version, and the DISPLAY DISCONNECTED banner is
// already saying so.
function ServerVersion(): JSX.Element | null {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => { if (alive && h?.version) setVersion(String(h.version)); })
      .catch(() => { /* silent — see above */ });
    return () => { alive = false; };
  }, []);
  if (!version) return null;
  return (
    <span
      className="mono text-[10px] text-faint select-text shrink-0"
      title={`AstroDeck server version ${version}`}
    >
      v{version}
    </span>
  );
}

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

  // --------------------------------------------------------- auth gate (#117)
  // ONE value naming which screen is actually on the glass, computed in the SAME
  // order the three returns below render them (splash first, then Login, then
  // the console) — so the store's copy can never claim the console is up while a
  // gate screen is. Published to the store because the things that must not
  // speak over a gate are not all in this file: confirmDialog() is called from
  // views, effects and the store itself, and re-deriving the gate at each of
  // those sites is how the weather alert came to fire over the login screen in
  // the first place (lib/authGate.ts).
  //
  // ORDER IS LOAD-BEARING — keep this effect above the others in this file.
  // React runs a component's effects in declaration order, so publishing the
  // gate first means (a) in a commit where the gate engages, the rig state is
  // already dropped before any later effect here can read it, and (b) at boot
  // the gate is known before connectWs() below has even opened the transport
  // that carries rig data.
  const splashUp = (authMethods === null || authResolving) && !authGraceElapsed;
  const authGate: AuthGate = splashUp ? "resolving" : showLogin ? "login" : "open";
  const setAuthGate = useStore((s) => s.setAuthGate);
  useEffect(() => {
    setAuthGate(authGate);
  }, [authGate, setAuthGate]);

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

  // Re-whole the app immediately after first paint. The views are code-split for
  // first-paint cost (the tablet is often on weak field WiFi or a phone hotspot),
  // but a chunk that is still missing when the user taps "Guide" at 2am is a far
  // worse outcome than a slightly slower start — so every split chunk is pulled in
  // the background on idle, one at a time, before anything is tapped. From then on
  // view switching is a pure cache hit and the app behaves exactly like the
  // single-bundle build.
  //
  // The link probe is not cosmetic: a browser permanently caches a FAILED dynamic
  // import for the life of the document, so each chunk gets exactly one attempt.
  // Spending it before the websocket is even up would poison that view for the
  // whole session. lazyViews holds the sweep until this reports healthy (with its
  // own fail-open timeout). Read via getState() inside a callback so App does not
  // re-subscribe or re-run this effect on link churn. See lib/lazyViews.ts.
  useEffect(() => {
    preloadAllViews(() => useStore.getState().wsPhase === "up");
  }, []);

  const Active = EAGER_VIEWS[view];
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
  // the confirm host stay mounted: Login's own errors render inline, but the
  // hosts are there the instant the gate lifts.
  //
  // What keeps the rig out of those two mounted overlays is NOT this file — it
  // is three guards in the store, named here so the claim is checkable rather
  // than asserted (#117): pushConfirm and enqueueToast both refuse under
  // announcementsBlocked, so neither host can be handed anything; handleEvent
  // refuses under intakeBlocked, so the frames that feed them are not taken in
  // at all; and setAuthGate drops what the live session left behind. The version
  // of this comment that just said "nothing the rig has to say gets through"
  // was itself the bug being fixed — a component reporting a guarantee it did
  // not have.
  //
  // High-cloud night warning popup (weather spec §12): fires once per server-
  // side once-per-night latch (weatherAlertKey bumps only on the alert
  // null -> non-null edge, store §9). Acknowledge-only (mode "ok",
  // ConfirmDialog.tsx:140-144) — the user-required hard notice; the
  // ignore-tonight override lives on the Sky Conditions / Sessions cards, not
  // in this dialog.
  //
  // #117: this effect used to be justified by "non-holders never receive weather
  // events (spec §8), so this can never fire for them". That is true of RBAC for
  // an authenticated principal and says nothing about an UNAUTHENTICATED one:
  // the weather was delivered while the session was live, the gate engaged
  // afterwards (expiry / auth-epoch bump / relay reconnect), and this
  // unconditional hook then read it out onto the login screen — peak cloud,
  // hours, and the operator's threshold, i.e. "there is an observatory here and
  // this is its local sky tonight". The gate check is the direct statement of
  // that rule at the site that broke it; the store's clear is what makes it hold
  // for the next effect somebody adds here.
  //
  // THE GATE IS A DEPENDENCY, not just a guard. weatherAlertKey is a latch that
  // bumps once per alert edge, so an alert consumed while a gate screen was up
  // used to be eaten for good: the key moved 0 -> 1, this effect returned early,
  // and it never changed again — the operator signed in and was told nothing
  // about a warning the spec calls mandatory. The boot splash makes that a real
  // sequence (a `weather` frame inside the first 4s is held by the store but
  // must not be spoken). Listing the gate here means the effect re-runs the
  // moment it lifts and delivers the notice it was holding. It cannot double-
  // fire: the only other transition into "open" is from "login", and that path
  // ran clearedRigState, so the key is back at 0.
  const weatherAlertKey = useWeatherAlertKey();
  const gate = useAuthGate();
  useEffect(() => {
    if (weatherAlertKey === 0) return;
    if (announcementsBlocked(gate)) return;
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
  }, [weatherAlertKey, gate]);

  // #117 — re-learn the sky when the sign-in screen goes away. While the login
  // gate is up the store refuses rig telemetry outright (store.handleEvent /
  // intakeBlocked), so a `weather` frame that arrived in that window was dropped,
  // not deferred. Everything else the console needs comes back on the post-login
  // reconnect (ws.onopen re-hydrates config, the log history, the monitor
  // snapshot) — weather is the one slice nothing re-fetches, and the server
  // republishes it only every 15 minutes. Without this, an operator who signed
  // back in at 22:05 could sit until 22:20 before being told about a high-cloud
  // window that had already started.
  //
  // Only on login -> open, the exact span where intake was refused: firing it on
  // the boot splash's resolving -> open would be a second cold GET for a slice
  // the socket is about to deliver anyway. Cap-gated the same way
  // SkyConditionsPanel is, so a non-holder never ISSUES the request (weather
  // spec §8) rather than issuing it and eating a 403; fail-quiet regardless,
  // because "this session cannot see weather" is a correct answer, not an error
  // worth a toast.
  const canViewWeather = useCanViewWeather();
  const prevGate = useRef<AuthGate>(gate);
  useEffect(() => {
    const was = prevGate.current;
    prevGate.current = gate;
    if (!(was === "login" && gate === "open")) return;
    if (!canViewWeather) return;
    let live = true;
    void getWeather()
      .then((w) => {
        if (!live) return;
        useStore.getState().handleEvent({
          type: "weather",
          data: w as unknown as Record<string, unknown>,
          ts: Date.now() / 1000,
        });
      })
      .catch(() => { /* offline, or the cap went away — never a toast here */ });
    return () => { live = false; };
  }, [gate, canViewWeather]);

  // `splashUp` (not a second copy of the condition) — the store's gate value is
  // derived from the same boolean, so "what is on screen" and "what the store
  // thinks is on screen" are the same expression (#117).
  if (splashUp) {
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
        {/* `app-header` is the hook for CSS-HEADERSET in index.css (review #47):
            the three icon buttons in the right-hand cluster measured 44x44 r0 /
            36x44 r10 / 36x44 r10 — an obviously-a-set that wasn't one, with two
            of the three below the 44px touch minimum. The class is deliberately
            narrow so the rule cannot reach the <header> elements inside Panel or
            the overlay heads, which are text rows rather than icon clusters. */}
        <header className="app-header relative z-20 flex items-center gap-3 px-4 h-12 border-b border-line bg-raise/70 backdrop-blur shrink-0">
          <h1 className="font-display font-semibold tracking-[0.3em] text-accent text-sm select-none">
            ASTRO<span className="text-ink">DECK</span>
          </h1>
          <ServerVersion />
          {backendBadge(status?.mode) && (
            /* Not a ternary defaulting to SIM. A rig built from per-role
               hardware drivers reports its first session's backend name
               ("zwo-usb"), which matched no branch and fell through to the
               default — so a fully real, tracking rig was badged SIMULATOR on
               the one chip that says whether commands reach the sky. */
            <span className={`hidden sm:inline px-2 py-0.5 border text-[9px]
              tracking-[0.18em] uppercase font-display font-medium ${
                backendBadgeIsSim(status?.mode)
                  ? "border-warn text-warn"
                  : "border-line2 text-accent"
              }`}
              title={`Backend: ${status?.mode}`}>
              {backendBadge(status?.mode)}
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
                  // Belt-and-braces warm-up for the seconds between first paint and
                  // preloadAllViews() finishing: a hovered/pressed tab starts its
                  // own chunk immediately. No-op once the chunk is in the registry,
                  // and a no-op entirely for eagerly bundled views.
                  onPointerEnter={() => preloadView(n.id)}
                  onPointerDown={() => preloadView(n.id)}
                  aria-current={view === n.id ? "page" : undefined}
                  // #24: `title` never fires on touch, and this rail lives on a
                  // tablet. The reason rides in the accessible NAME (the lock
                  // glyph below is the visible half of the same cue); title is
                  // kept as the mouse convenience it always was, never the only
                  // channel. The tab still navigates — to the interstitial,
                  // which states the same thing at full size.
                  aria-label={gated ? `${n.label} — connect equipment to use this` : undefined}
                  title={gated ? "Connect equipment to use this" : undefined}
                  // py-2, not py-3. MEASURED on this tree at 1440x900 — the
                  // "morning-after machine", the shortest viewport this rail
                  // renders on: 13 entries at py-3 came to scrollHeight 815
                  // against clientHeight 818, i.e. the rail was already one
                  // entry from overflowing, and appending Help pushed it to 877
                  // — the last item (HELP) sat BELOW the rail's own fold, on a
                  // scroll region with no visible affordance. A nav entry a user
                  // has to discover by scrolling the nav is not reachable, which
                  // would have re-created the exact defect this change exists to
                  // fix. py-2 brings 14 entries to 765 < 818 and every item
                  // still stands ~53px tall, above the 44px floor. (A rotated
                  // tablet, 1180x820, is tighter still and scrolls — it did
                  // before this change too, at 13 entries.)
                  className={`flex flex-col items-center gap-1 py-2 transition-colors relative cursor-pointer
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
            // `main-safe-pad` replaces `pb-20 sm:pb-4` (review #46): the bottom
            // padding has to clear the fixed bottom nav AND the home-indicator
            // inset, or the last row of a view is unreachable on a notched
            // phone. It is authored CSS on purpose — a Tailwind `pb-*` utility
            // would be indistinguishable from the `p-4` on the same element.
            className={`flex-1 overflow-y-auto overflow-x-hidden p-4 main-safe-pad outline-none ${dim ? "opacity-60 transition-opacity" : "transition-opacity"}`}
            key={view}
          >
            <div className="view-enter max-w-[1500px] mx-auto w-full min-h-full flex flex-col">
              {gatedOut ? (
                <NotConnectedInterstitial view={view} />
              ) : Active ? (
                <Active />
              ) : (
                <ViewBoundary view={view} />
              )}
            </div>
          </main>

          {/* Log drawer: docked column lg+, bottom sheet below — self-manages via
              store.logOpen; renders nothing when closed. It now renders through
              the shared <Overlay/> primitive, so it PORTALS to the body-level
              overlay host rather than occupying a flex slot here. It stays
              mounted at this point in the tree only because that is where its
              store subscription belongs; the DOM position is no longer load-
              bearing (and, being outside .view-enter and every .panel, is no
              longer a containing-block hazard). */}
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
            className="fixed float-safe-b left-1/2 -translate-x-1/2 sm:left-auto sm:right-3 sm:translate-x-0
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
