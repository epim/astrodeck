import { useStore } from "./store";
import { api } from "./api";
import { BASE } from "./lib/base";
import { HARD_STALE_MS, computeTelemetryStale } from "./lib/telemetry";
import type { LogLine, MonitorSnapshot } from "./types";

let socket: WebSocket | null = null;
let retryMs = 1000;
let everConnected = false;
let staleTicker: number | null = null;
let reconnectTimer: number | null = null;

const STALE_MS = 20000; // socket up but no frame for 20s AND not busy

// Resolve the auth signals that decide the login gate — INDEPENDENTLY of the WS.
// ROOT CAUSE of H1: these used to live ONLY in socket.onopen. But the WS upgrade
// is rejected (close 1008 before accept) the instant a sign-in method is enabled
// and this client has no session, so onopen never fires. With the signals trapped
// there, `authMethods`/`principal` stayed null, shouldShowLogin failed OPEN, and
// the app rendered the operational shell + a "DISPLAY DISCONNECTED" banner instead
// of a sign-in form. Fetching them here (at every connect attempt AND on every
// drop) lets the client learn "auth required" via /api/me → 401 even though the
// socket never opens. loadPrincipal pins a viewer sentinel on 401; loadAuthMethods
// serves Login the enabled-method truth. Both are best-effort (a network error
// leaves the prior value, so a genuine link-down never strips the gate).
function bootstrapAuth(): void {
  const st = useStore.getState();
  void st.loadAuthMethods();
  void st.loadPrincipal();
}

export function connectWs(): void {
  // We're connecting now — drop any scheduled retry so a manual/post-login kick
  // (reconnectWs) or a fresh boot can't stack a second socket behind a timer.
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  // Discover auth state up front so the Login gate can render even when the WS
  // handshake is about to be rejected (auth required, no session) — see above.
  bootstrapAuth();

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const s = useStore.getState();
  s.setWsPhase(everConnected ? "reconnecting" : "connecting");
  socket = new WebSocket(`${proto}://${location.host}${BASE}/ws`);

  socket.onopen = async () => {
    retryMs = 1000;
    everConnected = true;
    const st = useStore.getState();
    st.setWsPhase("up");
    st.noteWsEvent();
    if (!staleTicker) staleTicker = window.setInterval(tickStale, 1000);
    // Hydrate config at boot (and re-hydrate after reconnect) so settings-derived
    // UI isn't blank/defaults until a config mutation. Fire-and-forget. (The
    // principal + auth-methods signals are already resolved by bootstrapAuth at
    // the top of connectWs, so they are current for this now-open socket.)
    void st.loadConfig();
    // Refresh the principal on a successful (re)connect too: a post-login
    // reconnect carries the new session cookie, so /api/me now resolves the
    // signed-in identity and the role gates flip live.
    void st.loadPrincipal();
    // Hydrate the self-update snapshot (current/latest/availability). Cheap GET;
    // thereafter the `update` WS event keeps it live. Fail-quiet.
    void st.loadUpdate();
    // Reconcile after any gap: the bus drops frames under backpressure, so the
    // drawer/badge could be missing log lines that arrived while we were away.
    try {
      st.reconcileLogs(await api.get<LogLine[]>("/api/logs"));
    } catch {
      /* ignore */
    }
    // Rehydrate status/sequence from the monitor snapshot on every (re)connect.
    // `hello` (hub.summary()) carries no `sequence` key, and terminal sequence
    // transitions (complete/aborted/error) publish exactly once on the bus with
    // no history — if the socket was down when one fired, the store's `sequence`
    // slice is stuck on the last state it saw (e.g. RUNNING) forever. Reuse the
    // same cold-load path MonitorView uses, routed through handleEvent so the
    // WS path stays the single source of truth for how state gets applied.
    try {
      const snap = await api.get<MonitorSnapshot>("/api/monitor/snapshot");
      const ts = Date.now() / 1000;
      if (snap.status) st.handleEvent({ type: "status", data: snap.status as unknown as Record<string, unknown>, ts });
      if (snap.sequence) st.handleEvent({ type: "sequence", data: snap.sequence as unknown as Record<string, unknown>, ts });
      // Polar, for the same reason as sequence above. The aligner's terminal
      // states are its most important ones — "too close to the pole to measure",
      // an error, a finished measurement — and each publishes exactly once with
      // no bus history. A reload put the panel back on the cold default: an idle
      // aligner, no numbers, no reason, and a user who re-runs the run that had
      // just refused. Routed through handleEvent so the WS path stays the single
      // place that decides how this state is applied.
      if (snap.polar) st.handleEvent({ type: "polar", data: snap.polar as unknown as Record<string, unknown>, ts });
      // Focus has the SAME failure mode the sequence rehydration above exists
      // for, and it bit a real session on 2026-07-30: the sweep failed at
      // 23:13, the phone kept showing "measuring…" until Halt at 23:52, and
      // Halt appeared to do nothing because there was nothing left to halt.
      // `busy` is the server's truth about what is actually running, so a local
      // "running" that the server does not corroborate is stale. Cleared to
      // null rather than marked failed: the sweep may well have SUCCEEDED while
      // we were disconnected, and inventing an outcome is worse than showing
      // none. `lastAutofocusResult` still holds the last real result.
      if (Array.isArray(snap.busy) && !snap.busy.includes("autofocus")) {
        if (useStore.getState().focus?.state === "running") {
          useStore.setState({ focus: null });
        }
      }
    } catch {
      /* ignore — WS status polling will catch up within ~2s */
    }
  };

  socket.onmessage = (msg) => {
    try {
      const st = useStore.getState();
      st.noteWsEvent(); // stamp BEFORE handling so a throw still counts liveness
      st.handleEvent(JSON.parse(msg.data));
    } catch {
      /* malformed frame — ignore */
    }
  };

  socket.onclose = () => {
    const st = useStore.getState();
    st.setWsPhase("down");
    if (staleTicker) {
      clearInterval(staleTicker);
      staleTicker = null;
    }
    // Re-check auth on EVERY drop (H1 §2d/2e). An admin enabling a sign-in method
    // live-flips the provider; the server then closes this socket (4401 re-auth,
    // then 1008 on the retry) — and a browser can't read that close code as
    // "auth". Re-resolving /api/me (now 401) + /api/auth/methods routes the tab to
    // the Login gate instead of leaving it on a bare DISPLAY DISCONNECTED banner.
    bootstrapAuth();
    reconnectTimer = window.setTimeout(connectWs, retryMs);
    retryMs = Math.min(retryMs * 1.7, 15000);
  };

  socket.onerror = () => socket?.close();
}

