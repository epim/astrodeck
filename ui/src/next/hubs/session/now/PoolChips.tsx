// PoolChips.tsx - which target of the pool is being shot, and which are done.
//
// The prototype marks them with a tick, a filled dot and a hollow dot. At the
// 10 px floor, under `:root.night` where every token is the same red, three
// small circles are three small circles - so the WORD carries the state and the
// colour reinforces it. Same information, one more channel.
//
// Done-ness is the session ledger's, not a guess from `target_index`: a target
// the scheduler set aside for its altitude floor is not "done", and the ledger
// is the only thing that knows the difference.

import type { JSX } from "react";

import { targetProgress } from "../../../../lib/sessions";
import { useSeq } from "../../../../store";
import { Pill } from "../../../ui";
import { useActiveSession } from "./sessionData";

export function PoolChips(): JSX.Element | null {
  const seq = useSeq();
  const { session } = useActiveSession();
  const plan = session?.plan;
  if (!plan || plan.targets.length <= 1) return null;

  const rows = targetProgress(plan, session?.frames ?? []);
  const current = seq.target_index ?? 0;

  return (
    <div data-testid="now-pool" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {rows.map((r, i) => {
        const done = r.total > 0 && r.accepted >= r.total;
        const now = i === current && !done;
        const word = done ? "done" : now ? "now" : "next";
        const tone = done ? "good" : now ? "accent" : "dim";
        return (
          <Pill
            key={r.target_id}
            tone={tone}
            ariaLabel={`${r.name}: ${word}, ${r.accepted} of ${r.total} frames`}
            data-testid={`pool-${r.target_id}`}
          >
            {word} · {r.name} {r.accepted}/{r.total}
          </Pill>
        );
      })}
    </div>
  );
}
