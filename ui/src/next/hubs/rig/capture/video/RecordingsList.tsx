// RecordingsList.tsx - the .ser files on the rig, and the three things to do
// with one.
//
// DOWNLOAD SER IS AN ANCHOR OR IT IS NOTHING. `view.media` gates the route
// (`video_routes.py:180-188`), so a principal without it gets a span carrying
// the reason rather than a link that would 403 - an `<a>` that fails is a
// download the browser reports as a broken file, which is the worst possible
// way to learn about a capability.
//
// QUICK STACK lands nothing here. `POST .../stack` answers 202 and the RESULT
// arrives on the `video_stack` bus lane; the row learns about it when
// `has_stack` turns true on the next list fetch. So the button reports what it
// STARTED, and the picture appears when there is one - never a spinner that
// claims to know how far along a stack is when nothing published a fraction.
//
// THE STACK LANE IS NOT THE CAMERA. `video_stack` is deliberately unlabelled
// (`hub.py:346-352`): it is pure compute on a file already on disk. A row that
// said "the camera is busy" during a stack would be wrong twice.
//
// Props only.

import type { JSX } from "react";
import { ActionButton, Card, EmptyCard, Label, Mono } from "../../../../ui";
import { lockedAttrs } from "../../../../ui/honest";
import { recordingSerUrl, recordingStackUrl } from "../../../../../api/video";
import { fmtBytes } from "../../../../../lib/gallery";
import { recordingLine, recordingStamp } from "./videoModel";
import type { VideoRecording } from "../../../../../types";

export const NO_RECORDINGS_HINT =
  "A recording writes one .ser file per press. They live beside the night's "
  + "frames on the rig, not on this phone.";

/** What lucky stacking does, said once. The number is the whole decision: a
 *  keep of 25 percent throws away three frames in four, on purpose, because
 *  the seeing was worse in them. */
export function keepNote(keepPct: number): string {
  return `Aligns the file's frames, ranks them by sharpness and stacks the best `
    + `${keepPct} percent into one PNG. The .ser is not changed.`;
}

export interface RecordingsListProps {
  recordings: VideoRecording[] | null;
  loading: boolean;
  error: string | null;
  /** Show only the newest N. The capture screen shows one; the library sheet
   *  shows every one. */
  limit?: number;
  keepPct: number;
  /** `view.media`, resolved by the caller: "needs syncer or admin access". */
  mediaReason: string | null;
  /** `view.preview`, for the stack picture. */
  previewReason: string | null;
  /** `control.capture` plus the stack lane. */
  stackReason: string | null;
  /** `control.capture`; a row being written adds its own reason. */
  deleteReason: string | null;
  /** The id a stack was started for, while the `video_stack` lane is live. */
  stackingId: string | null;
  /** The id being written right now, which cannot be deleted
   *  (`video_routes.py:194-195` answers 409 `lane_busy`). */
  writingId: string | null;
  onStack: (id: string) => void;
  onDelete: (rec: VideoRecording) => void;
  onExplain: (reason: string) => void;
}

export function RecordingsList(props: RecordingsListProps): JSX.Element {
  const all = props.recordings ?? [];
  const rows = props.limit != null ? all.slice(0, props.limit) : all;

  if (props.error) {
    return (
      <Card tone="dashed">
        <div className="nx-vid-list" data-testid="video-recordings">
          <Label size={11}>RECORDINGS</Label>
          <Mono size={10.5} tone="bad">{props.error}</Mono>
        </div>
      </Card>
    );
  }

  if (props.recordings == null) {
    return (
      <div className="nx-vid-list" data-testid="video-recordings">
        <Label size={11}>RECORDINGS</Label>
        <Mono size={10.5} tone="dim">
          {props.loading ? "reading the rig's recordings" : "not read yet"}
        </Mono>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="nx-vid-list" data-testid="video-recordings">
        <Label size={11}>RECORDINGS</Label>
        <EmptyCard title="NOTHING RECORDED YET" hint={NO_RECORDINGS_HINT} />
      </div>
    );
  }

  return (
    <div className="nx-vid-list" data-testid="video-recordings">
      <Label size={11}>RECORDINGS</Label>
      {rows.map((rec) => {
        const writing = props.writingId === rec.id;
        const stacking = props.stackingId === rec.id;
        const deleteReason = writing
          ? "That recording is still being written - stop it first."
          : props.deleteReason;
        return (
          <Card key={rec.id} tone="default">
            <div className="nx-vid-row" data-testid="video-recording-row" data-rec={rec.id}>
              <div className="nx-vid-row-head">
                <Mono size={11}>{rec.id}</Mono>
                <Mono size={10} tone="dim">{recordingStamp(rec.ts)}</Mono>
              </div>
              <Mono size={10.5} tone="dim">{recordingLine(rec, fmtBytes)}</Mono>
              <Mono size={10} tone="dim">{`camera ${rec.camera}`}</Mono>

              {rec.has_stack && (
                props.previewReason ? (
                  <Mono size={10.5} tone="dim" data-testid="video-stack-locked">
                    {`Stack picture hidden - ${props.previewReason}.`}
                  </Mono>
                ) : (
                  <img
                    className="nx-vid-stack-img"
                    src={recordingStackUrl(rec.id)}
                    alt={`Lucky stack of recording ${rec.id}`}
                    data-testid="video-stack-image"
                  />
                )
              )}

              <div className="nx-vid-row-actions">
                {props.mediaReason ? (
                  <span
                    className="nx-vid-fakelink"
                    data-testid="video-download-locked"
                    onClick={() => props.onExplain(props.mediaReason as string)}
                    {...lockedAttrs(props.mediaReason)}
                  >
                    {`DOWNLOAD SER - ${props.mediaReason}`}
                  </span>
                ) : (
                  <a
                    className="nx-vid-download"
                    href={recordingSerUrl(rec.id)}
                    download={`${rec.id}.ser`}
                    data-testid="video-download"
                  >
                    {`DOWNLOAD SER · ${fmtBytes(rec.bytes)}`}
                  </a>
                )}

                <ActionButton
                  kind="secondary"
                  onPress={() => props.onStack(rec.id)}
                  busy={stacking}
                  lockedReason={props.stackReason}
                  onExplain={props.onExplain}
                  data-testid="video-stack"
                >
                  {stacking ? "STACKING"
                    : rec.has_stack ? `RE-STACK · KEEP ${props.keepPct}%`
                      : `QUICK STACK · KEEP ${props.keepPct}%`}
                </ActionButton>

                <ActionButton
                  kind="ghost"
                  onPress={() => props.onDelete(rec)}
                  lockedReason={deleteReason}
                  onExplain={props.onExplain}
                  data-testid="video-delete"
                >
                  DELETE
                </ActionButton>
              </div>
            </div>
          </Card>
        );
      })}
      <Mono size={10} tone="dim">{keepNote(props.keepPct)}</Mono>
      {props.limit != null && all.length > rows.length && (
        <Mono size={10} tone="dim">
          {`${all.length - rows.length} older recording${all.length - rows.length === 1 ? "" : "s"} `
            + "are in the library."}
        </Mono>
      )}
    </div>
  );
}
