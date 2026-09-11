// videoLibrary.tsx - every SER recording on the rig (D-RIG-1).
//
// WHY THE LIST IS A SHEET AND THE CAPTURE SCREEN ONLY SHOWS ONE. A recording
// row carries an id, a stamp, a frame count, a measured rate, a subframe, a
// size, a stack picture and three controls. Two of those rows fill a phone
// screen, and the capture screen's job is the recording that is about to be
// made - so the bench shows the newest one (proof the last press produced a
// file) and hands the rest to this sheet.
//
// It fetches its OWN list through the same `useVideoLibrary` hook the bench
// uses, so the two cannot drift on when they re-read or on what a delete
// confirms. There is deliberately no shared cache between them: this sheet is
// opened rarely, and a cache would be one more thing to invalidate on the
// `video_stack` lane edge.

import { useEffect, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Mono, Sheet } from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import { useStatus, useVideo } from "../../../../store";
import { fmtBytes } from "../../../../lib/gallery";
import { getVideoState } from "../../../../api/video";
import type { VideoState } from "../../../../types";
import {
  KEEP_OPTIONS, RecordingsList, isTerminalVideoState, laneBusy, useVideoLibrary,
} from "../capture/video";

export function VideoLibrarySheet(_props: SheetProps): JSX.Element {
  const status = useStatus();
  const busEvent = useVideo();
  const library = useVideoLibrary(true);
  const [keepPct, setKeepPct] = useState<number>(25);

  // THE COLD READ. `useVideo()` is the BUS, and a bus only carries what has
  // happened since this tab subscribed: open this sheet onto a recording that
  // started before the sheet existed - the ordinary case, since the bench is
  // where recordings start - and no event has arrived, so the row for the file
  // being written looked deletable. `video_routes.py:194-195` then answers 409
  // `lane_busy`, which is a refusal the user had to press to discover.
  //
  // One shot, on mount, exactly like `VideoMode`'s own `refreshState`: after
  // this the bus is the live source and is strictly newer, so `busEvent` wins
  // below. A failure (404 on an engine without the route, or anything else)
  // leaves this null and the row behaves as it did before - the bus is still a
  // source, and a sheet that refused to list files because one GET failed would
  // be worse than the lock it is trying to place.
  const [coldState, setColdState] = useState<VideoState | null>(null);
  useEffect(() => {
    let live = true;
    getVideoState().then(
      (s) => { if (live) setColdState(s); },
      () => { /* no route, or no answer: the bus still carries the live ones */ },
    );
    return () => { live = false; };
  }, []);

  /** The recording that cannot be deleted because it is still being written.
   *  The bus first (it is newer), then the cold read. */
  const writingId = busEvent
    ? (isTerminalVideoState(busEvent.state) ? null : busEvent.id)
    : coldState?.active && coldState.id != null
      ? coldState.id
      : null;

  const mediaLock = useLock({ cap: "view.media" });
  const previewLock = useLock({ cap: "view.preview" });
  const captureLock = useLock({ cap: "control.capture" });

  const stackBusy = laneBusy(status, "video_stack")
    ? "A stack is already running - only one runs at a time."
    : null;

  const count = library.recordings?.length ?? null;
  const bytes = (library.recordings ?? []).reduce((n, r) => n + (r.bytes || 0), 0);

  return (
    <Sheet
      data-testid="rig-video-library"
      title="VIDEO LIBRARY"
      sub="SER recordings on the rig"
      icon={<NxIcon name="rig" size={18} />}
      live={count == null
        ? "reading the rig's recordings"
        : `${count} recording${count === 1 ? "" : "s"}`}
      onBack={() => nav.back()}
      backLabel="CAPTURE"
    >
      <div className="nx-vid" data-testid="video-library-body">
        <div className="nx-vid-keep" role="group" aria-label="Frames a quick stack keeps">
          {KEEP_OPTIONS.map((k) => (
            <button
              key={k}
              type="button"
              className="nx-vid-keep-opt"
              data-on={keepPct === k ? "true" : "false"}
              aria-pressed={keepPct === k}
              onClick={() => setKeepPct(k)}
              aria-label={`Keep the sharpest ${k} percent of the frames`}
            >
              {`KEEP ${k}%`}
            </button>
          ))}
        </div>

        <RecordingsList
          recordings={library.recordings}
          loading={library.loading}
          error={library.error}
          keepPct={keepPct}
          mediaReason={mediaLock.lockedReason}
          previewReason={previewLock.lockedReason}
          stackReason={captureLock.lockedReason ?? stackBusy}
          deleteReason={captureLock.lockedReason}
          stackingId={library.stackingId}
          // The file being written cannot be deleted (`video_routes.py:194-195`
          // answers 409 `lane_busy`), so the row says so before the press.
          writingId={writingId}
          onStack={(id) => library.stack(id, keepPct)}
          onDelete={library.remove}
          onExplain={mediaLock.onExplain}
        />

        {count != null && count > 0 && (
          <Mono size={10} tone="dim" data-testid="video-library-total">
            {`${count} file${count === 1 ? "" : "s"}, ${fmtBytes(bytes)}. `
              + "Deleting one here removes the .ser and its stack from the rig; the gallery's "
              + "trash does not hold recordings."}
          </Mono>
        )}
      </div>
    </Sheet>
  );
}
