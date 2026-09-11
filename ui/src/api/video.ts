// api/video.ts: SER recording and the lucky stack (D-RIG-1). Server:
// server/astrodeck/imaging/video_routes.py over imaging/video.py.
//
// Video on this rig is a burst of short exposures written straight to a SER
// file, not a stream: the camera is owned for the whole recording, the frames
// go to disk unstretched, and what comes back is a file rather than a picture.
// That is why the routes here look like a capture surface and not like a
// preview one, and why the recorder holds a BLOCKING lane (`hub.py:307-311`) -
// a restart mid-file leaves a truncated .ser.
//
// TWO LANES, and they are not the same colour. `video` owns the camera and
// blocks; `video_stack` is pure compute on a file already on disk, touches no
// device, and a restart costs a stack that can be re-run in seconds from the
// same .ser (`hub.py:346-352`). A screen that showed "the camera is busy"
// during a stack would be wrong twice - it is not busy, and the operator would
// wait for nothing.
//
// EVERY REFUSAL CARRIES A CODE except one, and the exception is the interesting
// one. The 409s arrive as `ApiError.code` (`no_video_path`, `lane_busy`,
// `camera_busy`) and callers branch on that. The 507 is a disk shortfall and
// carries NUMBERS instead of a code, so it gets its own reader below rather
// than a caller poking at `err.body`.

import { api, ApiError } from "../api";
import { apiErrorPayload } from "../lib/apiError";
import { u } from "../lib/base";
import type {
  VideoRecording,
  VideoRoi,
  VideoStackStarted,
  VideoStarted,
  VideoState,
  VideoStopped,
} from "../types";

/** Body of `POST /api/capture/video` (`video_routes.py:57-65`). Every field has
 *  a server default, so the smallest honest request is `{}`. */
export interface VideoStartBody {
  /** Subframe in UNBINNED sensor pixels. Omitted means the whole sensor. The
   *  server ROUNDS IT AGAIN onto the sensor's own grid (`video.py:133-160
   *  align_roi`, to `lcm(caps.roi_align, bin)`), so what a picker shows before
   *  the press is a request and the `roi` on the response is the truth. */
  roi?: Partial<VideoRoi>;
  /** Frames per second asked for. 0 < fps <= 10000; default 30. The recorder
   *  clamps to what the adapter will honour and REPORTS the clamp
   *  (`clamped` / `clamp_reason`) rather than quietly delivering a slower file. */
  fps?: number;
  /** Per-frame exposure, milliseconds. 0 < ms <= 60000; default 8. */
  exposure_ms?: number;
  gain?: number;
  offset?: number;
  /** How long to record, seconds. > 0; default 10. With `fps` it decides
   *  `target_frames` and, with the frame size, `est_bytes`. */
  duration_s?: number;
  /** Default and only accepted value today; anything else is a 400 naming what
   *  this rig writes. Present so a future container is an additive change. */
  format?: "ser";
}

/** The 409 codes `POST /api/capture/video` can answer with, in the order the
 *  route checks them (`video_routes.py:96-154`). Branch on `ApiError.code`.
 *
 *  - `no_video_path` - THIS CAMERA CANNOT RECORD AT ALL. Not a busy state and
 *    not worth retrying: it is driven through an external driver with no
 *    subframe or burst path. The server's sentence names the brand and the
 *    backend; show it verbatim in place of the controls.
 *  - `lane_busy` - a recording (or, for the stack routes, a stack) is already
 *    running. Checked BEFORE `camera_busy` on purpose: a running recording holds
 *    the exposure guard, so the generic answer would hide the one case the
 *    caller can name and offer to stop.
 *  - `camera_busy` - something else has the camera (an autofocus sweep, a
 *    single capture). The detail names what.
 *
 *  The refusals ABOVE these in the route carry no code at all, because they are
 *  the same bare-string 409s `POST /api/capture` has always raised: "polar
 *  alignment in progress", "a sequence is running", "the live loop owns the
 *  camera", and the DeviceError when there is no camera. `ApiError.message` is
 *  the sentence for those. */
export type VideoRefusalCode = "no_video_path" | "lane_busy" | "camera_busy";

/** What a 507 from `POST /api/capture/video` carries instead of a code:
 *  `{"detail": "insufficient disk space", "free_bytes", "required_bytes"}`
 *  (`video_routes.py:147-151`). */
export interface VideoSpaceShortfall {
  free_bytes: number;
  required_bytes: number;
}

