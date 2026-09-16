// photosphere.ts - CAPTURE PHOTOSPHERE (plan A.15 / B.14): a getUserMedia
// video sweep, binned into azimuth columns of per-row luminance, handed to
// `next/lib/horizonModel.ts`'s `autoTraceSkyline` for the dashed proposal.
//
// H.11: the prototype never implements this (it sets a flag and toasts), so
// everything below is new work with no reference to transcribe. Every RULE
// that can be checked without a browser and a camera is a pure function
// (support detection, luminance, percentile, column folding); `PhotosphereSweep`
// is the thin, deliberately un-clever class that drives them from the real
// APIs. Unverified against real hardware - flagged in the task report per
// H.11's own instruction, not silently claimed as tested.

export interface PhotosphereSupport {
  supported: boolean;
  reason: string | null;
}

/** Secure context + `getUserMedia` - what CAPTURE PHOTOSPHERE needs before it
 *  can even ask for a camera. Same reason string as the AR camera/gyro
 *  fallback (B.10) - one sentence for "this needs HTTPS", not three. */
export function checkPhotosphereSupport(): PhotosphereSupport {
  const insecure = typeof window === "undefined" || !window.isSecureContext;
  const md = typeof navigator === "undefined"
    ? undefined
    : (navigator as Navigator & { mediaDevices?: MediaDevices }).mediaDevices;
  if (insecure || !md || typeof md.getUserMedia !== "function") {
    return {
      supported: false,
      reason: "Photosphere capture needs a secure connection - set one up in Connection.",
    };
  }
  return { supported: true, reason: null };
}

/** Rec. 601 luma from an 8-bit RGB triple. */
export function luminance(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/** The value at percentile `p` (0..1), nearest-rank. Empty input is 0 - a
 *  column with nothing sampled must never read as a bright sky. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx];
}

/** `skyLum` for `autoTraceSkyline`: the 80th percentile luminance of the top
 *  10% of rows across every sampled column (plan B.14 step 3) - read off
 *  whatever the top of THIS sweep actually was, so an overcast sky and a
 *  blue one both produce a usable threshold. */
export function skyLumFromColumns(columns: number[][]): number {
  const topRows: number[] = [];
  for (const col of columns) {
    const take = Math.max(1, Math.round(col.length * 0.1));
    for (let r = 0; r < take && r < col.length; r++) topRows.push(col[r]);
  }
  return percentile(topRows, 0.8);
}

/** Which azimuth bin (0..bins-1) a heading falls in. */
export function binForHeading(headingDeg: number, bins: number): number {
  const h = ((headingDeg % 360) + 360) % 360;
  return Math.min(bins - 1, Math.floor((h / 360) * bins));
}

export interface SweepFrame {
  bin: number;
  column: number[];
}

/** Fold captured frames into `autoTraceSkyline`'s `columns[]`: one column per
 *  azimuth bin, keeping the LAST frame seen for a bin (the most recent pass
 *  through that heading). Empty bins stay `[]`, never a zeroed column -
 *  `autoTraceSkyline` already reads a short/empty column as "no drop found",
 *  i.e. open to the bottom of frame, which is the honest default where there
 *  is no data. */
export function foldSweepColumns(frames: SweepFrame[], bins: number): number[][] {
  const out: (number[] | undefined)[] = new Array(bins);
  for (const f of frames) out[f.bin] = f.column;
  return out.map((c) => c ?? []);
}

/** Per-row luminance for one already-drawn video frame: `rows` samples
 *  spaced evenly down the frame, each a full-width average so a single hot
 *  pixel cannot fake a sky-to-ground transition. Pure function of the pixel
 *  buffer, so it is testable with a fabricated `Uint8ClampedArray` and no
 *  live video element. */
export function columnFromImageData(
  data: Uint8ClampedArray,
  width: number,
  height: number,
  rows: number,
): number[] {
  const out: number[] = [];
  for (let r = 0; r < rows; r++) {
    const y = Math.min(height - 1, Math.floor((r / Math.max(1, rows - 1)) * (height - 1)));
    let sum = 0;
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      sum += luminance(data[i], data[i + 1], data[i + 2]);
    }
    out.push(sum / Math.max(1, width));
  }
  return out;
}

/**
 * Drives the capture sweep: opens the environment-facing camera, grabs a
 * frame roughly every 350ms, bins each into an azimuth column keyed by the
 * device's compass heading when `deviceorientation` is available, or by
 * elapsed time against an assumed 12s full turn when it is not (the
 * photosphere card's own "assumes a level sweep" disclosure covers that
 * fallback - B.14 step 3). Everything that does not need a live camera is
 * the pure functions above; this class only holds the stream/video/canvas
 * and calls them in order.
 */
export class PhotosphereSweep {
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private frames: SweepFrame[] = [];
  private headingHandler: ((e: Event) => void) | null = null;
  private heading = 0;
  private hasOrientation = false;
  private startedAt = 0;
  private grabTimer: ReturnType<typeof setInterval> | null = null;
  private lastBin = -1;
  private generation = 0;
  readonly bins: number;

  constructor(bins = 30) {
    this.bins = bins;
  }

  get frameCount(): number {
    return this.frames.length;
  }

  get currentHeading(): number {
    return this.heading;
  }

  /** False once a frame has actually been binned by a real heading - drives
   *  the "no tilt sensor" disclosure on the adopted trace. */
  get usedOrientation(): boolean {
    return this.hasOrientation;
  }

  async start(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<void> {
    this.stop();
    const generation = this.generation;
    this.video = video;
    this.canvas = canvas;
    this.startedAt = Date.now();
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    // Closing the editor while the browser permission prompt is open must
    // also release a camera granted after the editor has disappeared.
    if (generation !== this.generation) { stream.getTracks().forEach(t=>t.stop()); return; }
    this.stream = stream;
    video.srcObject = this.stream;
    await video.play().catch(() => { /* autoplay can refuse; muted+playsinline covers most browsers */ });
    if (generation !== this.generation) return;

    if (typeof window !== "undefined" && "DeviceOrientationEvent" in window) {
      this.headingHandler = (e: Event) => {
        const oe = e as DeviceOrientationEvent & { webkitCompassHeading?: number };
        const h = oe.webkitCompassHeading ?? (oe.alpha != null ? 360 - oe.alpha : null);
        if (h != null) { this.heading = h; this.hasOrientation = true; }
      };
      window.addEventListener("deviceorientation", this.headingHandler);
    }

    this.grabTimer = setInterval(() => this.grabFrame(), 350);
  }

  private grabFrame(): void {
    const { video, canvas } = this;
    if (!video || !canvas || video.videoWidth === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const column = columnFromImageData(data, canvas.width, canvas.height, 24);

    const heading = this.hasOrientation
      ? this.heading
      : (((Date.now() - this.startedAt) / 12000) * 360) % 360;
    const bin = binForHeading(heading, this.bins);
    if (bin === this.lastBin) return; // same column as last grab - nothing new
    this.lastBin = bin;
    this.frames.push({ bin, column });
  }

  columns(): number[][] {
    return foldSweepColumns(this.frames, this.bins);
  }

  stop(): void {
    this.generation++;
    if (this.grabTimer != null) { clearInterval(this.grabTimer); this.grabTimer = null; }
    if (this.headingHandler && typeof window !== "undefined") {
      window.removeEventListener("deviceorientation", this.headingHandler);
    }
    this.headingHandler = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
  }
}
