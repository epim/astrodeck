// camera.ts - the AR passthrough behind the finder (hub-sky plan B.10).
//
// The prototype fakes this entirely (it draws a CSS star field). This is the real
// thing: `getUserMedia` on the rear camera, streamed into a `<video>` filling the
// box, with the design's inset vignette over it so the markers stay readable
// against a bright sky.
//
// EVERY FAILURE IS A SENTENCE, and every failure falls back to MAP. A black box
// with no explanation is the outcome this file exists to prevent - the three
// causes (denied, no camera, insecure origin) need three different actions from
// the user, so they get three different strings.
//
// THE TRACK IS ALWAYS RELEASED. A camera left running is a recording light left
// on and a battery gone by midnight, so `stopCamera` is called on unmount, on
// leaving the Sky hub, and on `visibilitychange` to hidden.

export const NEEDS_SECURE =
  "AR camera and gyro need a secure connection - set up in Connection";
export const DENIED = "Camera permission denied - MAP mode still works.";
export const NOT_FOUND = "No camera on this device - MAP mode still works.";
export const NO_API = "No camera API in this browser - MAP mode still works.";

interface MediaDevicesLike {
  getUserMedia(constraints: unknown): Promise<MediaStream>;
}

function mediaDevices(): MediaDevicesLike | null {
  if (typeof navigator === "undefined") return null;
  const md = (navigator as unknown as { mediaDevices?: MediaDevicesLike }).mediaDevices;
  return md && typeof md.getUserMedia === "function" ? md : null;
}

/** Can the AR camera be armed at all, and if not, why not - in one sentence. */
export function cameraSupport(): { ok: boolean; reason: string | null } {
  if (typeof window === "undefined") return { ok: false, reason: NO_API };
  // The secure-context check comes FIRST: on a plain-HTTP origin `mediaDevices`
  // is simply absent, and reporting that as "no camera API in this browser"
  // would send the user hunting for a different browser instead of a
  // certificate.
  if (window.isSecureContext !== true) return { ok: false, reason: NEEDS_SECURE };
  if (mediaDevices() == null) return { ok: false, reason: NO_API };
  return { ok: true, reason: null };
}

/** Map a getUserMedia rejection to the sentence that names the user's next move. */
export function cameraErrorMessage(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") return DENIED;
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") {
    return NOT_FOUND;
  }
  const msg = (e as { message?: string } | null)?.message;
  return msg && msg.trim() !== ""
    ? `The camera did not start (${msg}). MAP mode still works.`
    : NO_API;
}

/**
 * Open the rear camera. `facingMode: { ideal: "environment" }` and not `exact`:
 * on a laptop or a phone with only a front camera, `exact` rejects outright and
 * the user gets an error where a usable (if front-facing) stream would have done.
 */
export async function startCamera(): Promise<MediaStream> {
  const md = mediaDevices();
  if (!md) throw new Error(NO_API);
  return md.getUserMedia({
    video: { facingMode: { ideal: "environment" } },
    audio: false,
  });
}

/** Release every track. Safe to call on null, twice, or after the tracks ended. */
export function stopCamera(stream: MediaStream | null | undefined): void {
  if (!stream) return;
  try {
    for (const t of stream.getTracks()) t.stop();
  } catch {
    /* a stream already torn down by the browser */
  }
}
