// NextApp.tsx - the root of the new front end (ARCHITECTURE.md section 2).
//
// One of the two roots `main.tsx` can mount; the other is the legacy `App`. Only
// ever one at a time, which is what makes it safe for both to call `connectWs()`
// and both to own the auth gate.
//
// The lifecycle effects below are LIFTED from `App.tsx` (the auth gate at
// App.tsx:293-314, the WS connect at 343-345, the resume-arm poll at 384-399,
// the weather alert at 451-469, the login -> open weather refetch at 488-507,
// the Shift+B brightness hatch). They are transcribed rather than re-invented
// because each one encodes a bug that has already been paid for once - the 4 s
// fail-open grace, the gate as a DEPENDENCY of the weather alert so a latch
// consumed behind the login screen is re-delivered, the cap gate that keeps a
// non-holder from issuing a request it would only eat a 403 for. The reasoning
// is preserved in the comments at each site; the originals are cited.
//
// What is NOT lifted: `preloadAllViews()`. The legacy views are not mounted
// under this root, so warming their chunks would spend the one attempt a failed
// dynamic import ever gets on code this root never renders.

import { useEffect, useRef, useState, type JSX } from "react";
import "./next.css";
import "./shell/shell.css";

import {
  useStore, useBrightness, useAuthMethods, useAuthGate, useWeatherAlertKey,
} from "../store";
import { connectWs } from "../ws";
import { getResumeArm } from "../api/sessions";
import { getWeather } from "../api/weather";
import { fmtHm } from "../lib/weather";
import { useMonitorWakeLock } from "../lib/useWakeLock";
import { useShouldShowLogin, useAuthResolving, useCanViewWeather } from "../lib/caps";
import { announcementsBlocked, type AuthGate } from "../lib/authGate";
import { confirmDialog } from "../components/ConfirmDialog";
import TouchGuard from "../components/TouchGuard";
import Login from "../views/Login";

import { Sheet, Wordmark } from "./ui";
import { useRoute } from "./router";
import { useBreakpoint } from "./breakpoint";
import { useLegacyBridge } from "./legacyBridge";
import { HUBS } from "./hubs";
import { Header } from "./shell/Header";
import { Banners } from "./shell/Banners";
import { CampaignStrip } from "./shell/CampaignStrip";
import { SubNavBar } from "./shell/SubNav";
import { TabBar } from "./shell/TabBar";
import { Rail } from "./shell/Rail";
import { SheetHost } from "./shell/SheetHost";
import { SessionColumn } from "./shell/SessionColumn";
import { Toasts } from "./shell/Toasts";
import { ConfirmCard } from "./shell/ConfirmCard";

/** How often the chrome recomputes "how long has this incident been going".
 *  10 s, not 1 s: the incident lines are minutes-resolution, and a second-rate
 *  re-render of the whole shell is a cost paid on a phone battery all night. */
const CLOCK_MS = 10_000;

function useShellClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

/** Register the service worker (ARCHITECTURE.md section 12, S4). Production
 *  builds only, secure contexts only: a dev-server SW caches a shell that the
 *  next `vite` restart contradicts, and a stale service worker is its own 2 a.m.
 *  failure mode. Scoped "./" so the relay's /h/<home_id>/ mount is respected. */
function useServiceWorker(): void {
  useEffect(() => {
    // `import.meta.env` as a whole, not `.PROD` directly: Vite substitutes the
    // object, and plain Node (every test in this repo) has no `env` at all, so
    // reading a property off it would throw before the guard could run.
    const env = import.meta.env;
    if (!env || !env.PROD) return;
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    if (!window.isSecureContext) return;
    navigator.serviceWorker.register("./sw.js", { scope: "./" }).catch(() => {
      /* an unregistrable SW is an offline shell we do not get, never an error
         worth interrupting the night for */
    });
  }, []);
}

