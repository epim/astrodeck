// ChannelStrip.tsx - which channels went into the picture, and how it is shown.
//
// TWO CONTROLS AND ONE SENTENCE, and the sentence is the important part.
//
// Tapping a filter chip does NOT fetch that channel. `GET /api/sequence/stack/
// preview.jpg` takes `size` and `seq` and nothing else - there is no channel
// parameter and the server never renders one - so the chip greyscales the
// COLOUR COMPOSITE and tints it, which is what the prototype does. Left at
// that, the chip would claim a per-channel stack that does not exist, so the
// line under the strip states what the picture actually is. That line is not
// decoration and it is not optional (deviation D4).
//
// The counts are ACCEPTED FRAMES OF THIS NIGHT from the session ledger, not the
// stack's own channel tallies: the stack folds R, G and B onto three composite
// channels and a run with two red filters would come back as one number. When
// there is no session ledger the stack's tallies are used and the chip says
// which filter the STACK is calling it.

import type { JSX } from "react";

import { useSeq } from "../../../../store";
import { Chip, Mono, Segmented } from "../../../ui";
import { acceptedByFilter, filterColor, plannedByFilter, tonightNightKey } from "./filters";
import { useActiveSession } from "./sessionData";
import { paletteWord } from "./useCampaign";
import { useSessionStackStatus, useStackView, type StretchMode } from "./stackView";

export const TINT_NOTE =
  "tinted from the colour composite - the rig does not serve one channel on its own yet.";

const STRETCH_OPTIONS: { value: StretchMode; label: string }[] = [
  { value: "SOFT", label: "SOFT" },
  { value: "AUTO", label: "AUTO" },
  { value: "HARD", label: "HARD" },
];

export function ChannelStrip(): JSX.Element | null {
  const { session } = useActiveSession();
  const { status } = useSessionStackStatus();
  const { channel, setChannel, stretch, setStretch } = useStackView();
  const seq = useSeq();

  const planned = plannedByFilter(session?.plan);
  const accepted = acceptedByFilter(session, tonightNightKey(session));

  // The wheel's own names when there is a ledger; the stack's composite channel
  // names when there is not, because those are then the only names anything on
  // this rig has said out loud.
  const rows = planned.length > 0
    ? planned.map((p) => ({ name: p.filter, count: accepted.get(p.filter) ?? 0 }))
    : (status?.channels ?? []).map((c) => ({ name: c.channel, count: c.frames }));

  if (rows.length === 0 && !status?.enabled) return null;

  const palette = status && Array.isArray(status.channels) && status.channels.length > 0
    ? paletteWord(status.channels.map((c) => c.channel))
    : paletteWord(rows.map((r) => r.name));

  const live = seq.state === "running" || seq.state === "holding" || seq.state === "paused";

  return (
    <div data-testid="now-channel-strip" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <div style={{ flex: 1, display: "flex", gap: 5, overflowX: "auto", minWidth: 0 }}>
          <Chip
            active={channel == null}
            onClick={() => setChannel(null)}
            data-testid="channel-combine"
          >
            {palette}
          </Chip>
          {rows.map((r) => (
            <Chip
              key={r.name}
              active={channel === r.name}
              onClick={() => setChannel(channel === r.name ? null : r.name)}
              data-testid={`channel-${r.name}`}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 7, height: 7, borderRadius: "50%", display: "inline-block",
                  marginRight: 5, background: filterColor(r.name), verticalAlign: "middle",
                }}
              />
              {r.name} {r.count}
            </Chip>
          ))}
        </div>
        <Segmented<StretchMode>
          label="Stretch"
          options={STRETCH_OPTIONS}
          value={stretch}
          onChange={setStretch}
          data-testid="now-stretch"
        />
      </div>
      {channel && (
        <span data-testid="channel-tint-note">
          <Mono size={10} tone="dim">{TINT_NOTE}</Mono>
        </span>
      )}
      {!live && rows.length > 0 && (
        <Mono size={10} tone="dim">counts are this night's accepted subs</Mono>
      )}
    </div>
  );
}