// Force an IMMEDIATE (re)connect after a credential change — a login/logout, or
// an admin flipping the enabled sign-in methods. Resets the exponential backoff
// and tears down any pending retry + the current socket so a fresh session lands
// telemetry at once instead of after up to 15s of backoff. Handlers on the old
// socket are detached first so its deliberate close() does NOT run the onclose
// reconnect path (which would stack a second socket).
export function reconnectWs(): void {
  retryMs = 1000;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    try {
      socket.close();
    } catch {
      /* already closing/closed */
    }
    socket = null;
  }
  connectWs();
}

function tickStale(): void {
  const s = useStore.getState();
  if (s.wsPhase !== "up") return;
  // Stale ONLY if a rig is connected, the socket is up, frames stopped, AND the
  // backend is not in a known long op (slew/solve/AF/capture legitimately block
  // the 2s poll) — EXCEPT past HARD_STALE_MS, where nothing legitimises the
  // quiet. See lib/telemetry.ts: `busy` is read from the last message received,
  // so an outage that starts mid-capture freezes it at "capture" and used to
  // disarm this check permanently.
  const ageMs = Date.now() - s.wsLastEvent;
  const stale = computeTelemetryStale({
    connected: s.equipConnected,
    busy: s.status?.busy ?? null,
    ageMs,
    staleMs: STALE_MS,
  });
  if (s.telemetryStale !== stale) s.setTelemetryStale(stale);

  // A SOCKET THAT HAS SAID NOTHING FOR THIS LONG IS NOT A SLOW SOCKET.
  //
  // `readyState === OPEN` is not evidence that anything is on the other end: a
  // relay restart, a sleeping tab's resumed-but-orphaned connection, or a NAT
  // idle-timeout all leave a half-open socket that reads OPEN forever and
  // delivers nothing. Nobody else will notice, because every other reconnect
  // path is driven by onclose — the event a zombie never fires.
  //
  // So tear it down and let the normal retry ladder rebuild it. Bounded by
  // `zombieKickedAt` to one kick per outage: reconnect resets wsLastEvent, so
  // without the latch a link that comes back empty would be kicked every second.
  if (ageMs > HARD_STALE_MS && Date.now() - zombieKickedAt > HARD_STALE_MS) {
    zombieKickedAt = Date.now();
    reconnectWs();
  }
}

/** When the zombie-socket kick last fired, so one outage costs one teardown. */
let zombieKickedAt = 0;
