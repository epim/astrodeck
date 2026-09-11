// subContext.ts - the ONE place the shell assembles what the sub-nav chips are
// allowed to know (`hubs/index.ts`'s `SubContext`).
//
// WHY THE CHIPS CARRY NUMBERS AT ALL. A chip that says only "ALERTS" is a label
// for a destination already on the screen; the reason the row is worth its
// height is that "ALERTS 3" answers "is anything unsent" without opening it.
// Every field below is a number or a state the hub body would otherwise be the
// only place to learn - and every one of them is dropped rather than guessed
// when nothing has reported it (`null`, which renders as no count, is a
// different claim from `0`).
//
// `subs(ctx)` stays a pure function of this bag, so the chips are unit-testable
// (`next/__tests__/hubMeta.test.ts`) and the shell reads each field with one
// narrow selector instead of subscribing the whole row to every status poll.

import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { getAlertHealth } from "../../api/alerts";
import { useCapability } from "../../lib/caps";
import { useStore } from "../../store";
import type { SubContext } from "../hubs";
import type { Tone } from "../ui";
import { SUBS } from "../router";
import { useGalleryCountFrom, useSessionsIndex } from "../hubs/session/gallery/sessionsIndex";
import { useIncidents } from "./useIncidents";

/** Which section the Weather hub was last left on. The hub itself WRITES this
 *  key; the tab bar and the rail read it so a tap reopens where the last one
 *  did. Per-phone, `astrodeck-next-` prefixed, try/catch on every access, and
 *  correct with nothing stored (ARCHITECTURE.md section 9). */
const WX_SUB_KEY = "astrodeck-next-wx-sub";

export function rememberedWeatherSub(): string | undefined {
  let raw: string | null = null;
  try { raw = window.localStorage.getItem(WX_SUB_KEY); } catch { return undefined; }
  if (raw == null) return undefined;
  // A stored id the router no longer recognises would send `nav.hub` to a
  // section that does not exist; fall back to the hub's own default.
  return (SUBS.weather as readonly string[]).includes(raw) ? raw : undefined;
}

/** The incident model paints in colour VALUES; a chip dot takes a tone token.
 *  This is the one place the two vocabularies meet, and it keeps the severity:
 *  a safety trip or a failed solve is red, a lost link is dim, everything else
 *  is amber - the same ladder `next/lib/incidents.ts` uses. */
export function toneOfIncident(kind: string): Tone {
  if (kind === "safety" || kind === "solve") return "bad";
  if (kind === "link") return "dim";
  return "warn";
}

/** Undelivered alerts, re-read when the CONFIG changes and never on a timer.
 *
 *  `GET /api/alerts/health` is a pure read of the dispatcher's queue, and the
 *  only thing that changes what a sink is expected to deliver is the config -
 *  so `config.version` is the honest trigger. An interval here would be a
 *  request every N seconds, all night, on a field link, to keep a two-character
 *  count fresh. `null` until it has been read once: no count is not zero. */
function useAlertsUndelivered(): number | null {
  const canView = useCapability("view.status");
  const configVersion = useStore((s) => s.config?.version ?? null);
  const [undelivered, setUndelivered] = useState<number | null>(null);

  useEffect(() => {
    if (!canView) { setUndelivered(null); return; }
    let alive = true;
    getAlertHealth()
      .then((h) => { if (alive) setUndelivered(h.undelivered); })
      // An older server, an offline rig or a lost cap: no count, never a toast.
      .catch(() => { if (alive) setUndelivered(null); });
    return () => { alive = false; };
  }, [canView, configVersion]);

  return undelivered;
}

/** How many devices are connected, and whether any role the rig was told to
 *  bring up did not come up.
 *
 *  `backend_links` is the per-role boot result joined with the role's LIVE
 *  connected state, so it distinguishes the two failures that look identical in
 *  a device count: a role that never connected (`attempted && !ok` - BAD) and
 *  one that connected at boot and has since dropped (`ok && !connected` -
 *  degraded, WARN). `[]` until a profile connect happened, which is why an
 *  empty list is no dot rather than a green one. */
function useRigChip(): { count: number | null; tone: Tone | null } {
  const connected = useStore(useShallow((s) => s.status?.connected ?? null));
  const links = useStore(useShallow((s) => s.status?.backend_links ?? null));
  const bootFailed = useStore((s) => s.status?.boot_connect_failed === true);

  const count = connected
    ? Object.values(connected).filter((d) => d?.connected).length
    : null;

  let tone: Tone | null = null;
  if (bootFailed) tone = "bad";
  if (links && links.length > 0) {
    if (links.some((l) => l.attempted && !l.ok)) tone = "bad";
    else if (tone == null && links.some((l) => l.ok && !l.connected)) tone = "warn";
  }
  return { count, tone };
}

/** The Weather chip's dot. An alert that has NOT been overridden for tonight is
 *  amber; a feed that has gone stale is dim, because a stale forecast is an
 *  absence of information rather than bad news - and rendering the two the same
 *  is how "no data" reads as "clear". */
function useWeatherDot(): Tone | null {
  const alert = useStore((s) => s.weather?.alert != null);
  const ignored = useStore((s) => s.weather?.ignore_tonight === true);
  const stale = useStore((s) => s.weather?.stale === true);
  if (alert && !ignored) return "warn";
  if (stale) return "dim";
  return null;
}

/** How many cards the GALLERY shelf holds - nights the rig still has, which is
 *  sessions PLUS the report-only nights that predate the session ledger, folded
 *  the one way (`sessionsIndex.ts`). The grid reads the same snapshot, so the
 *  chip cannot disagree with the tiles under it.
 *
 *  Gated on `view.status`, which is what `GET /api/sessions` and `GET /api/
 *  reports` require: without it nothing is fetched and the chip carries no
 *  count, rather than the app collecting 403s all night for two characters. */
function useGalleryCount(): number | null {
  const canView = useCapability("view.status");
  const idx = useSessionsIndex(canView);
  const count = useGalleryCountFrom(idx);
  return canView ? count : null;
}

export function useSubContext(nowMs: number): SubContext {
  const flowCount = useStore((s) => (s.flows.libraryLoaded ? s.flows.cards.length : null));
  const incidents = useIncidents(nowMs);
  const alertsUndelivered = useAlertsUndelivered();
  const rig = useRigChip();
  const weatherDot = useWeatherDot();
  // Error lines counted while the log was closed. A plain store field, no
  // derivation and no request: `store.ts:2072` bumps it, `openLog()` clears it,
  // and until now nothing in this UI read it (review #12).
  const unseenError = useStore((s) => s.unseenError);
  const galleryCount = useGalleryCount();

  return {
    flowCount,
    incidentTone: incidents.length > 0 ? toneOfIncident(incidents[0].kind) : null,
    incidentCount: incidents.length,
    alertsUndelivered,
    rigDeviceCount: rig.count,
    rigLinkTone: rig.tone,
    weatherDot,
    galleryCount,
    unseenError,
  };
}
