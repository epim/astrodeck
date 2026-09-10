// SessionCard.tsx - one night on the Gallery shelf.
//
// Four facts and six verbs. The facts are the thumbnail, the sub count, what
// this phone already has, and the session's state; the verbs live behind the
// overflow control so the card stays a picture and not a toolbar.
//
// THE STATE CHIP IS A WORD, NOT A HUE. `SessionsPanel`'s `StatusChip` is
// module-private, so its status->word/tone mapping is transcribed here rather
// than imported (and `SessionsPanel.tsx` is not this task's file to edit).
// Under `:root.night` every token collapses toward the same red, so the word
// IS the state and the colour only reinforces it.
//
// THE ON-PHONE LINE IS A CONVENIENCE, NOT A CLAIM ABOUT THE RIG. It reads
// `localStorage["astrodeck-next-dl"]`, which records that a download was
// STARTED from this phone - a plain `<a download>` navigation reports nothing
// back, so "on this phone" is as strong a claim as the evidence supports.

import { useEffect, useRef, useState, type JSX } from "react";

import { u } from "../../../../lib/base";
import { acquire, noteSucceeded } from "../../../../lib/thumbQueue";
import { fmtIntegration } from "../../../../api/sessionStack";
import { sessionDates } from "../../../../components/sequence/sessionDates";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import { IconButton48, Mono, Pill, Popover } from "../../../ui";
import type { Tone } from "../../../ui";
import { downloadedLine, type DlEntry } from "../sheets/filesData";
import {
  ARMED_WITHOUT_MONITOR_CHIP, runAbandon, runAutoResume, runDelete,
  runResume, runUpdateFromPlan, verbsFor, type Verb,
} from "./cardActions";
import type { SessionCardData } from "./useSessionCards";
import type { SequencePlan } from "../../../../types";

/** `SessionsPanel.tsx:24`'s mapping, transcribed. */
export function statusChip(status: SessionCardData["status"]): { word: string; tone: Tone } {
  switch (status) {
    case "active": return { word: "ACTIVE", tone: "accent" };
    case "dormant": return { word: "DORMANT", tone: "warn" };
    case "complete": return { word: "COMPLETE", tone: "good" };
    case "abandoned": return { word: "ABANDONED", tone: "dim" };
    default: return { word: "REPORT ONLY", tone: "dim" };
  }
}

