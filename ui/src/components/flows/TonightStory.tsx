// TonightStory.tsx — the same night, in sentences the operator can argue with.
//
// This tab is a 1:1 rendering of the server's `story[]` and nothing else. The
// prototype had its own story generator (a hardcoded 20:41/20:52/21:58/… script
// with fabricated banked hours); contract §C.11 says it becomes dead code and
// must not be ported, because a plan written in the browser and a plan the
// engine will run are two different documents that look identical on screen.
//
// The row shape is `{t_unix, label, msg, tone}` (tonight.py:568). `label` is ""
// for a timed row and literally "ANY" / "BUDGET" / "—" otherwise, which is what
// the 60px first column is sized for. `tone` names a rung on the token ladder;
// tonight.py deliberately does NOT send hex, because the do-not list forbids it
// and because the night palette re-derives every one of these.

import type { JSX } from "react";
import { fmtTime } from "../../lib/visibility";

export interface TonightStoryRow {
  /** Unix seconds, or null for an untimed row (an "ANY" rule, a "BUDGET" line,
   *  or the "—" row that carries a refusal reason). */
  t_unix: number | null;
  label: string;
  msg: string;
  tone: string;
}

/** tonight.py's closed tone set (TONE_TEXT…TONE_BAD) → the token ladder.
 *  An unknown tone falls back to body ink rather than being dropped: a sentence
 *  the server thought worth sending is worth showing even if a future rung
 *  arrives before this map does. */
const TONE_CLASS: Record<string, string> = {
  text: "text-ink",
  dim: "text-dim",
  faint: "text-faint",
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
};

/** The mechanical brief: the graph read back as one paragraph of prose.
 *
 *  Generated on the SERVER (tonight.py::brief), for the same reason the story
 *  rows are: a second generator in the browser would be a second reading of the
 *  same graph, and the two would drift into describing different nights.
 *
 *  It is bordered and it leads the tab because it is the one thing on this
 *  surface that puts every number the night will use into a single sentence
 *  sequence. The canvas shows the shape; the inspector shows one node. A graph
 *  that reads wrong out loud usually is wrong, and this is where that is
 *  noticeable at 21:00 rather than at 03:00.
 */
function Brief({ text }: { text: string }): JSX.Element | null {
  if (!text) return null;
  return (
    <section
      className="border border-line rounded-[10px] p-3 flex flex-col gap-2"
      aria-label="Brief generated from the graph"
      data-tonight-brief=""
    >
      <h3 className="font-display font-semibold text-[9.5px] tracking-[0.22em] text-faint">
        BRIEF - GENERATED FROM THE GRAPH
      </h3>
      <p className="text-[12.5px] leading-[1.65] [text-wrap:pretty]">{text}</p>
    </section>
  );
}

export function TonightStory({ story, brief = "" }: {
  story: TonightStoryRow[];
  /** "" when the server had no graph to read — a plan dict alone cannot
   *  produce this, the same rule the dawn story's report sink follows. */
  brief?: string;
}): JSX.Element {
  if (story.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <Brief text={brief} />
        <p className="text-[12px] leading-[1.5] text-dim [text-wrap:pretty]">
          The server resolved this night and had nothing to say about it.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Brief text={brief} />
      <div className="flex flex-col gap-2">
      {story.map((row, i) => (
        <div
          key={`${i}-${row.t_unix ?? row.label}`}
          className="grid gap-2.5 items-baseline"
          style={{ gridTemplateColumns: "60px 1fr" }}
        >
          <span className="font-mono text-[10px] text-faint text-right">
            {/* A label wins when the server sent one — "ANY" and "BUDGET" are
                statements that the row has no clock time, not missing data. */}
            {row.label !== "" ? row.label : fmtTime(row.t_unix)}
          </span>
          <span
            className={`text-[12px] leading-[1.5] [text-wrap:pretty] ${
              TONE_CLASS[row.tone] ?? "text-ink"
            }`}
          >
            {row.msg}
          </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default TonightStory;
