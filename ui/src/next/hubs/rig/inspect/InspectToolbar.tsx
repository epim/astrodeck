// InspectToolbar.tsx - the preview toolbar, rebuilt in the design's vocabulary
// (wave R7, T-R7-19; replaces `components/preview/PreviewToolbar.tsx`, which is
// NOT edited and keeps serving `#/classic`).
//
// WHAT IT CARRIES, AND WHY IN THIS ORDER. The zoom primaries first, because they
// are what a finger reaches for while the picture is on screen: `-`, the
// percentage, `+`, FIT, 100%, all at the 48 px toolbar floor and never collapsed
// into an overflow sheet. Then the overlay toggles, then the magnifier, then the
// download disclosure at the end of the row.
//
// TOGGLES ARE FILLED PLUS A WORD PLUS A GLYPH, never colour alone. `IconButton48`
// carries the fill and the border; the glyph swaps to a tick when the overlay is
// on; the word is always there. Under `:root.night` every token collapses toward
// one red hue, so a state carried by colour would simply stop existing.
//
// THE LEGACY PICKER IS GONE, AND THAT IS THE ONE SHAPE CHANGE. `PreviewToolbar`
// folded six overlays into a `PickerButton` summarised as "2 on", because six
// 44 px switches plus a legend was most of a 300 px rail in the old three-column
// layout. This sheet is a 420 px panel (or a full-height phone sheet) with one
// wrapping row, so the toggles are visible controls again - a summary that reads
// "2 on" cannot say WHICH two, and the legacy component had to grow a whole
// second helper (`annotationsOn`) to stop that summary lying about overlays the
// stage was refusing to draw. Every row, every reason and the two rows that
// exist only when their data does are preserved in `toggleRows()`.
//
// EVERY LOCKED SURFACE goes through `lockedReason` + `onExplain`: dim,
// `aria-disabled`, still focusable, and a press STATES the reason. The native
// `disabled` attribute appears nowhere - it strips the element and its reason
// out of the accessibility tree and leaves a grey rectangle that cannot be asked
// why. On the tablet at the scope, which is the primary field device, `title=`
// never fires at all, so the press path is the only one that reaches a finger.

import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import type { OverlayToggles, PreviewInfo, StretchParams } from "../../../../types";
import { NxIcon } from "../../../icons";
import { ActionButton, IconButton48, Mono, Popover } from "../../../ui";
import { lockedAttrs, lockedClass } from "../../../ui/honest";
import {
  downloadLockReason, downloadRows, magnifierLockReason, toggleNext, toggleOn, toggleRows,
  zoomLockReason, zoomText, type ToggleId,
} from "./inspectModel";

/** Glyphs the shared set does not draw. Same idiom as the rest of `icons.tsx`:
 *  one 24x24 grid, `currentColor`, round caps. Drawn here rather than added to
 *  the shared set because they are this toolbar's own two overlays. */
