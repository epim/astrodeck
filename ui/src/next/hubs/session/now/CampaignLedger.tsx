// CampaignLedger.tsx - the purple card: what is banked, per filter, over the
// whole campaign, and what tonight is adding to it.
//
// THE ROWS ARE THE SERVER'S OWN `budget` ARRAY. Nothing here computes a banked
// figure; `flows/tonight.py` folds accepted integration per filter over the
// whole report archive and this draws it. See `useCampaign.ts` for the fetch and
// for why tonight's share is measured from the LIVE ledger instead.
//
// `banked_h === null` IS NOT ZERO, AND IT LOOKS DIFFERENT. A null row draws an
// outlined empty track and reads `- / 6 h`, with the server's own sentence under
// the card. "0 of 45 banked" says the rig looked and found nothing; "not
// counted" says nobody has looked, and only one of those should make somebody
// re-plan a month.
//
// THE NIGHT-STRIP MARK IS THE WORD "held", not a cloud glyph (deviation D3):
// under `:root.night` every token collapses toward the same red, so a status
// carried by hue or by a glyph alone is a status that cannot be read at the
// scope.

import type { JSX } from "react";

import { Label, Mono } from "../../../ui";
import { QuotaRows } from "./QuotaRows";
import { LEDGER_NOTE, useCampaign, type BudgetRow } from "./useCampaign";

function pct(n: number): string { return `${Math.max(0, Math.min(100, n)).toFixed(2)}%`; }

function Row({ row, tonightVisible }: { row: BudgetRow; tonightVisible: boolean }): JSX.Element {
  const goal = row.goal_h > 0 ? row.goal_h : 1;
  const banked = row.banked_h ?? 0;
  const bankedPct = (100 * banked) / goal;
  // The lighter band sits ON TOP of what the archive has banked, because that
  // is where tonight's share actually is: `banked_h` folds FINISHED reports and
  // tonight's frames reach the archive only when the report is written.
  const tonightPct = Math.max(0, Math.min(100 - bankedPct, (100 * row.live_tonight_h) / goal));

  return (
    <div
      data-testid={`ledger-row-${row.filter}`}
      data-has-ledger={row.has_ledger ? "true" : "false"}
      style={{
        display: "grid", gridTemplateColumns: "38px 1fr 84px", gap: 8, alignItems: "center",
      }}
    >
      <span
        className="nx-display"
        style={{ fontSize: 10, letterSpacing: ".08em", color: row.color }}
      >
        {row.filter}
      </span>
      <div
        role="img"
        aria-label={row.has_ledger
          ? `${row.filter}: ${banked.toFixed(1)} of ${row.goal_h} hours banked`
          : `${row.filter}: not counted, goal ${row.goal_h} hours`}
        style={{
          height: 6, borderRadius: 999, position: "relative", overflow: "hidden",
          background: row.has_ledger ? "var(--line)" : "transparent",
          border: row.has_ledger ? "0" : "1px dashed var(--line-bright)",
          boxSizing: "border-box",
        }}
      >
        {row.has_ledger && (
          <div style={{ position: "absolute", inset: 0, width: pct(bankedPct), background: row.color }} />
        )}
        {row.has_ledger && tonightVisible && tonightPct > 0 && (
          <div
            data-testid={`ledger-tonight-${row.filter}`}
            style={{
              position: "absolute", top: 0, bottom: 0,
              left: pct(bankedPct), width: pct(tonightPct),
              background: "rgba(232,236,247,.35)",
            }}
          />
        )}
      </div>
      <span style={{ textAlign: "right" }}>
        <Mono size={10} tone="dim">
          {row.has_ledger ? `${banked.toFixed(1)} / ${row.goal_h} h` : `- / ${row.goal_h} h`}
        </Mono>
      </span>
    </div>
  );
}

export function CampaignLedger(): JSX.Element | null {
  const { campaign, refused, error } = useCampaign();

  if (refused || error) {
    // The server's own refusal, as prose inside the card - never as error
    // chrome. "This flow has no dusk window" is information, not a fault.
    return (
      <div className="nx-card" data-tone="purple" data-testid="now-campaign-ledger">
        <Label>CAMPAIGN LEDGER</Label>
        <Mono size={10.5} tone="dim">{refused ?? `Could not read tonight's plan: ${error}`}</Mono>
      </div>
    );
  }
  if (!campaign) return null;

  const anyTonight = campaign.tonightH > 0;

  return (
    <div
      className="nx-card"
      data-tone="purple"
      data-testid="now-campaign-ledger"
      style={{ display: "flex", flexDirection: "column", gap: 10 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <Label>CAMPAIGN LEDGER</Label>
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <Mono size={10} tone="dim">{campaign.summary}</Mono>
        </span>
      </div>

      <div style={{ display: "flex", gap: 4, alignItems: "flex-end" }} data-testid="ledger-nights">
        {campaign.nights.map((n) => (
          <div key={n.key} style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
            <div style={{
              height: 8, borderRadius: 4, overflow: "hidden", position: "relative",
              border: `1px solid ${n.kind === "planned" ? "var(--line-bright)" : n.color}`,
              borderStyle: n.kind === "planned" ? "dashed" : "solid",
              background: "transparent", boxSizing: "border-box",
            }}>
              {n.fill > 0 && (
                <div style={{ height: "100%", width: pct(n.fill * 100), background: n.color }} />
              )}
            </div>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              <Mono size={10} tone="dim">{n.label}</Mono>
            </span>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {campaign.budget.map((r) => (
          <Row key={r.filter} row={r} tonightVisible={anyTonight} />
        ))}
      </div>

      <Mono size={10} tone="dim">{LEDGER_NOTE}</Mono>
      {campaign.ledgerNote && (
        <span data-testid="ledger-no-ledger-note">
          <Mono size={10} tone="warn">{campaign.ledgerNote}</Mono>
        </span>
      )}

      <QuotaRows />
    </div>
  );
}
