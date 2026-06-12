import { useStore } from "./store";

let socket: WebSocket | null = null;
let retryMs = 1000;

export function connectWs(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.onopen = () => {
    retryMs = 1000;
    useStore.getState().setWsConnected(true);
  };

  socket.onmessage = (msg) => {
    try {
      useStore.getState().handleEvent(JSON.parse(msg.data));
    } catch { /* malformed frame — ignore */ }
  };

  socket.onclose = () => {
    useStore.getState().setWsConnected(false);
    setTimeout(connectWs, retryMs);
    retryMs = Math.min(retryMs * 1.7, 15000);
  };

  socket.onerror = () => socket?.close();
}