function Glyph({ d, size = 16 }: { d: string; size?: number }): JSX.Element {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const RETICLE_D = "M12 3v6M12 15v6M3 12h6M15 12h6M12 10.5a1.5 1.5 0 1 0 0 3a1.5 1.5 0 1 0 0-3";
const CENTER_D = "M4 4h4M16 4h4M4 20h4M16 20h4M4 4v4M20 4v4M4 16v4M20 16v4M12 11v2M11 12h2";
const TILT_D = "M4 18l6-6 4 4 6-8M4 6h4M4 6v4";
const SPIKES_D = "M5 5l14 14M19 5L5 19M12 10a2 2 0 1 0 0 4a2 2 0 1 0 0-4";
const FIT_D = "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5";
const ONE_TO_ONE_D = "M4 6h16M4 18h16M9 9l1-1v8M14 9l1-1v8";
const CLIP_D = "M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7";

const TOGGLE_GLYPH: Record<ToggleId, ReactNode> = {
  stars: <NxIcon name="star" size={16} />,
  clip: <Glyph d={CLIP_D} />,
  reticle: <Glyph d={RETICLE_D} />,
  centerMark: <Glyph d={CENTER_D} />,
  tilt: <Glyph d={TILT_D} />,
  bahtinov: <Glyph d={SPIKES_D} />,
  objects: <NxIcon name="galaxy" size={16} />,
};

const TICK = <NxIcon name="check" size={16} />;

export interface InspectToolbarProps {
  preview: PreviewInfo | null;
  overlays: OverlayToggles;
  setOverlays: (o: Partial<OverlayToggles>) => void;
  /** The stage's current zoom, already rounded to whole percent. */
  scalePct: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onHundred: () => void;
  starsAvailable: boolean;
  /** `data_is_linear && full_well != null` - the clip mask's own gate. */
  clipAvailable: boolean;
  linkDown: boolean;
  /** The newest frame this browser has seen, for the retention gates. */
  liveId: number | null;
  /** The client stretch, so the full-res export bakes the levels on screen. */
  stretch: StretchParams;
  shareMeta?: { target?: string; subs?: number };
  loupeOn: boolean;
  loupeAvailable: boolean;
  onLoupe: (v: boolean) => void;
  /** How a refused press says why. The sheet passes the shell's toast channel. */
  onExplain: (reason: string) => void;
}

/** The zoom group's own copy. The legacy toolbar carried it in `title=`, which
 *  never fires on a touch screen - so on the tablet at the scope "Magnifier" was
 *  a word with no explanation anywhere, and "100%" and "1:1" are adjacent
 *  homographs to a novice. */
const ZOOM_HELP: { name: string; text: string }[] = [
  { name: "FIT", text: "scales the whole frame into the panel." },
  { name: "100%", text: "100% of the PREVIEW image, which is downscaled to 1400 px on its long edge." },
  {
    name: "MAGNIFIER",
    text: "real sensor pixels at the centre of the view - the true focus and noise check. Needs linear data.",
  },
];

export function InspectToolbar({
  preview, overlays, setOverlays, scalePct, onZoomIn, onZoomOut, onFit, onHundred,
  starsAvailable, clipAvailable, linkDown, liveId, stretch, shareMeta,
  loupeOn, loupeAvailable, onLoupe, onExplain,
}: InspectToolbarProps): JSX.Element {
  const [dlOpen, setDlOpen] = useState(false);
  const dlRef = useRef<HTMLDivElement | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const helpRef = useRef<HTMLDivElement | null>(null);

  const zoomLock = zoomLockReason(!!preview);
  const rows = toggleRows(preview, { starsAvailable, clipAvailable });
  const dlInput = { preview, liveId, linkDown, stretch, shareMeta };
  const dlLock = downloadLockReason(dlInput);
  // A DISCLOSURE CANNOT OUTLIVE ITS OWN TRIGGER. `dlLock` flips the moment the
  // link drops or a pinned frame falls out of the ring, and the panel below used
  // to keep rendering live `<a download>` rows under a trigger that had just
  // withdrawn them - and an `<a download>` that 404s reports nothing at all.
  const dlPanelOpen = dlOpen && !dlLock;
  // The render above cannot paint that contradiction for even one frame; this
  // clears the STATE so the panel does not spring back open by itself when the
  // link returns or the user pins a fresher frame.
  useEffect(() => { if (dlLock) setDlOpen(false); }, [dlLock]);
  const magnifierLock = magnifierLockReason(preview, loupeAvailable);

  return (
    <div className="nx-insp-toolbar" data-testid="preview-toolbar">
      <div className="nx-insp-cluster">
        <IconButton48
          glyph={<NxIcon name="minus" size={16} />} label="OUT"
          onPress={onZoomOut} lockedReason={zoomLock} onExplain={onExplain}
          data-testid="preview-zoom-out"
        />
        {/* The only control in the cluster that ASSERTS something, so it reads
            "-" rather than a percentage while the cluster is inert. */}
        <span className="nx-insp-zoom" title={zoomLock ?? undefined} data-testid="preview-zoom">
          <Mono size={11} tone={zoomLock ? "dim" : undefined}>{zoomText(scalePct, zoomLock)}</Mono>
        </span>
        <IconButton48
          glyph={<NxIcon name="plus" size={16} />} label="IN"
          onPress={onZoomIn} lockedReason={zoomLock} onExplain={onExplain}
          data-testid="preview-zoom-in"
        />
        <IconButton48
          glyph={<Glyph d={FIT_D} />} label="FIT"
          onPress={onFit} lockedReason={zoomLock} onExplain={onExplain}
          data-testid="preview-zoom-fit"
        />
        {/* 100% of the <=1400 px PREVIEW encode. The magnifier below is 100% of
            the SENSOR, which is a different and much stricter thing - the two
            are adjacent homographs to a novice, so neither is labelled "1:1". */}
        <IconButton48
          glyph={<Glyph d={ONE_TO_ONE_D} />} label="100%"
          onPress={onHundred} lockedReason={zoomLock} onExplain={onExplain}
          data-testid="preview-zoom-hundred"
        />
      </div>

      <div className="nx-insp-cluster">
        {rows.map((row) => {
          const on = toggleOn(overlays, row);
          return (
            <IconButton48
              key={row.id}
              glyph={on ? TICK : TOGGLE_GLYPH[row.id]}
              label={row.label}
              active={on}
              onPress={() => setOverlays(toggleNext(overlays, row))}
              lockedReason={row.lockedReason}
              onExplain={onExplain}
              data-testid={`preview-toggle-${row.id}`}
            />
          );
        })}
        <IconButton48
          glyph={loupeOn ? TICK : <NxIcon name="search" size={16} />}
          label="MAGNIFIER"
          active={loupeOn}
          onPress={() => onLoupe(!loupeOn)}
          lockedReason={magnifierLock}
          onExplain={onExplain}
          data-testid="preview-toggle-magnifier"
        />
        {/* ONE help control, not one per button. Seven more 44 px triggers would
            be rows of pure help on a bar whose job is the primaries, and the
            legacy pair of `title=` tooltips could not be opened by a fingertip
            at all. Every overlay's copy, and every locked row's reason, is here
            on a tap. */}
        <div className="nx-insp-help" ref={helpRef}>
          <IconButton48
            glyph={<NxIcon name="info" size={16} />}
            label="ABOUT"
            active={helpOpen}
            onPress={() => setHelpOpen((v) => !v)}
            data-testid="preview-overlays-help"
          />
          <Popover
            open={helpOpen}
            anchorRef={helpRef}
            onClose={() => setHelpOpen(false)}
            data-testid="preview-overlays-help-menu"
          >
            <div className="nx-insp-help-list">
              {rows.map((row) => (
                <p key={row.id} className="nx-insp-help-row">
                  <span className="nx-insp-help-name">{row.label}</span>
                  {row.hint}
                  {row.lockedReason != null && (
                    <span className="nx-insp-help-lock"> Unavailable here: {row.lockedReason}.</span>
                  )}
                </p>
              ))}
              {ZOOM_HELP.map((h) => (
                <p key={h.name} className="nx-insp-help-row">
                  <span className="nx-insp-help-name">{h.name}</span>
                  {h.text}
                </p>
              ))}
            </div>
          </Popover>
        </div>
      </div>

      <div className="nx-insp-dl" ref={dlRef}>
        <ActionButton
          kind="ghost"
          glyph={<NxIcon name="download" size={16} />}
          onPress={() => setDlOpen((v) => !v)}
          lockedReason={dlLock}
          onExplain={onExplain}
          data-testid="preview-download"
        >
          DOWNLOAD
        </ActionButton>
        <Popover
          open={dlPanelOpen}
          anchorRef={dlRef}
          onClose={() => setDlOpen(false)}
          align="end"
          data-testid="preview-download-menu"
        >
          <div className="nx-insp-dl-list" role="group"
            aria-label={`Download frame ${preview?.id ?? ""}`.trim()}>
            {downloadRows(dlInput).map((r) => (
              r.href
                ? (
                  // A plain <a download>, never a fetch into a Blob: iOS serves
                  // one file at a time in the foreground and the cookie on the
                  // navigation is the auth.
                  <a
                    key={r.id}
                    className="nx-insp-dl-row"
                    data-primary={r.primary ? "true" : undefined}
                    href={r.href}
                    download={r.filename}
                    onClick={() => setDlOpen(false)}
                    data-testid={`preview-dl-${r.id}`}
                  >
                    <span className="nx-insp-dl-label">{r.label}</span>
                    {r.hint != null && <span className="nx-insp-dl-hint">{r.hint}</span>}
                  </a>
                )
                : (
                  <button
                    key={r.id}
                    type="button"
                    className={lockedClass(r.lockedReason, "nx-insp-dl-row")}
                    onClick={() => { if (r.lockedReason) onExplain(r.lockedReason); }}
                    data-testid={`preview-dl-${r.id}`}
                    {...lockedAttrs(r.lockedReason)}
                  >
                    <span className="nx-insp-dl-label">{r.label}</span>
                    <span className="nx-insp-dl-hint">{r.lockedReason}</span>
                  </button>
                )
            ))}
          </div>
        </Popover>
      </div>
    </div>
  );
}
