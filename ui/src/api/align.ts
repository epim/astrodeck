// align.ts — where the instruments point relative to each other.
import { api } from "../api";

export interface GuideOffsetSolve {
  ok: boolean;
  ra_hours: number;
  dec_deg: number;
  rotation_deg: number;
  scale: number;
  message: string;
}

export interface GuideOffsetValue {
  sep_arcsec: number;
  /** Position angle in the INSTRUMENT frame, so it survives a meridian flip. */
  pa_deg: number;
  measured_ts: number;
  measured_pa_deg: number;
  camera: string;
  guide_camera: string;
  note: string;
}

export interface GuideOffsetMeasurement {
  main: GuideOffsetSolve;
  guide: GuideOffsetSolve;
  camera: string;
  guide_camera: string;
  offset: GuideOffsetValue | null;
  /** Present only when `offset` is null: which frame failed, and what it said. */
  reason?: string;
}


/** Start a measurement. Returns immediately -- two plate solves take about
 *  forty seconds, well past a browser fetch's patience, so the work runs in a
 *  lane and the result is collected with `getGuideOffset`. */
export const startGuideOffsetMeasure = (): Promise<{ started: string }> =>
  api.post<{ started: string }>("/api/align/guide-offset/measure");

/** The last measurement, or null before one has been taken. */
export const getGuideOffset = (): Promise<{ last: GuideOffsetMeasurement | null }> =>
  api.get<{ last: GuideOffsetMeasurement | null }>("/api/align/guide-offset");
