// CommonProblems.tsx - the symptom guide (wave R7, T-R7-16).
//
// Ten `Disclosure`s, one per `TROUBLESHOOTING` entry, keyed by the entry's
// own `topic`. Keying by topic is what makes the deep link exact: a diagnosed
// failure's "How to fix" action calls `store.openHelp(topic)`, the bridge turns
// that into `#/settings/help?topic=<topic>`, and the sheet writes the SAME
// store field back through the SAME action, so there is one copy of "which
// problem is being shown" and it is the store's.
//
// THREE THINGS ARE SEPARATE HERE, and collapsing any two of them is a defect:
//
//   * `helpTopic`  - the arriving deep link. It is SPENT after 2500 ms by
//     `clearHelpTopic()` so a later manual visit is not stuck highlighting an
//     error from an hour ago (`views/HelpView.tsx:55-62`).
//   * the HIGHLIGHT - drawn only while `helpTopic` names the entry, so it goes
//     when the topic is spent.
//   * the OPEN entry - `openTopic`, which the deep link SETS and the timer does
//     NOT clear. Closing the section the reader is mid-way through, 2.5 seconds
//     after it opened, would be the fix for a problem nobody has.
//
// Exactly one entry is open at a time by construction (`openTopic` is a single
// value, and every `Disclosure` here is controlled by it), which is what the
// "open exactly one" parity assertion measures.
//
// Every string is rendered through `hyphenate()`: `TROUBLESHOOTING` is shared
// with `#/classic` and carries em-dashes and en-dashes at source, which this
// UI's copy rules forbid, and the file is not ours to edit.

import { useEffect, useRef, useState, type JSX } from "react";
import { Card, Disclosure, Label } from "../../../ui";
import { useStore } from "../../../../store";
import { TROUBLESHOOTING } from "../../../../lib/troubleshoot";
import { HIGHLIGHT_MS, hyphenate, problemSub, seeAlsoLine } from "./helpModel";

export function CommonProblems(): JSX.Element {
  const helpTopic = useStore((s) => s.helpTopic);
  const clearHelpTopic = useStore((s) => s.clearHelpTopic);
  const [openTopic, setOpenTopic] = useState<string | null>(null);
  const activeRef = useRef<HTMLDivElement | null>(null);

  // The deep link opens its entry. Separate from the effect below because it
  // must NOT be undone when the topic is spent: see the header note.
  useEffect(() => {
    if (helpTopic) setOpenTopic(helpTopic);
  }, [helpTopic]);

  // Scroll it into view, then one-shot clear so the highlight does not outlive
  // the arrival. `window.setTimeout`, not the bare one, so the cleanup and the
  // arming are provably the same clock.
  useEffect(() => {
    if (!helpTopic) return;
    activeRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    const t = window.setTimeout(() => clearHelpTopic(), HIGHLIGHT_MS);
    return () => window.clearTimeout(t);
  }, [helpTopic, clearHelpTopic]);

  return (
    <section className="nx-help-block" aria-label="Common problems">
      <Label size={11}>COMMON PROBLEMS</Label>
      <Card padding={0}>
        {TROUBLESHOOTING.map((e) => {
          const active = e.topic === helpTopic;
          const see = seeAlsoLine(e);
          return (
            <div
              key={e.topic}
              ref={active ? activeRef : undefined}
              className="nx-help-problem"
              data-topic={e.topic}
              data-active={active ? "true" : "false"}
              aria-current={active ? "true" : undefined}
              data-testid="help-problem"
            >
              <Disclosure
                summary={hyphenate(e.symptom)}
                sub={problemSub(e, active)}
                open={openTopic === e.topic}
                onToggle={(next) => setOpenTopic(next ? e.topic : null)}
              >
                <div className="nx-help-body">
                  <p className="nx-help-cause">{hyphenate(e.cause)}</p>
                  <ol className="nx-help-steps">
                    {e.steps.map((s, i) => <li key={i}>{hyphenate(s)}</li>)}
                  </ol>
                  {see != null && <p className="nx-help-see">{see}</p>}
                </div>
              </Disclosure>
            </div>
          );
        })}
      </Card>
    </section>
  );
}