export default function NextApp(): JSX.Element {
  // ------------------------------------------------------------ auth gate
  // App.tsx:280-314, transcribed. `authMethods === null` means the login signal
  // has not loaded once yet; `authResolving` means a method IS enabled and the
  // principal has not come back. Either shows the neutral splash - but only
  // until the 4 s grace elapses, so a wedged /api/me can never trap a field
  // tablet behind a spinner. Fail OPEN.
  const showLogin = useShouldShowLogin();
  const authResolving = useAuthResolving();
  const authMethods = useAuthMethods();
  const [authGraceElapsed, setAuthGraceElapsed] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setAuthGraceElapsed(true), 4000);
    return () => window.clearTimeout(t);
  }, []);

  const splashUp = (authMethods === null || authResolving) && !authGraceElapsed;
  const authGate: AuthGate = splashUp ? "resolving" : showLogin ? "login" : "open";
  const setAuthGate = useStore((s) => s.setAuthGate);

  // ORDER IS LOAD-BEARING (App.tsx's own note): React runs effects in
  // declaration order, so publishing the gate BEFORE the transport opens means
  // the store's guards (announcementsBlocked, intakeBlocked) are already in
  // place when the first frame arrives. Keep this effect first in this file.
  useEffect(() => {
    setAuthGate(authGate);
  }, [authGate, setAuthGate]);

  // ---------------------------------------------------------------- transport
  useEffect(() => {
    connectWs();
  }, []);

  useServiceWorker();

  // Hold a screen wake lock while a sequence is running or monitorAwake is on.
  useMonitorWakeLock();

  // ------------------------------------------------- brightness escape hatch
  // The in-header dimmer lives in Settings in this UI; what stays global is the
  // always-reachable reset, because a screen dimmed to 0.5 in the dark is a
  // screen whose reset button is also dimmed to 0.5.
  const brightness = useBrightness();
  const resetBrightness = useStore((s) => s.resetBrightness);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey && (e.key === "B" || e.key === "b") && !e.repeat) {
        const tag = (e.target as HTMLElement | null)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;  // do not hijack typing
        e.preventDefault();
        resetBrightness();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [resetBrightness]);

  // --------------------------------------------------------- resume-arm poll
  // App.tsx:384-399. ONE poll feeds both the banner here and the Session hub's
  // panel, so the two can never disagree about what is armed.
  const setResumeArm = useStore((s) => s.setResumeArm);
  useEffect(() => {
    if (showLogin) return;
    let live = true;
    const tick = () => {
      getResumeArm()
        .then((r) => { if (live) setResumeArm(r); })
        .catch(() => { /* older server or offline - the banner stays hidden */ });
    };
    tick();
    const id = window.setInterval(tick, 20000);
    return () => { live = false; window.clearInterval(id); };
  }, [showLogin, setResumeArm]);

  // ------------------------------------------------------ high-cloud notice
  // App.tsx:451-469. Fires once per server-side latch. THE GATE IS A DEPENDENCY,
  // not just a guard: weatherAlertKey bumps once per alert edge, so an alert
  // that arrived while a gate screen was up would otherwise be eaten for good -
  // the key moves 0 -> 1, the effect returns early, and it never changes again.
  // Listing the gate means the effect re-runs the moment it lifts and delivers
  // the notice it was holding.
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
        `dominant) between ${fmtHm(a.start_iso)} and ${fmtHm(a.end_iso)} - ` +
        `at or above your ${w.threshold_pct}% threshold. Auto-resume will hold ` +
        `unless "ignore weather tonight" is set.`,
      tone: "warn",
      mode: "ok",
    });
  }, [weatherAlertKey, gate]);

  // ------------------------------------------- re-learn the sky after sign-in
  // App.tsx:488-507. While the login gate is up the store REFUSES rig telemetry
  // (intakeBlocked), so a `weather` frame in that window was dropped, not
  // deferred - and weather is the one slice the reconnect does not re-fetch,
  // with the server republishing only every 15 minutes. Only on login -> open,
  // cap-gated so a non-holder never issues the request, fail-quiet either way.
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
      .catch(() => { /* offline, or the cap went away - never a toast here */ });
    return () => { live = false; };
  }, [gate, canViewWeather]);

  // ------------------------------------------------------------------ chrome
  const bp = useBreakpoint();
  const route = useRoute();
  const nowMs = useShellClock();
  useLegacyBridge();

  const phone = bp === "phone";

  // The gate screens keep the toast stack and the confirm card mounted, exactly
  // as App does: what stops the rig speaking over them is the store's own
  // guards, not this file declining to render the hosts.
  if (splashUp) {
    return (
      <div className="nx-app" data-bp={bp} data-testid="next-app">
        <div className="nx-col">
          <div className="nx-gate" data-testid="next-splash">
            <Wordmark />
            <span className="nx-gate-note">connecting to the rig</span>
          </div>
        </div>
        <Toasts bp={bp} />
        <ConfirmCard />
      </div>
    );
  }

  if (showLogin) {
    return (
      <div className="nx-app" data-bp={bp} data-testid="next-app">
        <div className="nx-col">
          <div className="nx-gate" data-testid="next-login">
            <Wordmark />
            <div className="nx-gate-sheet">
              {/* A sheet with no BACK: there is nowhere behind a sign-in screen
                  to go. `views/Login.tsx` is reused whole - only its container
                  changes. */}
              <Sheet title="SIGN IN" sub="this rig asks who you are">
                <Login />
              </Sheet>
            </div>
          </div>
        </div>
        <Toasts bp={bp} />
        <ConfirmCard />
      </div>
    );
  }

  const Hub = HUBS[route.hub];
  // The desktop's Session column is the running night, always visible - except
  // on the Session hub itself, which IS that screen at full size.
  const showSessionColumn = bp === "desktop" && route.hub !== "session";

  return (
    <div className="nx-app" data-bp={bp} data-testid="next-app">
      {!phone && <Rail route={route} nowMs={nowMs} />}

      <div className="nx-col">
        <Header />
        <Banners route={route} nowMs={nowMs} />
        <CampaignStrip route={route} />
        <SubNavBar route={route} nowMs={nowMs} />
        <main className="nx-body" id="nx-main" data-testid="hub-body">
          <Hub />
        </main>
        {phone && <TabBar route={route} nowMs={nowMs} />}
      </div>

      <SheetHost route={route} phone={phone} />
      {showSessionColumn && <SessionColumn />}

      <Toasts bp={bp} />
      <ConfirmCard />

      {/* The screen lock. Self-managing: it reads its own slices and renders
          nothing until it is armed. */}
      <TouchGuard />

      {/* The brightness reset, always reachable at full opacity even when the
          rest of the app is dimmed to the floor. */}
      {brightness < 0.95 && (
        <button
          type="button"
          className="nx-bright-reset"
          onClick={resetBrightness}
          data-testid="brightness-reset"
        >
          BRIGHTNESS {Math.round(brightness * 100)}% - RESET
        </button>
      )}

      {/* Popovers portal here, so a `backdrop-filter` on a card can never become
          their containing block. */}
      <div id="nx-popover-root" />
    </div>
  );
}
