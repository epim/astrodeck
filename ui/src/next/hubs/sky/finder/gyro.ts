// gyro.ts - the finder follows the phone (hub-sky plan B.10).
//
// Three things the prototype does not do, all of them required by the README's
// "Web capabilities used" line and all of them the difference between a compass
// that works and one that points somewhere plausible:
//
//   1. `deviceorientationabsolute` FIRST, `deviceorientation` as the fallback.
//      On Chrome/Android only the absolute event carries a true-north heading;
//      the relative one drifts from wherever the page happened to start.
//   2. `screen.orientation.angle` is SUBTRACTED. Held in landscape the device
//      frame is rotated 90 degrees from the screen, and without this the sky
//      swings a quarter turn the moment the phone is turned on its side.
//   3. A SECURE CONTEXT is required before anything is attached at all. Over
//      plain-HTTP LAN the events either never fire or arrive without a heading,
//      and a GYRO button that silently does nothing is worse than one that says
//      why.
//
// iOS needs `DeviceOrientationEvent.requestPermission()` from a user gesture and
// exposes the true heading as `webkitCompassHeading`. Everything else derives it
// from `alpha`, which is measured anticlockwise from east-of-north, hence
// `360 - alpha`.
//
// Every failure path produces a SENTENCE. There is no state in which the button
// is pressed and nothing at all happens.

export const NO_SENSOR = "No orientation sensor here - drag the sky to pan.";
export const PERMISSION_DENIED = "Motion permission denied - drag to pan.";
export const NO_DATA =
  "No sensor data arrived - on a phone the view follows the compass. Drag to pan.";
export const NEEDS_SECURE =
  "AR camera and gyro need a secure connection - set up in Connection";

/** How long to wait for a first event before giving up and saying so. */
export const WATCHDOG_MS = 1500;

interface OrientationEventLike {
  alpha: number | null;
  beta: number | null;
  gamma: number | null;
  webkitCompassHeading?: number;
  absolute?: boolean;
}

type PermissionCapable = {
  requestPermission?: () => Promise<"granted" | "denied" | "default">;
};

function isSecure(): boolean {
  return typeof window !== "undefined" && window.isSecureContext === true;
}

/** Can the gyro be armed at all, and if not, why not - in one sentence. */
export function gyroSupport(): { ok: boolean; reason: string | null } {
  if (typeof window === "undefined") return { ok: false, reason: NO_SENSOR };
  if (typeof (window as unknown as { DeviceOrientationEvent?: unknown }).DeviceOrientationEvent === "undefined") {
    return { ok: false, reason: NO_SENSOR };
  }
  if (!isSecure()) return { ok: false, reason: NEEDS_SECURE };
  return { ok: true, reason: null };
}

/** Screen rotation to subtract from a device heading, degrees. */
function screenAngle(): number {
  const so = (typeof window !== "undefined"
    ? (window.screen as unknown as { orientation?: { angle?: number } } | undefined)?.orientation
    : undefined);
  const a = so?.angle;
  return typeof a === "number" && Number.isFinite(a) ? a : 0;
}

/** Heading (degrees east of north) from one orientation event, or null when the
 *  event carries no usable compass at all. */
export function headingOf(e: OrientationEventLike): number | null {
  const raw =
    typeof e.webkitCompassHeading === "number" && Number.isFinite(e.webkitCompassHeading)
      ? e.webkitCompassHeading
      : e.alpha != null && Number.isFinite(e.alpha)
        ? 360 - e.alpha
        : null;
  if (raw == null) return null;
  return (((raw - screenAngle()) % 360) + 360) % 360;
}

/** Altitude from the device's pitch. `beta - 45` is the prototype's mapping: a
 *  phone held at 45 degrees looks at the horizon, upright looks up. */
export function altitudeOf(e: OrientationEventLike, current: number): number {
  if (e.beta == null || !Number.isFinite(e.beta)) return current;
  return Math.max(-12, Math.min(89, e.beta - 45));
}

export interface GyroHandle {
  stop(): void;
}

export interface GyroCallbacks {
  onView: (v: { az: number; alt: number }) => void;
  /** Current altitude, read at event time when the device sends no pitch. */
  currentAlt: () => number;
  onError: (message: string) => void;
}

/**
 * Attach the orientation listener, asking iOS for permission first when it wants
 * to be asked. Returns a handle whose `stop()` is idempotent; call it on unmount,
 * on leaving the hub, and whenever a sheet takes the screen.
 *
 * Returns `null` (having already called `onError`) when the gyro cannot be armed.
 */
export function startGyro(cb: GyroCallbacks): GyroHandle | null {
  const support = gyroSupport();
  if (!support.ok) {
    cb.onError(support.reason ?? NO_SENSOR);
    return null;
  }

  let stopped = false;
  let attachedEvent: string | null = null;
  let handler: ((e: Event) => void) | null = null;
  let watchdog: ReturnType<typeof setTimeout> | null = null;
  let got = false;

  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    if (watchdog != null) { clearTimeout(watchdog); watchdog = null; }
    if (handler && attachedEvent) window.removeEventListener(attachedEvent, handler);
    handler = null;
    attachedEvent = null;
  };

  const attach = (): void => {
    if (stopped) return;
    handler = (raw: Event) => {
      const e = raw as unknown as OrientationEventLike;
      const head = headingOf(e);
      if (head == null) return;
      got = true;
      cb.onView({ az: head, alt: altitudeOf(e, cb.currentAlt()) });
    };
    // Absolute first: it is the only one with a true-north reference on Android.
    attachedEvent =
      "ondeviceorientationabsolute" in window ? "deviceorientationabsolute" : "deviceorientation";
    window.addEventListener(attachedEvent, handler);
    watchdog = setTimeout(() => {
      watchdog = null;
      if (!got && !stopped) {
        stop();
        cb.onError(NO_DATA);
      }
    }, WATCHDOG_MS);
  };

  const DOE = (window as unknown as { DeviceOrientationEvent: PermissionCapable }).DeviceOrientationEvent;
  if (typeof DOE?.requestPermission === "function") {
    DOE.requestPermission()
      .then((r) => {
        if (r === "granted") attach();
        else cb.onError(PERMISSION_DENIED);
      })
      .catch(() => cb.onError(PERMISSION_DENIED));
  } else {
    attach();
  }

  return { stop };
}
