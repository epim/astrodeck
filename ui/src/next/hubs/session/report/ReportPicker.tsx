// ReportPicker.tsx - which night is being read, and the three states the
// index itself can be in.
//
// A `<select>` in the legacy view. Here it is a `Disclosure` over `ListRow`s:
// the closed row already answers "which night am I looking at" without opening
// anything, each choice is a 44 px target instead of a 16 px option, and the
// row can carry the end reason and the frame count that the option's one line
// of text had no room for. Opening it is the only thing that costs a list.
//
// The three index states are NOT collapsed. "No reports yet" is a claim about
// the user's data; a dropped request is a claim about the network; and "we
// have not asked yet" is neither. Each says which it is, and the failure gets
// the RETRY the empty state could never offer.

import type { JSX } from "react";
import { ActionButton, Disclosure, EmptyCard, ListRow, Mono, Pill } from "../../../ui";
import { NxIcon } from "../../../icons";
import type { SessionReportSummary } from "../../../../types";
import { endReasonTone, endReasonWord, fmtStamp, REPORT_COPY } from "./reportModel";

export function ReportPicker({
  list, listLoading, listErr, onRetry, sel, onSelect,
}: {
  list: SessionReportSummary[];
  listLoading: boolean;
  listErr: string | null;
  onRetry: () => void;
  sel: string | null;
  onSelect: (id: string) => void;
}): JSX.Element {
  const current = list.find((r) => r.id === sel) ?? null;

  if (listLoading && list.length === 0) {
    return (
      <Mono size={10.5} tone="dim" data-testid="report-picker">
        {REPORT_COPY.indexLoading}
      </Mono>
    );
  }

  if (listErr && list.length === 0) {
    return (
      <div className="nx-report-block" data-testid="report-picker">
        <p className="nx-report-note" data-tone="warn">
          {REPORT_COPY.indexError(listErr)}
        </p>
        <div className="nx-report-actions">
          <ActionButton
            kind="secondary"
            onPress={onRetry}
            data-testid="report-index-retry"
            glyph={<NxIcon name="refresh" size={14} />}
          >
            {REPORT_COPY.retryIndex}
          </ActionButton>
        </div>
      </div>
    );
  }

  if (list.length === 0) {
    return (
      <EmptyCard
        data-testid="report-picker"
        title={REPORT_COPY.noReportsTitle}
        hint={REPORT_COPY.noReportsHint}
      />
    );
  }

  // The sub is what the closed row is FOR: the night on screen, named, so the
  // group never has to be opened to know which report these numbers belong to.
  const sub = current
    ? `${current.plan_name} · ${fmtStamp(current.started_at)}`
    : `${list.length} on the rig, none picked`;

  return (
    <Disclosure
      summary="NIGHT"
      sub={sub}
      data-testid="report-picker"
    >
      <div className="nx-report-block">
        {list.map((r) => (
          <ListRow
            key={r.id}
            data-testid={`report-pick-${r.id}`}
            icon={<NxIcon name="session" size={16} />}
            title={r.plan_name}
            sub={`${fmtStamp(r.started_at)} · ${r.frames_captured} accepted`}
            right={
              r.id === sel
                ? <Pill tone="accent">SHOWING</Pill>
                : <Pill tone={endReasonTone(r.end_reason)}>{endReasonWord(r.end_reason)}</Pill>
            }
            onPress={() => onSelect(r.id)}
          />
        ))}
      </div>
    </Disclosure>
  );
}
