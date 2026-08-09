// ObjectCard — what the app can honestly say about the object you just tapped.
//
// THE ORDERING RULE is "by how much the app can stand behind it", and the
// deliberate consequence is that this card is SHORT. The catalogue holds seven
// fields per object and not one word of prose for any of its 13,370 rows; the
// sentence at the top is composed server-side from the fields the object
// actually has (catalog/describe.py), and nothing here pads it out.
//
// WHAT IS NOT ON THIS CARD, ON PURPOSE:
//   • No generated description. An offline observatory app cannot call a
//     language model at exposure time, and a model asked about NGC 4562 would
//     invent a discoverer for it.
//   • No paraphrase of the lines above it. Text that restates what the reader
//     can already see is the defect this repo keeps a memory file about.
//   • No RA/Dec. Formatting them here would be a SECOND coordinate formatter
//     beside catalog/coords.py's, and the Mount screen already shows position.
//
// AND WHAT IS: every absence is named. 1,823 catalog rows have no published
// magnitude and describe() simply omits the clause — so a reader could not tell
// "nobody measured it" from "the app forgot". This card says which.

import type { JSX } from "react";
import type { SkyRow } from "../../lib/skyRegion";

export interface ObjectCardProps {
  row: SkyRow | null;
  /** Longest edge of the camera's field, degrees. 0 = optics not set, in which
   *  case the "through your rig" line is omitted rather than guessed. */
  frameFovDeg: number;
  /** The whole deep-sky pack failed to load, so the map is answering from 64
   *  curated objects. */
  degraded: boolean;
  /** True while the first region fetch is still in flight. */
  loading: boolean;
  /** Nothing catalogued is currently in view (not the same as "not loaded"). */
  emptyRegion: boolean;
  onFrame: (row: SkyRow) => void;
  onClose: () => void;
}

/** Arcminutes under a degree, degrees above — the Atlas's own convention. */
function fmtSize(arcmin: number): string {
  if (arcmin >= 60) return `${(arcmin / 60).toFixed(2)}° across`;
  if (arcmin >= 1) return `${arcmin.toFixed(1)}′ across`;
  return `${(arcmin * 60).toFixed(1)}″ across`;
}

/** How this object sits in the camera's frame. Reuses the wording SkyCanvas
 *  already uses under the canvas so the two cannot describe the same geometry
 *  in two different vocabularies. */
function frameVerdict(sizeArcmin: number, frameFovDeg: number): string | null {
  if (!(frameFovDeg > 0) || !(sizeArcmin > 0)) return null;
  const frac = sizeArcmin / 60 / frameFovDeg;
  if (frac <= 1) return `Fills ${Math.round(frac * 100)}% of your frame`;
  return `${frac.toFixed(1)}× your frame — needs a mosaic`;
}

function ephemerisAge(row: SkyRow): string | null {
  if (row.kind !== "solar_system" || !row.ephemeris_unix) return null;
  const mins = Math.max(0, (Date.now() / 1000 - row.ephemeris_unix) / 60);
  const when = mins < 1 ? "just now" : `${Math.round(mins)} min ago`;
  const where = row.topocentric === false
    ? " Computed for the centre of the Earth, because no site is set — that "
      + "moves the Moon by up to a degree."
    : "";
  return `Position computed ${when}; it moves as the night does.${where}`;
}

export function ObjectCard(props: ObjectCardProps): JSX.Element {
  const { row, frameFovDeg, degraded, loading, emptyRegion, onFrame, onClose } = props;

  if (!row) {
    return (
      <div className="text-[12px] text-dim leading-snug flex flex-col gap-1.5">
        <p>
          {loading
            ? "Looking up what's in this part of the sky…"
            : emptyRegion
              ? "Nothing catalogued is in this view. That is a real answer for "
                + "most of the sky — pan or zoom out to find something."
              : "Tap anything marked on the map to see what it is."}
        </p>
        {degraded && (
          <p className="text-warn">
            The deep-sky pack didn't load, so only the 64 built-in objects can
            be marked. Most of the sky will look empty until it is restored.
          </p>
        )}
      </div>
    );
  }

  const named = row.label !== row.id;
  const verdict = frameVerdict(row.size_arcmin, frameFovDeg);
  const age = ephemerisAge(row);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-base text-accent tracking-wide truncate">
            {named ? row.label : row.id}
          </h3>
          <p className="text-[12px] mono text-dim">
            {named ? row.id : row.type}
            {row.alias && <> · also {row.alias}</>}
          </p>
        </div>
        <button
          type="button"
          className="btn tap min-h-[36px] !px-2 shrink-0"
          onClick={onClose}
          aria-label={`Close the card for ${named ? row.label : row.id}`}
        >
          ✕
        </button>
      </div>

      {/* The one sentence, composed on the server from the fields this object
          actually has. Never empty, never a placeholder. */}
      <p className="text-[13px] leading-snug">{row.describe}</p>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-dim">Size</dt>
        <dd className="mono">
          {row.size_arcmin > 0
            ? fmtSize(row.size_arcmin)
            : row.kind === "star"
              ? "a point at any focal length"
              : "not recorded"}
        </dd>

        {/* describe() DROPS the magnitude clause when there is none, which is
            correct for a sentence and useless for a reader trying to decide
            whether the app knows. So the absence is stated here, in the row
            where a number would have been. */}
        <dt className="text-dim">Magnitude</dt>
        <dd className={row.mag == null ? "text-dim" : "mono"}>
          {row.mag == null ? "not published for this object" : row.mag.toFixed(1)}
        </dd>

        <dt className="text-dim">Constellation</dt>
        <dd className={row.constellation ? "" : "text-dim"}>
          {row.constellation ?? "not on file for this object"}
        </dd>
      </dl>

      {verdict && <p className="text-[12px]">{verdict}</p>}
      {age && <p className="text-[12px] text-dim leading-snug">{age}</p>}

      {row.kind === "dso" && (
        <p className="text-[12px] text-dim leading-snug">
          The offline catalogue carries positions, sizes and brightnesses — no
          descriptions or history for any object. Everything above is measured,
          not written.
        </p>
      )}

      <button
        type="button"
        className="btn btn-accent btn-touch w-full"
        onClick={() => onFrame(row)}
      >
        Frame this
      </button>
    </div>
  );
}

export default ObjectCard;