/** Read the two byte figures off a 507, or null when the error is anything else.
 *
 *  This exists so a sheet never reaches into `err.body` itself. The 507 is the
 *  one refusal on this surface with no `code` - it is a MEASUREMENT, and the
 *  two numbers are the whole message ("2.1 GB free, 5.8 GB needed" is a
 *  sentence an operator can act on; "insufficient disk space" is not). Status
 *  507 is checked as well as the shape, so a future coded error that happened
 *  to carry byte counts could not be mistaken for this one. */
export function videoSpaceShortfall(err: unknown): VideoSpaceShortfall | null {
  if (!(err instanceof ApiError) || err.status !== 507) return null;
  const d = apiErrorPayload(err.body);
  const free = d?.free_bytes;
  const need = d?.required_bytes;
  if (typeof free !== "number" || typeof need !== "number") return null;
  return { free_bytes: free, required_bytes: need };
}

/** `POST /api/capture/video` -> 202. Requires `control.capture` and a camera
 *  role. See `VideoRefusalCode` for the 409s and `videoSpaceShortfall` for the
 *  507; a 400 means the `format` is not one this rig writes.
 *
 *  The response's `roi` is the ALIGNED subframe and `actual_fps` is the PLAN,
 *  which is not the same number as the `fps` that arrives on the status and the
 *  bus (that one is measured). Display the response's numbers once recording
 *  starts, not the ones that were requested. */
export const startVideo = (body: VideoStartBody = {}): Promise<VideoStarted> =>
  api.post<VideoStarted>("/api/capture/video", body);

/** `GET /api/capture/video` - the recorder's own view of itself.
 *
 *  The `video` bus event carries the same numbers at about 2 Hz, so this is a
 *  WATCHDOG rather than the primary source: fetch it on mount, and on a slow
 *  timer while `active`, so a dropped socket cannot leave a progress bar frozen
 *  at 40 percent for the rest of the night. */
export const getVideoState = (): Promise<VideoState> =>
  api.get<VideoState>("/api/capture/video");

/** `POST /api/capture/video/stop` - cancel the recording in flight.
 *
 *  It WAITS for the unwind (up to 10 s server-side) so the numbers it answers
 *  with are the ones in the finished file rather than the ones in flight when
 *  it was asked. `cancelled: false` means there was nothing running, which is
 *  not a failure - the file, if any, is already closed and valid. */
export const stopVideo = (): Promise<VideoStopped> =>
  api.post<VideoStopped>("/api/capture/video/stop");

/** `GET /api/captures/video` - every .ser on disk, newest first. */
export const listRecordings = (): Promise<VideoRecording[]> =>
  api.get<{ recordings: VideoRecording[] }>("/api/captures/video")
    .then((r) => r.recordings ?? []);

/** `DELETE /api/captures/video/{id}` - the .ser, its sidecar and its stack.
 *
 *  409 `code: "lane_busy"` when it is the file being written right now. 404 on
 *  a traversal-shaped id or one that is not a recording; the server checks
 *  containment before it touches the filesystem. */
export const deleteRecording = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(
    `/api/captures/video/${encodeURIComponent(id)}`);

/** `POST /api/captures/video/{id}/stack` -> 202. Lucky-stack the sharpest
 *  `keepPct` percent of the file's frames into `<id>.stack.png`.
 *
 *  0 < keepPct <= 100; default 25. 409 `code: "lane_busy"` when a stack is
 *  already running - and only ONE runs at a time, though it is legitimate to
 *  stack last night's recording while tonight's is being written, because the
 *  two are different lanes.
 *
 *  The result does not come back here: it arrives on the `video_stack` bus
 *  event, and `has_stack` on the recording row turns true. */
export const stackRecording = (
  id: string,
  keepPct = 25,
): Promise<VideoStackStarted> =>
  api.post<VideoStackStarted>(
    `/api/captures/video/${encodeURIComponent(id)}/stack`,
    { keep_pct: keepPct });

/** The raw .ser, for a download link. Requires `view.media` - these are the
 *  original bytes off the sensor, the same class of thing a FITS download is,
 *  so a principal without that capability must not be shown an anchor at all.
 *
 *  Built through `u()` because the relay mounts the whole app under
 *  `/h/<home_id>`; a bare path 404s through the tunnel. */
export const recordingSerUrl = (id: string): string =>
  u(`/api/captures/video/${encodeURIComponent(id)}.ser`);

/** The rendered stack PNG. Requires `view.preview`, not `view.media`: a
 *  stretched picture is not the raw bytes. 404 until a stack has run, so gate
 *  the `<img>` on the row's `has_stack` rather than on an error handler. */
export const recordingStackUrl = (id: string): string =>
  u(`/api/captures/video/${encodeURIComponent(id)}/stack.png`);
