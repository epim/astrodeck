// useVideoLibrary.ts - the recordings on disk, and the two verbs that change
// them. Shared by the capture screen (which shows the newest one inline) and
// the VIDEO LIBRARY sheet (which shows them all), so there is one fetch policy
// and not two that drift.
//
// WHEN IT RE-READS, and why each one is a real signal rather than a timer:
//
//  * on mount, once;
//  * when the `video` lane FALLS - a file has just been closed, so there is a
//    new row and its byte count is final;
//  * when the `video_stack` lane FALLS - `has_stack` has turned true (or the
//    stack failed and it has not), which is the only thing that decides whether
//    a row shows a picture.
//
// Both are edges off `status.busy_lanes` (`hub.py:2101`, unreduced), not polls.
// A stack publishes its own `video_stack` bus events, but they are not in the
// store (D-RIG-1 bought exactly one case, and it is the recording's) - and the
// lane edge carries the one fact the list needs, which the event stream would
// only tell it more often.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../../../../api";
import { deleteRecording, listRecordings, stackRecording } from "../../../../../api/video";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import { fmtBytes } from "../../../../../lib/gallery";
import { useStatus, useStore } from "../../../../../store";
import { laneBusy } from "./videoModel";
import type { VideoRecording } from "../../../../../types";

export interface VideoLibrary {
  recordings: VideoRecording[] | null;
  loading: boolean;
  /** The fetch's own failure, shown in place of the list. A missing route (S7L
   *  not on this rig) is a 404 and is reported as one, because "no recordings"
   *  and "this engine cannot record" are different facts. */
  error: string | null;
  /** The id whose stack we started, while the `video_stack` lane is live. */
  stackingId: string | null;
  refresh: () => void;
  stack: (id: string, keepPct: number) => void;
  remove: (rec: VideoRecording) => void;
}

export function useVideoLibrary(enabled: boolean): VideoLibrary {
  const status = useStatus();
  const enqueueToast = useStore((s) => s.enqueueToast);

  const [recordings, setRecordings] = useState<VideoRecording[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stackingId, setStackingId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listRecordings().then(
      (rows) => { setRecordings(rows); setError(null); },
      (e: unknown) => {
        const err = e as ApiError;
        setError(err?.status === 404
          ? "This engine does not carry SER recording yet - there is no recordings route on it."
          : (err?.message ?? "could not read the recordings"));
      },
    ).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!enabled) return;
    refresh();
  }, [enabled, refresh]);

  // ---------------------------------------------------------- the lane edges
  const recordingLane = laneBusy(status, "video");
  const stackLane = laneBusy(status, "video_stack");
  const sawRecording = useRef(false);
  const sawStack = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    if (recordingLane) { sawRecording.current = true; return; }
    if (!sawRecording.current) return;
    sawRecording.current = false;
    refresh();
  }, [enabled, recordingLane, refresh]);

  useEffect(() => {
    if (!enabled) return;
    if (stackLane) { sawStack.current = true; return; }
    if (!sawStack.current) return;
    sawStack.current = false;
    setStackingId(null);
    refresh();
  }, [enabled, stackLane, refresh]);

  // ------------------------------------------------------------- the verbs
  const stack = useCallback((id: string, keepPct: number) => {
    setStackingId(id);
    stackRecording(id, keepPct).then(
      () => {
        enqueueToast({
          level: "info",
          title: `Stacking ${id} - the picture appears on the row when it lands.`,
        });
      },
      (e: unknown) => {
        setStackingId(null);
        const err = e as ApiError;
        // The server's own sentence, by CODE and never by matching its text:
        // `lane_busy` here means another stack is running, which is not the
        // camera being busy and must not be described as it.
        enqueueToast({ level: "warning", title: err?.message ?? "the stack was refused" });
      },
    );
  }, [enqueueToast]);

  const remove = useCallback((rec: VideoRecording) => {
    void confirmDialog({
      title: `Delete ${rec.id}?`,
      body: `${fmtBytes(rec.bytes)} of raw frames, and its stack if it has one. `
        + "This cannot be undone and the file is not in the gallery's trash.",
      confirmLabel: "Delete",
      cancelLabel: "Keep",
      tone: "danger",
    }).then((ok) => {
      if (!ok) return;
      deleteRecording(rec.id).then(
        () => { refresh(); },
        (e: unknown) => {
          const err = e as ApiError;
          enqueueToast({ level: "warning", title: err?.message ?? "the delete was refused" });
        },
      );
    });
  }, [enqueueToast, refresh]);

  return { recordings, loading, error, stackingId, refresh, stack, remove };
}
