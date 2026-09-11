// EphemerisCard.tsx - the satellite and comet element files, and the one button
// that goes and gets them (D-SKY-1, wave U7b).
//
// WHY IT LIVES IN THE SKY DATA SHEET AND NOWHERE ELSE. The server's own stale
// sentences end "Refresh them from Sky settings when the rig is online."
// (`catalog/ephemeris/elements.py:132-143`). That sentence is shown verbatim
// wherever a stale element set is used - on the passes card, in the targets
// sheet - so the control it names has to exist at the place it names. One
// button, in the sheet the sentence points at.
//
// WHAT IT WILL NOT DO:
//
//   * It will not poll. `GET /api/ephemeris/status` is cheap, but the only
//     thing on it that moves on its own is `age_days`, which creeps by one a
//     day. The 5 s poll runs ONLY while the server says a fetch is in flight
//     and stops on the first tick where `fetching` is empty.
//   * It will not branch on a message. The one refusal that is not an error -
//     a second press while a fetch is running - is `409 code:
//     "already_fetching"` with NO sentence of its own (`routes.py:71-73`
//     answers a flat body). The UI supplies that sentence; the CODE is what
//     the branch reads.
//   * It will not pretend an older engine has the feature. S7L is what mounts
//     this router; a rig without it answers 404 and the card says so and
//     renders no controls. That branch is what lets this UI ship against a rig
//     that has not been updated.
//
// The card styles with inline objects like its neighbours in this folder and
// adds no `nx-*` class, so it ships no stylesheet.

import { useCallback, useEffect, useState, type JSX } from "react";
import { ApiError } from "../../../../api";
import { getEphemerisStatus, refreshEphemeris } from "../../../../api/ephemeris";
import type { EphemerisCacheState, EphemerisStatus } from "../../../../types";
import { NxIcon } from "../../../icons";
import { fmtClock } from "../../../lib/format";
import { isLocalOnly, LOCAL_ONLY_REASON } from "../../../lib/gate";
import { useLock } from "../../../lib/gateHook";
import { ActionButton, Card, Label, LockNote, Mono } from "../../../ui";

const POLL_MS = 5000;

export const EPHEMERIS_TITLE = "SATELLITE AND COMET ELEMENTS";

/** The 202. It says what happens next and where the answer appears, because
 *  the press itself produces no visible change on this card for a few seconds. */
export const REFRESH_STARTED = "Fetching elements - the list updates when it lands.";

/** The 409. Not an error: a fetch is already running and a second press would
 *  be a second request to an upstream that asks to be polled four times a day. */
export const ALREADY_FETCHING = "Elements are already downloading - this will not start a second fetch.";

/** The 404. The routes are mounted by the server spine; a rig that predates it
 *  serves the rest of the UI perfectly well and simply has no ephemerides. */
export const NO_EPHEMERIS_ENGINE = "This engine does not carry satellite or comet ephemerides yet.";

/** The failure with nothing of the server's to quote. */
export const REFRESH_FAILED = "Could not start the fetch.";

const ROW_LABEL: Record<"satellites" | "comets", string> = {
  satellites: "SATELLITES",
  comets: "COMETS",
};

/** One cache's line: how many, from where, when. Every clause is a fact the two
 *  words "SATELLITES" and a tick could not carry. */
export function cacheLine(state: EphemerisCacheState | null | undefined): string {
  if (!state) return "not loaded";
  if (!state.present) return "no elements on the rig";
  const parts = [`${state.count} objects`];
  if (state.source) parts.push(state.source);
  if (typeof state.fetched_unix === "number") {
    parts.push(`fetched ${fmtClock(state.fetched_unix * 1000, state.fetched_unix * 1000)}`);
  }
  if (typeof state.age_days === "number") {
    parts.push(state.age_days < 1 ? "today" : `${Math.round(state.age_days)} days old`);
  }
  return parts.join(" · ");
}

