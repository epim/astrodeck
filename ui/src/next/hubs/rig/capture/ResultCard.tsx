// ResultCard.tsx - what landed, and the three things to do about it.
//
// THE ONE PLACE THIS SCREEN CONTRADICTS THE DESIGN, and it is a correction, not
// a shortfall (deviation D14): the prototype's `SAVE TO GALLERY` cannot exist.
// `POST /api/capture` writes at capture time or not at all - `CaptureBody`
// carries `save`, the hub decides then, and there is NO route that promotes an
// unsaved preview to a library file. A button labelled SAVE TO GALLERY would
// either do nothing or quietly re-shoot behind the operator's back. So it says
// RE-SHOOT AND SAVE, and the card says why in one line.
//
// When the frame WAS saved the slot is a link to where it went instead, because
// "saved" and "here is where" are different facts and only one of them is
// already on screen.

import type { JSX } from "react";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import type { PreviewInfo } from "../../../../types";

export const NOT_SAVED_NOTE =
  "Nothing was written - this re-takes the frame and saves it.";
export const NO_TARGET_FLOW_REASON =
  "Lock a target in SKY first - a flow needs somewhere to point.";

/** `HFR 2.1" · 1,204 stars · mean 812 ADU`, or the honest subset. Every number
 *  comes off the landed `PreviewInfo`; a missing one is omitted, never zeroed. */
export function statsLine(p: PreviewInfo | null): string {
  if (!p) return "no statistics for this frame";
  const bits: string[] = [];
  if (p.hfr != null) bits.push(`HFR ${p.hfr.toFixed(2)}"`);
  const stars = p.star_list?.length ?? p.stars ?? null;
  if (stars != null) bits.push(`${stars.toLocaleString("en")} stars`);
  if (p.stats?.mean != null) bits.push(`mean ${Math.round(p.stats.mean)} ADU`);
  return bits.length ? bits.join(" · ") : "no statistics for this frame";
}

export interface ResultCardProps {
  preview: PreviewInfo | null;
  saved: boolean;
  count: number;
  exposureS: number;
  filter: string;
  gain: number;
  onReshootAndSave: () => void;
  onOpenLibrary: () => void;
  onRetake: () => void;
  onMakeFlow: () => void;
  actionReason: string | null;
  flowReason: string | null;
  onExplain: (reason: string) => void;
}

export function ResultCard(props: ResultCardProps): JSX.Element {
  return (
    <Card tone="accent" data-testid="capture-result">
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        gap: 8, flexWrap: "wrap",
      }}>
        <Label>{props.saved ? "CAPTURED · IN THE LIBRARY" : "CAPTURED · NOT SAVED"}</Label>
        <Mono size={10} tone="dim">
          {`${props.count} × ${props.exposureS} s · ${props.filter} · gain ${props.gain}`}
        </Mono>
      </div>
      <div style={{ marginTop: 6 }}>
        <Mono size={11}>{statsLine(props.preview)}</Mono>
      </div>
      {!props.saved && (
        <div style={{ marginTop: 6 }}>
          <Mono size={10} tone="warn">{NOT_SAVED_NOTE}</Mono>
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
        {props.saved ? (
          <ActionButton
            kind="primary"
            onPress={props.onOpenLibrary}
            lockedReason={null}
            onExplain={props.onExplain}
            data-testid="result-open"
          >
            OPEN IN GALLERY
          </ActionButton>
        ) : (
          <ActionButton
            kind="primary"
            onPress={props.onReshootAndSave}
            lockedReason={props.actionReason}
            onExplain={props.onExplain}
            data-testid="result-save"
          >
            RE-SHOOT AND SAVE
          </ActionButton>
        )}
        <ActionButton
          kind="secondary"
          onPress={props.onRetake}
          lockedReason={props.actionReason}
          onExplain={props.onExplain}
          data-testid="result-retake"
        >
          RETAKE
        </ActionButton>
        <ActionButton
          kind="purple"
          onPress={props.onMakeFlow}
          lockedReason={props.flowReason}
          onExplain={props.onExplain}
          data-testid="result-flow"
        >
          &rarr; FLOW
        </ActionButton>
      </div>
    </Card>
  );
}
