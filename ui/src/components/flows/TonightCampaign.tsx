// TonightCampaign.tsx — how much of the pool each member has actually banked.
//
// The fourth Tonight tab, new in the 2026-08-14 export. Per-member rows: name,
// a right-aligned mono status, and a 6px pill bar under it; then the projection
// note and an honesty line.
//
// THIS TAB RENDERS WHAT THE SERVER SENT AND COMPUTES NOTHING. The prototype
// hard-codes each member's progress (`fr = [1, 0.51, 0.1, 0, 0, 0]`) and the
// completion estimate ("~6 clear nights"); the README says production reads the
// first from the session ledger and the second from the scheduler. The ledger
// exists and `tonight.py::_campaign` folds it. The projection does not exist, so
// no number is shown for it — see the note the server sends.
//
// `banked === null` IS NOT ZERO, and the distinction is the reason this file has
// three render branches instead of one. "0 of 45 banked" says the rig looked and
// found nothing; "no ledger" says nobody looked. Only the first should make an
// operator re-plan a month, so a null renders as "not counted", never as an
// empty bar at 0%.
import type { JSX } from "react";

export interface CampaignMember {
  name: string;
  /** Complete cycles in the bank, or null when there is no ledger to ask. */
  banked: number | null;
  quota: number;
  done: boolean;
  /** 0-100, or null alongside a null `banked`. */
  pct: number | null;
}

export interface CampaignRead {
  is_campaign: boolean;
  has_pool: boolean;
  has_ledger: boolean;
  quota: number;
  members: CampaignMember[];
  note: string;
}

/** The status text for one member's row.
 *
 *  Exported because it is the whole information content of the row and it is
 *  worth testing without a DOM: the difference between "23/45 cycles",
 *  "45/45 cycles · DONE" and "not counted" is the difference between three
 *  different nights. */
export function memberStatus(m: CampaignMember): string {
  if (m.banked === null) return "not counted";
  return `${m.banked}/${m.quota} cycles${m.done ? " · DONE" : ""}`;
}

function MemberRow({ m }: { m: CampaignMember }): JSX.Element {
  // Shape as well as colour: the do-not list forbids colour-alone status, and a
  // DONE row already differs by its word. The bar's fill token follows.
  const tone = m.banked === null ? "text-faint"
    : m.done ? "text-good" : "text-accent";
  const fill = m.done ? "var(--good)" : "var(--accent)";
  return (
    <div className="flex flex-col gap-1.5" data-campaign-member={m.name}>
      <div className="flex items-baseline gap-3 min-w-0">
        <span className="font-display font-semibold text-[11px] tracking-[0.06em] truncate flex-1">
          {m.name}
        </span>
        <span className={`font-mono text-[10.5px] tabular-nums flex-none ${tone}`}>
          {memberStatus(m)}
        </span>
      </div>
      <div
        className="h-[6px] rounded-full overflow-hidden"
        style={{ background: "color-mix(in srgb, var(--line) 15%, transparent)" }}
        // A bar with no number behind it is a bar that cannot be read, so the
        // unmeasured case gets an explicit accessible value rather than 0.
        role="progressbar"
        aria-label={`${m.name} campaign progress`}
        aria-valuemin={0}
        aria-valuemax={m.quota}
        aria-valuenow={m.banked ?? undefined}
        aria-valuetext={memberStatus(m)}
      >
        {m.pct !== null && (
          <div className="h-full rounded-full"
               style={{ width: `${m.pct}%`, background: fill }} />
        )}
      </div>
    </div>
  );
}

export function TonightCampaign({ campaign }: {
  campaign: CampaignRead | null;
}): JSX.Element {
  if (!campaign) {
    return (
      <p className="text-[12px] leading-[1.5] text-dim [text-wrap:pretty]">
        This flow's campaign state has not been resolved yet.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {campaign.members.length > 0 && (
        <div className="flex flex-col gap-3.5">
          {campaign.members.map((m) => <MemberRow key={m.name} m={m} />)}
        </div>
      )}

      <p className="text-[11.5px] leading-[1.55] text-dim [text-wrap:pretty]">
        {campaign.note}
      </p>

      {/* THE HONESTY LINE, and it says a different thing in each case rather
          than one hedge that covers all of them. A hedge that fits every state
          is one nobody reads. */}
      <p className="font-mono text-[10px] leading-[1.5] text-faint [text-wrap:pretty]">
        {!campaign.has_pool
          ? "Progress is per pool member, so a flow with a single TARGET has nothing to track here."
          : !campaign.has_ledger
            ? "Counts come from the session ledger on the rig. None was readable, so no figure above is claimed."
            : "Counts are accepted subs from the session ledger, folded into complete cycles: a cycle counts only once every filter in the table has its sub."}
      </p>
    </div>
  );
}

export default TonightCampaign;
