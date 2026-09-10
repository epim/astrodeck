// SessionColumn.tsx - the desktop's persistent Session - Now, condensed
// (ARCHITECTURE.md section 4).
//
// PLACEHOLDER. The Session task exports the live stack, the vitals band, the
// integration bar and the incident card from `hubs/session/now/*` and this
// column mounts the same components the hub does - one implementation, two
// densities. What it does today is the part that matters even empty: say
// whether a run is going, and what phase it is in.
//
// It is hidden while the Session hub itself is open. Two copies of the same
// live stack side by side is not redundancy, it is a second thing to check
// against the first.

import type { JSX } from "react";
import { useStore } from "../../store";
import { ActionButton, EmptyCard, Label, StatusPill } from "../ui";
import { nav } from "../router";

function phase(state: string): { text: string; tone: "accent" | "warn" | "bad" | "dim"; pulse: boolean } {
  switch (state) {
    case "running": return { text: "RUNNING", tone: "accent", pulse: true };
    case "paused": return { text: "PAUSED", tone: "warn", pulse: false };
    case "holding": return { text: "HOLDING", tone: "warn", pulse: true };
    case "aborting": return { text: "STOPPING", tone: "bad", pulse: true };
    case "error": return { text: "ERROR", tone: "bad", pulse: true };
    case "nina_native": return { text: "NINA DRIVING", tone: "accent", pulse: true };
    default: return { text: state.toUpperCase(), tone: "dim", pulse: false };
  }
}

export function SessionColumn(): JSX.Element {
  const sequence = useStore((s) => s.sequence);
  const live = ["running", "paused", "holding", "aborting", "nina_native"].includes(sequence.state);

  if (!live) {
    return (
      <aside className="nx-session-col" aria-label="Session" data-testid="session-column">
        <EmptyCard
          title="NO SESSION RUNNING"
          hint="A flow started from Sky or Session appears here and stays visible while you work elsewhere."
          action={
            <ActionButton kind="secondary" onPress={() => nav.go("/session/now")}>
              OPEN SESSION
            </ActionButton>
          }
        />
      </aside>
    );
  }

  const p = phase(sequence.state);
  const prog = sequence.progress;

  return (
    <aside className="nx-session-col" aria-label="Session" data-testid="session-column">
      <div className="nx-session-head">
        <Label>{sequence.plan_name || sequence.target || "SESSION"}</Label>
        <StatusPill text={p.text} tone={p.tone} pulse={p.pulse} data-testid="session-column-phase" />
      </div>
      <EmptyCard
        title="LIVE PANEL NOT BUILT YET"
        hint={
          prog
            ? `${prog.frames_done} of ${prog.frames_total} frames, ${Math.round(prog.percent)}% done. The stack, vitals and incidents land here with the Session hub.`
            : "The stack, vitals and incidents land here with the Session hub."
        }
        action={
          <ActionButton kind="secondary" onPress={() => nav.go("/session/now")}>
            OPEN SESSION
          </ActionButton>
        }
      />
    </aside>
  );
}
