// ResultCard.tsx - what landed, and what can still be done about it.
//
// SAVE TO GALLERY IS REAL NOW (D-SES-4). This file used to say the opposite,
// and it was right at the time: `POST /api/capture` decided at shutter time or
// not at all, so a button labelled SAVE TO GALLERY would either do nothing or
// quietly re-shoot behind the operator's back. The engine now HOLDS the last
// unsaved frame per camera and `POST /api/capture/last/save` writes those
// pixels - the same pixels on screen, not a second exposure of a sky that has
// moved. So the primary verb is SAVE TO GALLERY when the rig confirms it is
// holding the frame, and RE-SHOOT AND SAVE keeps working beside it.
//
// THE OFFER IS ONLY MADE WHEN THE RIG CONFIRMS IT. `useLastFrame` reads
// `GET /api/capture/last` on mount; an older engine 404s and the card renders
// exactly what it shipped with. Nothing here infers "there must be a frame
// buffered" from the fact that a capture just ran with `save: false`.
//
// THE frame_id INTERLOCK is why this card can be trusted after a minute of
// looking at it. The press names the frame the card is showing; if a later
// exposure has replaced it, the rig refuses and says so instead of writing a
// frame the operator never saw. See api/capture.ts.
//
// When the frame WAS saved the slot is a link to where it went instead, because
// "saved" and "here is where" are different facts and only one of them is
// already on screen.

import type { JSX } from "react";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import { useLastFrame } from "./lastFrame";
import type { PreviewInfo } from "../../../../types";

/** The frame is not on disk and the rig is not holding it either - the only way
 *  to keep this framing is another exposure. */
export const NOT_SAVED_NOTE =
  "Nothing was written - this re-takes the frame and saves it.";
/** The frame is not on disk but the rig still has the pixels, which is a
 *  different offer and a different cost. */
export const PROMOTABLE_NOTE =
  "Nothing was written - the rig is still holding this frame, so it can be "
  + "saved without re-shooting it.";
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

/** What SAVE AS would change, in the words of the two names involved.
 *
 *  Null when there is nothing to offer: no target on the bench, or the same one
 *  the frame already carries. The sentence names the header and the cards
 *  because the override re-runs the identification server-side - it is not just
 *  a label on a row. */
export function targetOverrideNote(
  frameTarget: string | null,
  captureTarget: string,
): string | null {
  const want = captureTarget.trim();
  const have = (frameTarget ?? "").trim();
  if (!want || want === have) return null;
  return have
    ? `The buffered frame is filed under ${have}. SAVE AS files it under `
      + `${want} instead, and the header and identification cards follow.`
    : `This frame was shot with no target name. SAVE AS files it under `
      + `${want}, and writes the header and identification cards to match.`;
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
  /** The target typed on the capture screen right now, for the SAVE AS offer.
   *
   *  Optional with an empty default so the card is correct with no wiring at
   *  all: with nothing passed, the override is simply never offered and every
   *  other behaviour is unchanged. There is deliberately NO free-text field
   *  here - the target box is one screen up, and a second one would be a second
   *  truth about the same frame. */
  captureTarget?: string;
}

export function ResultCard(props: ResultCardProps): JSX.Element {
  // Disabled for a frame that was already written at shutter time: there is
  // nothing in the buffer to promote, and reading it would be one request per
  // saved frame for an answer that is always "nothing held".
  const promote = useLastFrame(!props.saved);
  const inLibrary = props.saved || promote.done;
  const override = promote.offered
    ? targetOverrideNote(promote.frame?.target ?? null, props.captureTarget ?? "")
    : null;
  const overrideTarget = (props.captureTarget ?? "").trim();

  return (
    <Card tone="accent" data-testid="capture-result">
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        gap: 8, flexWrap: "wrap",
      }}>
        <Label>{inLibrary ? "CAPTURED · IN THE LIBRARY" : "CAPTURED · NOT SAVED"}</Label>
        <Mono size={10} tone="dim">
          {`${props.count} × ${props.exposureS} s · ${props.filter} · gain ${props.gain}`}
        </Mono>
      </div>
      <div style={{ marginTop: 6 }}>
        <Mono size={11}>{statsLine(props.preview)}</Mono>
      </div>
      {promote.saved && (
        <div style={{ marginTop: 6 }}>
          <Mono size={10} tone="good" data-testid="result-saved-path">
            {`Saved as ${promote.saved.path}`}
          </Mono>
        </div>
      )}
      {!inLibrary && (
        <div style={{ marginTop: 6 }}>
          <Mono size={10} tone="warn">
            {promote.offered ? PROMOTABLE_NOTE : NOT_SAVED_NOTE}
          </Mono>
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
        {inLibrary ? (
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
          <>
            {promote.offered && (
              <ActionButton
                kind="primary"
                onPress={() => promote.save()}
                lockedReason={promote.saveReason}
                onExplain={promote.onExplain}
                busy={promote.saving}
                data-testid="result-save-gallery"
              >
                SAVE TO GALLERY
              </ActionButton>
            )}
            <ActionButton
              kind={promote.offered ? "secondary" : "primary"}
              onPress={props.onReshootAndSave}
              lockedReason={props.actionReason}
              onExplain={props.onExplain}
              data-testid="result-save"
            >
              RE-SHOOT AND SAVE
            </ActionButton>
            {override != null && (
              <ActionButton
                kind="secondary"
                onPress={() => promote.save(overrideTarget)}
                lockedReason={promote.saveReason}
                onExplain={promote.onExplain}
                busy={promote.saving}
                data-testid="result-save-as-target"
              >
                {`SAVE AS ${overrideTarget}`}
              </ActionButton>
            )}
          </>
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
      {override != null && (
        <div style={{ marginTop: 6 }}>
          <Mono size={10} tone="dim">{override}</Mono>
        </div>
      )}
      {promote.note != null && (
        <div style={{ marginTop: 6 }}>
          <Mono size={10} tone="warn" data-testid="result-promote-note">
            {promote.note}
          </Mono>
        </div>
      )}
    </Card>
  );
}