export function SessionCard({
  card, thumb, stackUrl, live, dl, canControl, monitorConnected, plan, onChanged,
}: {
  card: SessionCardData;
  /** undefined = not resolved yet; null = there is none. */
  thumb: string | null | undefined;
  /** The live composite, for the session a run is writing to right now. */
  stackUrl: string | null;
  live: boolean;
  dl: DlEntry | undefined;
  canControl: boolean;
  monitorConnected: boolean;
  plan: SequencePlan | null;
  onChanged: () => void;
}): JSX.Element {
  const [menu, setMenu] = useState(false);
  const [broken, setBroken] = useState(false);
  const anchor = useRef<HTMLSpanElement | null>(null);

  const url = live && stackUrl ? stackUrl : thumb ? u(thumb) : null;

  // THE SAME QUEUE THE FRAME GRID USES. Six pictures in flight at once, and one
  // 429 parks every pending one - the relay's token bucket is per-IP, so a
  // shelf that set every `src` on mount would drain it exactly the way 41
  // gallery tiles once did (lib/thumbQueue.ts, measured 2026-08-10). The SRC is
  // what waits, not a fetch, so the happy path stays a plain <img> with HTTP
  // caching and no Blob to revoke.
  const [slotted, setSlotted] = useState(false);
  const release = useRef<(() => void) | null>(null);
  useEffect(() => {
    setSlotted(false);
    setBroken(false);
    if (!url) return;
    const { slot, cancel } = acquire();
    let alive = true;
    void slot.then((done) => {
      release.current = done;
      if (!alive) { done(); return; }
      setSlotted(true);
    });
    return () => {
      alive = false;
      cancel();
      // Every path out gives the slot back: a card that keeps one shrinks the
      // cap permanently, and enough of them deadlock the shelf.
      release.current?.();
      release.current = null;
    };
  }, [url]);

  const settleImage = (ok: boolean) => {
    release.current?.();
    release.current = null;
    if (ok) noteSucceeded();
    else setBroken(true);
  };

  const src = slotted ? url : null;
  const chip = statusChip(card.status);
  const line = downloadedLine(dl, card.accepted, live);
  const meta = [
    sessionDates(card.createdTs, card.updatedTs),
    `${card.accepted} subs`,
    card.integrationS != null ? fmtIntegration(card.integrationS) : null,
  ].filter(Boolean).join(" · ");

  const open = () => {
    if (card.id) { nav.sheet("files", { src: card.id }); return; }
    if (card.reportId) { nav.sheet("report", { id: card.reportId }); return; }
  };

  const fire = (v: Verb) => {
    setMenu(false);
    if (v.reason) { explainLock(v.reason); return; }
    switch (v.id) {
      case "resume": if (card.id) void runResume(card.id, onChanged); break;
      case "update": if (card.id) void runUpdateFromPlan(card.id, plan, onChanged); break;
      case "autoResume":
        if (card.id) void runAutoResume(card.id, !card.autoResume, monitorConnected, onChanged);
        break;
      case "report": if (card.reportId) nav.sheet("report", { id: card.reportId }); break;
      case "abandon": if (card.id) void runAbandon(card.id, card.name, onChanged); break;
      case "delete": if (card.id) void runDelete(card.id, card.name, onChanged); break;
    }
  };

  const verbs = verbsFor(card, canControl);

  return (
    <div
      className="nx-card"
      data-testid={`session-card-${card.key}`}
      data-tone={live ? "accent" : "default"}
      style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column", minWidth: 0 }}
    >
      <button
        type="button"
        onClick={open}
        aria-label={`Open ${card.name}`}
        data-testid={`session-open-${card.key}`}
        style={{
          position: "relative", height: 96, width: "100%", background: "#000",
          border: 0, padding: 0, cursor: "pointer", display: "block",
        }}
      >
        {src && !broken ? (
          <img
            src={src}
            alt=""
            onLoad={() => settleImage(true)}
            onError={() => settleImage(false)}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          <span
            style={{
              position: "absolute", inset: 6, border: "1px dashed var(--line-bright)",
              borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            {/* Three different answers, and "not looked yet" is not "there is
                none": the ledger read that finds the newest kept frame is
                queued behind the other cards. */}
            <Mono size={10} tone="dim">
              {thumb === undefined && !live ? "reading the ledger"
                : url && !broken ? "loading"
                  : "no preview yet"}
            </Mono>
          </span>
        )}
        <span style={{ position: "absolute", left: 8, top: 8 }}>
          <Pill tone="dim">{`${card.accepted} SUBS`}</Pill>
        </span>
        {live && (
          <span style={{ position: "absolute", right: 8, top: 8 }}>
            <Pill tone="accent">LIVE</Pill>
          </span>
        )}
      </button>

      <div style={{ padding: "8px 10px 10px", display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
          <span className="nx-display" style={{ fontSize: 11.5, letterSpacing: ".1em", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {card.name}
          </span>
          <span ref={anchor} style={{ display: "inline-flex" }}>
            <IconButton48
              glyph={<NxIcon name="layers" size={16} />}
              label="MORE"
              onPress={() => setMenu((m) => !m)}
              data-testid={`session-more-${card.key}`}
            />
          </span>
        </div>
        <Mono size={10} tone="dim">{meta}</Mono>
        <Mono size={10} tone={line.tone}>{line.text}</Mono>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 2 }}>
          <Pill tone={chip.tone}>{chip.word}</Pill>
          {card.autoResume && !monitorConnected && (
            <Pill tone="warn">{ARMED_WITHOUT_MONITOR_CHIP}</Pill>
          )}
        </div>
      </div>

      <Popover open={menu} anchorRef={anchor} onClose={() => setMenu(false)} align="end"
        data-testid={`session-menu-${card.key}`}>
        <div style={{ display: "flex", flexDirection: "column", minWidth: 210 }}>
          {verbs.map((v) => (
            <button
              key={v.id}
              type="button"
              className={v.reason ? "nx-row nx-locked" : "nx-row"}
              aria-disabled={v.reason ? true : undefined}
              title={v.reason ?? undefined}
              data-testid={`session-verb-${v.id}-${card.key}`}
              onClick={() => fire(v)}
            >
              <span className="nx-row-text">
                <span className="nx-row-title" style={v.danger ? { color: "var(--bad)" } : undefined}>
                  {v.label}
                </span>
                {v.reason && <span className="nx-row-sub">{v.reason}</span>}
              </span>
            </button>
          ))}
        </div>
      </Popover>
    </div>
  );
}
