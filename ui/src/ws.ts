import { useStore } from "./store";
import { api } from "./api";
import type { LogLine } from "./types";

let socket: WebSocket | null = null;
let retryMs = 1000;
let everConnected = false;
let staleTicker: number | null = null;

const STALE_MS = 20000; // socket up but no frame for 20s AND not busy

export function connectWs(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const s = useStore.getState();
  s.setWsPhase(everConnected ? "reconnecting" : "connecting");
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.onopen = async () => {
    retryMs = 1000;
    everConnected = true;
    const st = useStore.getState();
    st.setWsPhase("up");
    st.noteWsEvent();
    if (!staleTicker) staleTicker = window.setInterval(tickStale, 1000);
    // Hydrate config at boot (and re-hydrate after reconnect) so settings-derived
    // UI isn't blank/defaults until a config mutation. Fire-and-forget.
    void st.loadConfig();
    // Reconcile after any gap: the bus drops frames under backpressure, so the
    // drawer/badge could be missing log lines that arrived while we were away.
    try {
      st.reconcileLogs(await api.get<LogLine[]>("/api/logs"));
    } catch {
      /* ignore */
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
    setTimeout(connectWs, retryMs);
    retryMs = Math.min(retryMs * 1.7, 15000);
  };

  socket.onerror = () => socket?.close();
}

function tickStale(): void {
  const s = useStore.getState();
  if (s.wsPhase !== "up") return;
  // Stale ONLY if the socket is up, frames stopped, AND the backend is not in a
  // known long op (slew/solve/AF/capture legitimately block the 2s poll).
  const busy = s.status?.busy ?? null;
  const stale = busy === null && Date.now() - s.wsLastEvent > STALE_MS;
  if (s.telemetryStale !== stale) s.setTelemetryStale(stale);
}