export function EphemerisCard(): JSX.Element {
  // `needsLan`: REFRESH ELEMENTS is `POST /api/ephemeris/refresh`, which makes
  // THIS BOX dial out to CelesTrak and the MPC - the SSRF shape the relay fence
  // exists for (`app.py` `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES`), so it is
  // refused over the tunnel for every role. The GET status stays open.
  const lock = useLock({ cap: "config.site_optics", needsLan: true });
  const [status, setStatus] = useState<EphemerisStatus | null>(null);
  // "absent" is the 404 - the routes are not on this engine at all. It is a
  // different state from "we have not asked yet" and from "the request failed",
  // and only the first of the three means "render no controls".
  const [route, setRoute] = useState<"unknown" | "present" | "absent">("unknown");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const s = await getEphemerisStatus();
      setStatus(s);
      setRoute("present");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { setRoute("absent"); return; }
      setErr(e instanceof Error && e.message ? e.message : REFRESH_FAILED);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  // ONLY while a fetch is in flight, and this component is only mounted while
  // the sheet is open - so an idle Settings screen asks the rig nothing.
  const fetching = (status?.fetching?.length ?? 0) > 0;
  useEffect(() => {
    if (!fetching) return;
    const id = setInterval(() => { void reload(); }, POLL_MS);
    return () => clearInterval(id);
  }, [fetching, reload]);

  const refresh = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      await refreshEphemeris("all");
      setMsg(REFRESH_STARTED);
      await reload();
    } catch (e) {
      // BY CODE, never by message: `routes.py:71-73` sends no sentence at all
      // for this one, so `ApiError.message` is the stringified body.
      if (e instanceof ApiError && e.code === "already_fetching") {
        setMsg(ALREADY_FETCHING);
      } else if (isLocalOnly(e)) {
        // The fence, not a fault and not a missing capability: the refresh
        // dials out from the rig, so it is refused over the relay for every
        // role. Branch before the message fallback, whose text is the server's
        // own "this security-sensitive operation is LAN-only".
        setErr(LOCAL_ONLY_REASON);
      } else if (e instanceof ApiError && e.status === 404) {
        setRoute("absent");
      } else {
        setErr(e instanceof Error && e.message ? e.message : REFRESH_FAILED);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-testid="settings-ephemeris">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <Label size={11}>{EPHEMERIS_TITLE}</Label>

        {route === "absent" ? (
          <p
            data-testid="ephemeris-absent"
            style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}
          >
            {NO_EPHEMERIS_ENGINE}
          </p>
        ) : (
          <>
            {(["satellites", "comets"] as const).map((which) => {
              const state = status?.[which] ?? null;
              return (
                <div
                  key={which}
                  data-testid={`ephemeris-row-${which}`}
                  style={{ display: "flex", flexDirection: "column", gap: 3 }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <Label size={10}>{ROW_LABEL[which]}</Label>
                    <Mono size={10} tone="dim">
                      {status?.fetching?.includes(which) ? "downloading" : cacheLine(state)}
                    </Mono>
                  </div>
                  {state?.note && (
                    <p
                      data-testid={`ephemeris-note-${which}`}
                      style={{
                        margin: 0, fontSize: 11, lineHeight: 1.5,
                        color: state.present ? "var(--warn, #ffb454)" : "var(--text-3, #7683a5)",
                      }}
                    >
                      {state.note}
                    </p>
                  )}
                </div>
              );
            })}

            <ActionButton
              kind="secondary"
              data-testid="ephemeris-refresh"
              busy={busy}
              lockedReason={lock.lockedReason}
              onExplain={lock.onExplain}
              onPress={() => { void refresh(); }}
            >
              REFRESH ELEMENTS
            </ActionButton>

            {msg != null && (
              <p
                role="status"
                data-testid="ephemeris-message"
                style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--text-2, #9aa6c2)" }}
              >
                {msg}
              </p>
            )}

            {err != null && (
              <p
                role="alert"
                data-testid="ephemeris-error"
                style={{ margin: 0, display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--warn, #ffb454)" }}
              >
                <NxIcon name="info" size={12} />
                {err}
              </p>
            )}

            <LockNote reason={lock.lockedReason} data-testid="ephemeris-lock" />
          </>
        )}
      </div>
    </Card>
  );
}
