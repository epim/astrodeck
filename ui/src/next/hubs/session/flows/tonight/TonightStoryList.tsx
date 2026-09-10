// TonightStoryList.tsx - the same night, in sentences the operator can argue
// with (parity row A20).
//
// A 1:1 rendering of the server's `story[]` and the server's `brief`, and
// nothing else. A plan written in the browser and a plan the engine will run
// are two different documents that look identical on screen, so there is no
// second generator here: the row shape is `{t_unix, label, msg, tone}`
// (`tonight.py:568`), `label` is "" for a timed row and literally "ANY" /
// "BUDGET" / a dash placeholder otherwise, and `tone` names a rung on the token
// ladder rather than a hex.
//
// A refusal arrives through this same path: the server carries `reason` as a
// story row, which is why STORY needs no special case for `ok: false`.

import type { JSX } from "react";

import type { TonightStoryRow } from "../../../../../components/flows/TonightStory";
import { Card, Label } from "../../../../ui";
import { storyStamp, storyToneVar } from "./tonightModel";

/** The mechanical brief: the graph read back as one paragraph of prose,
 *  generated on the SERVER (`tonight.py::brief`). It leads the tab because it
 *  is the one thing on this surface that puts every number the night will use
 *  into a single sentence sequence - a graph that reads wrong out loud usually
 *  is wrong, and this is where that is noticeable at 21:00 rather than 03:00.
 *  "" means the server had no graph to read; the block is then absent rather
 *  than empty. */
function Brief({ text }: { text: string }): JSX.Element | null {
  if (!text) return null;
  return (
    <Card className="nx-tn-brief" data-testid="tonight-brief">
      <Label size={10}>BRIEF - GENERATED FROM THE GRAPH</Label>
      <p className="nx-tn-brief-text">{text}</p>
    </Card>
  );
}

export function TonightStoryList({ story, brief = "" }: {
  story: TonightStoryRow[];
  /** "" when the server had no graph - a plan dict alone cannot produce it. */
  brief?: string;
}): JSX.Element {
  return (
    <div className="nx-tn-stack" data-testid="tonight-story">
      <Brief text={brief} />
      {story.length === 0 ? (
        <p className="nx-tn-note">
          The server resolved this night and had nothing to say about it.
        </p>
      ) : (
        <div className="nx-tn-story">
          {story.map((row, i) => (
            <div
              key={`${i}-${row.t_unix ?? row.label}`}
              className="nx-tn-story-row"
              data-testid={`tonight-story-row-${i}`}
            >
              <span className="nx-tn-story-stamp">{storyStamp(row)}</span>
              <span className="nx-tn-story-msg" style={{ color: storyToneVar(row.tone) }}>
                {row.msg}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default TonightStoryList;
