// notify.ts — opt-in push (Web Notification) + short audible beep for
// sequence error/complete and sustained link loss (reliability spec §11).
// No-ops unless the user opted in AND the browser granted permission.

let audioCtx: AudioContext | null = null;

/** Ask the browser for Notification permission. Resolves true if granted. */
export async function requestNotifyPermission(): Promise<boolean> {
  if (typeof Notification === "undefined") return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  try {
    const res = await Notification.requestPermission();
    return res === "granted";
  } catch {
    return false;
  }
}

function beep(): void {
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtx) audioCtx = new Ctx();
    const ctx = audioCtx;
    if (ctx.state === "suspended") void ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 660;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.26);
  } catch {
    /* audio unavailable — ignore */
  }
}

/**
 * Fire a Web Notification + beep when notifications are enabled and permitted.
 * `state` is anything carrying a `notifyEnabled` flag (the store), so this stays
 * decoupled from the store module (avoids an import cycle).
 */
export function notifyAndBeep(
  state: { notifyEnabled: boolean },
  title: string,
  body: string,
): void {
  if (!state.notifyEnabled) return;
  if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    try {
      new Notification(title, { body, tag: "astrodeck", silent: false });
    } catch {
      /* construction can throw on some platforms — ignore */
    }
  }
  beep();
}
