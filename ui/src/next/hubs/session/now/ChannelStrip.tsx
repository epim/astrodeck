// ChannelStrip.tsx - which channels the stack holds, and which one is on screen.
//
// TAPPING A CHIP FETCHES THAT CHANNEL (D-SES-1). `GET /api/sequence/stack/
// preview.jpg` now takes `channel=`, answered from the stacker's own per-channel
// accumulator (`imaging/sessionstack.py` `channel_preview`), so the picture
// really is Ha alone rather than the colour composite with a tint over it. The
// chip therefore has to name something the route can resolve, which is why the
// chips are `status.channels[].channel` - the stacker's keys, R/G/B/L/Ha/Oiii/
// Sii - and nothing else.
//
// THIS MOVED A NUMBER SOMEBODY READS, so it is stated here. The chips used to be
// the session ledger's per-FILTER rows and their counts were THIS NIGHT'S
// ACCEPTED SUBS. Those are the WHEEL's names ("Ha", "H-alpha", "S2" for the same
// glass); the route resolves a name only when `channel_for` maps it, and an
// unmapped one folds onto L, so a chip built from a wheel name either 404s or
// quietly shows L's picture under another label. The ledger counts are not lost:
// they are the line under the strip, and it says which count is which. That line
// appears only when the two disagree, which they do whenever the stack was
// switched on mid-night (it counts from the press, the ledger counts from dusk)
// or whenever two filters fold onto one channel.
//
// AND THE LEDGER OUTLIVES THE STACK (T-R7-21a item 10). The counts under the
// strip are the NIGHT'S: `acceptedByFilter` reads the session's frames, which
// are on disk whether or not anybody asked the stacker to build a picture out
// of them. Switching the live stack off used to take the whole strip away and
// the only per-filter tally on the screen with it. With the stack off and no
// channels, the ledger line now renders alone and says the stack is off, so a
// count is never left standing with no explanation for the missing chips - and
// "off" is only printed once the rig has actually said so (`answered`).

import type { JSX } from "react";

import { Chip, Mono, Segmented } from "../../../ui";
import {
  acceptedByFilter, filterColor, filterToken, plannedByFilter, tonightNightKey,
} from "./filters";
import { useActiveSession } from "./sessionData";
import { paletteWord } from "./useCampaign";
import { useSessionStackStatus, useStackView, type StretchMode } from "./stackView";

const STRETCH_OPTIONS: { value: StretchMode; label: string }[] = [
  { value: "SOFT", label: "SOFT" },
  { value: "AUTO", label: "AUTO" },
  { value: "HARD", label: "HARD" },
];

interface Row { name: string; count: number }

/** One key per piece of glass, so "H-alpha" from the wheel and "Ha" from the
 *  stacker are compared as the same thing and an unmapped name still compares
 *  as itself rather than collapsing onto L. */
const foldKey = (name: string): string => filterToken(name) ?? name.trim().toUpperCase();

/** "H-alpha 12, Oiii 4" - the ledger's OWN names, which are the wheel's. */
const ledgerWords = (rows: readonly Row[]): string =>
  rows.map((r) => `${r.name} ${r.count}`).join(", ");

/** Do the ledger's per-filter counts say something the chips do not? Same
 *  channels with the same totals is one fact printed twice; anything else is
 *  two facts and the user needs both. */
export function ledgerDiffers(chips: readonly Row[], ledger: readonly Row[]): boolean {
  if (ledger.length === 0) return false;
  const fold = (rows: readonly Row[]): Map<string, number> => {
    const m = new Map<string, number>();
    for (const r of rows) m.set(foldKey(r.name), (m.get(foldKey(r.name)) ?? 0) + r.count);
    return m;
  };
  const a = fold(chips);
  const b = fold(ledger);
  if (a.size !== b.size) return true;
  for (const [k, v] of b) if (a.get(k) !== v) return true;
  return false;
}

/** The ledger line while the stack has not answered. "Live stack off" is a
 *  claim about the rig; before the first `GET /api/sequence/stack` lands there
 *  is nothing to claim, and the night's counts are true either way. */
export const UNANSWERED_LEDGER = "The stack has not answered yet.";

export function ChannelStrip(): JSX.Element | null {
  const { session } = useActiveSession();
  const { status, answered } = useSessionStackStatus();
  const { channel, setChannel, stretch, setStretch } = useStackView();

  // The offered channels ARE the stack's channels. A chip for a channel the
  // stack has no accumulator for is a chip that 404s.
  const rows: Row[] = (status?.channels ?? []).map((c) => ({ name: c.channel, count: c.frames }));

  const planned = plannedByFilter(session?.plan);
  const accepted = acceptedByFilter(session, tonightNightKey(session));
  const ledger: Row[] = planned.map((p) => ({ name: p.filter, count: accepted.get(p.filter) ?? 0 }));

  if (rows.length === 0 && !status?.enabled) {
    // Nothing to say about the stack, and nothing shot yet either.
    if (ledger.length === 0) return null;
    // THE UNANSWERED BRANCH COMES FIRST. `status == null` is both "the poll has
    // not come back" and "the poll failed", and printing "Live stack off" over
    // either one tells an operator the rig is not building a picture when
    // nothing has asked it yet.
    return (
      <div data-testid="now-channel-strip" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span data-testid="channel-ledger-note">
          <Mono size={10} tone="dim">
            {`${answered ? "Live stack off." : UNANSWERED_LEDGER} `
              + `This night's accepted subs: ${ledgerWords(ledger)}.`}
          </Mono>
        </span>
      </div>
    );
  }

  // Nothing stacked yet: the plan says what the composite is GOING to be, and
  // when there is no plan either the chip says what it is rather than guessing
  // a palette out of an empty list (`paletteWord([])` answers OSC).
  const palette = rows.length > 0
    ? paletteWord(rows.map((r) => r.name))
    : planned.length > 0 ? paletteWord(planned.map((p) => p.filter)) : "COMBINED";

  const ledgerLine = ledgerDiffers(rows, ledger)
    ? `Chips count what the stack holds. This night's accepted subs: ${ledgerWords(ledger)}.`
    : null;

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
      {ledgerLine && (
        <span data-testid="channel-ledger-note">
          <Mono size={10} tone="dim">{ledgerLine}</Mono>
        </span>
      )}
    </div>
  );
}
