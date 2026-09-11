import type { JSX } from "react";
import { lockedAttrs, lockedClass } from "./honest";
import type { Incident } from "./types";

/** Two clock digits, local time, from an epoch. Kept here rather than pulled
 *  from `next/lib/format.ts` because primitives may not depend on the libs;
 *  it is four lines and it has no other caller. */
function clockOf(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** `since 23:12 - 8 min - resolves itself if it can | needs you`. The last
 *  clause is the whole point of the line: it tells the user whether to put the
 *  phone down or their boots on. */
export function sinceLine(inc: Incident, nowMs: number): string {
  const tail = inc.resolvesItself ? "resolves itself if it can" : "needs you";
  if (inc.sinceMs == null) return tail;
  const mins = Math.max(0, Math.floor((nowMs - inc.sinceMs) / 60000));
  return `since ${clockOf(inc.sinceMs)} - ${mins ? `${mins} min` : "just now"} - ${tail}`;
}

/** The incident card. Border, glow and the primary action all take the
 *  incident's own colour; the ENGINE and NEXT rows are the two sentences the
 *  README requires - what the rig is doing right now, and what it will do on
 *  its own. An action the caller cannot authorise renders honest-disabled with
 *  its reason instead of vanishing, so a viewer sees the same card an operator
 *  does. */
export function IncidentCard({ incident, onAction, lockedFor, onExplain, glyph, nowMs, className = "", ...rest }: {
  incident: Incident;
  onAction: (id: string) => void;
  lockedFor?: (id: string) => string | null;
  /** How a blocked action's reason reaches the user (section 6: primitives have
   *  no store, the caller nominates the channel). */
  onExplain?: (reason: string) => void;
  /** The kind's glyph. Supplied by the caller so this file needs no icon map. */
  glyph?: JSX.Element;
  /** Injectable clock; defaults to now. */
  nowMs?: number;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const color = incident.color;
  return (
    <div
      className={`nx-incident ${className}`.trim()}
      data-kind={incident.kind}
      style={{ borderColor: color, boxShadow: `0 0 22px ${color}33` }}
      data-testid={rest["data-testid"]}
    >
      <div className="nx-incident-head">
        <span className="nx-incident-tile" style={{ borderColor: color, color, background: `${color}1f` }}
          aria-hidden="true">{glyph}</span>
        <span className="nx-incident-titles">
          <span className="nx-incident-title" style={{ color }}>{incident.title}</span>
          <span className="nx-incident-since">{sinceLine(incident, nowMs ?? Date.now())}</span>
        </span>
      </div>
      <div className="nx-incident-rows">
        <div className="nx-incident-row">
          <span className="nx-incident-key">ENGINE</span>
          <span className="nx-incident-val">{incident.engine}</span>
        </div>
        <div className="nx-incident-row">
          <span className="nx-incident-key">NEXT</span>
          <span className="nx-incident-val" data-dim="true">{incident.next}</span>
        </div>
      </div>
      <div className="nx-incident-actions">
        {incident.actions.map((a) => {
          const reason = lockedFor?.(a.id) ?? null;
          return (
            <button
              key={a.id}
              type="button"
              className={lockedClass(reason, "nx-incident-action")}
              data-action={a.id}
              data-primary={a.primary ? "true" : undefined}
              style={a.primary ? { borderColor: color, background: `${color}1f`, color } : undefined}
              onClick={() => (reason ? onExplain?.(reason) : onAction(a.id))}
              {...lockedAttrs(reason)}
            >
              {a.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
