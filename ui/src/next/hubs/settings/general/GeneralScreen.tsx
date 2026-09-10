// GeneralScreen.tsx - Settings > GENERAL (`#/settings/general`, plan C.2).
//
// The setup card, then five groups of rows: RIG, SKY, PHONE, LIBRARY, MORE.
// Every row is either `nav.sheet(...)` or `nav.go(...)` - never a modal opened
// outside the router, because the browser's Back button has to close it
// (`ARCHITECTURE.md` section 3, "sheets are route state").
//
// HONEST-ABSENT, NOT PLACEHOLDER (plan D.2). Three facts the design's rows
// carry are NOT on the wire and are therefore left off rather than invented:
//   - the connection row's latency and MB/s, which nothing measures;
//   - the gallery row's byte total (`SessionRow` has no size field);
//   - the f-ratio when no aperture has been set, because 0 is `config.py`'s
//     own unset convention (D-SET-1: `aperture_mm` is a rig field now,
//     `opticsModel.ts`'s `fRatioFrom` never invents one).
// A clause that has no fact behind it is dropped; the row never prints a dash
// where a number was promised.
//
// FOUR READS ON MOUNT, each fired once and each fail-quiet:
//   /healthz             (open)              - nothing here, the hub owns it
//   GET /api/remote/status  (view.status)    - DIRECT vs RELAY, from the rig
//   GET /api/profiles       (view.status)    - via `useSetupFacts`
//   GET /api/locations      (config.site_optics) - CAP-GATED, see below
//   GET /api/sessions       (view.status)    - the gallery count
// `/api/locations` is the one a viewer may not have, so a viewer never ISSUES
// it: `App.tsx:507` already models this ("a non-holder never ISSUES the request
// ... rather than issuing it and eating a 403"), and the DOM test asserts it.

import { useEffect, useState, type JSX } from "react";
import { BannerCard, Card, ListRow, Mono } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import {
  useStore, useConfig, useSite, useWsPhase, useBootConnectFailed,
} from "../../../../store";
import {
  accessPhrase, useCan, useCanConfigBackend, useCanViewSitePrecise, useIsViewer,
} from "../../../../lib/caps";
import { getRemoteStatus } from "../../../../api/backends";
import { listLocations } from "../../../../api/site";
import { listSessions } from "../../../../api/sessions";
import type { RemoteStatus, SessionRow } from "../../../../types";
import { fRatioFrom } from "../sheets/opticsModel";
import { ConnectionGlyph, GalleryGlyph } from "./glyphs";
import { Group } from "./Group";
import { MoreGroup } from "./MoreGroup";
import { PhoneGroup } from "./PhoneGroup";
import { SetupCard } from "./SetupCard";
import { useSetupFacts } from "./useSetup";

/** `.nx-body`'s 10 px gap applies to the hub root's own children; this screen is
 *  nested two deep, so it repeats it for its groups. */
const COL = { display: "flex", flexDirection: "column", gap: "10px" } as const;

const LINK_WORD: Record<string, string> = {
  up: "online",
  connecting: "connecting",
  reconnecting: "reconnecting",
  down: "unreachable",
};

function joinClauses(parts: (string | null | undefined)[]): string {
  return parts.filter((p): p is string => !!p).join(" · ");
}

export function GeneralScreen(): JSX.Element {
  const facts = useSetupFacts();
  const config = useConfig();
  const site = useSite();
  const wsPhase = useWsPhase();
  const bootFailed = useBootConnectFailed();
  const isViewer = useIsViewer();
  const canConfig = useCanConfigBackend();
  const canSitePrecise = useCanViewSitePrecise();
  const canSiteOptics = useCan("config.site_optics");
  const flowCount = useStore((s) => (s.flows.libraryLoaded ? s.flows.cards.length : null));
  const mountParked = useStore((s) => s.status?.mount?.parked ?? null);
  const mountTracking = useStore((s) => s.status?.mount?.tracking ?? null);
  const cooler = useStore((s) => s.status?.camera?.cooler ?? null);
  const camTemp = useStore((s) => s.status?.camera?.temperature ?? null);
  const filterNow = useStore((s) => s.status?.filterwheel?.current ?? null);
  const safeNow = useStore((s) => s.safety?.reading?.is_safe ?? null);

  // --- the rig's own answer to "how did this request arrive" ---------------
  const [remote, setRemote] = useState<RemoteStatus | null>(null);
  useEffect(() => {
    let alive = true;
    void getRemoteStatus()
      .then((r) => { if (alive) setRemote(r); })
      .catch(() => { /* a server older than S3: the row falls back to the path */ });
    return () => { alive = false; };
  }, []);

  // --- the saved-site count, only for a role that may read the library -----
  const [siteCount, setSiteCount] = useState<number | null>(null);
  useEffect(() => {
    if (!canSiteOptics) return;
    let alive = true;
    void listLocations()
      .then((rows) => { if (alive) setSiteCount(rows.length); })
      .catch(() => { /* leave the count off rather than printing a wrong one */ });
    return () => { alive = false; };
  }, [canSiteOptics]);

  // --- the night library ---------------------------------------------------
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  useEffect(() => {
    let alive = true;
    void listSessions()
      .then((rows) => { if (alive) setSessions(rows); })
      .catch(() => { /* the row prints what it can without a count */ });
    return () => { alive = false; };
  }, []);

  // ------------------------------------------------------------- RIG group
  const host = typeof window === "undefined" ? "" : window.location.host;
  const pathHere = typeof window === "undefined" ? "/" : window.location.pathname;
  const here: "direct" | "relay" =
    remote ? remote.via : (pathHere.startsWith("/h/") ? "relay" : "direct");
  const connSub = joinClauses([
    here === "relay" ? "through the relay" : "direct to the rig",
    host || null,
    LINK_WORD[wsPhase] ?? null,
  ]);

  const rigSummary = joinClauses([
    cooler?.on
      ? `cooled ${camTemp == null ? "" : `${camTemp.toFixed(1)}°C`}`.trim()
      : cooler
        ? "cooler warm"
        : null,
    mountParked === true ? "parked" : mountTracking === true ? "tracking" : null,
    filterNow || null,
    safeNow == null ? null : safeNow ? "safe" : "UNSAFE",
  ]);
  const profileWord = facts.activeProfile?.name ?? "no profile";
  const rigSub = facts.snapshot.equipConnected
    ? joinClauses([profileWord, `${facts.deviceCount} devices`, rigSummary || null])
    : joinClauses(["not connected", facts.activeProfile ? `${profileWord} profile ready` : null]);

  const oc = config?.optics_computed ?? null;
  const fr = oc ? fRatioFrom(oc, oc.focal_length_mm, oc.aperture_mm) : "";
  const opticsSub = oc?.have_optics
    ? joinClauses([
        `${Math.round(oc.focal_length_mm)} mm`,
        fr.startsWith("f/") ? fr : null,
        `${oc.pixel_size_um} µm`,
        oc.fov_w_deg != null && oc.fov_h_deg != null
          ? `${oc.fov_w_deg.toFixed(2)}° × ${oc.fov_h_deg.toFixed(2)}°`
          : null,
      ])
    : "focal length and pixel size are not set, so nothing knows the frame size";

  // ------------------------------------------------------------- SKY group
  const siteIsDefault = facts.snapshot.siteIsDefault;
  const lat = site?.latitude ?? config?.site?.latitude;
  const lon = site?.longitude ?? config?.site?.longitude;
  const sitesSub = siteIsDefault
    ? "no observing site saved yet · the default (0, 0) is not a real sky"
    : canSitePrecise && typeof lat === "number" && typeof lon === "number"
      ? `${facts.siteName} · ${lat.toFixed(4)}° · ${lon.toFixed(4)}°`
      : "site set · precise location hidden for this role";

  const horizonSub = joinClauses([
    siteIsDefault ? null : facts.siteName,
    facts.horizonSummary ?? "no horizon drawn yet",
  ]);

  // ---------------------------------------------------------- LIBRARY group
  const newest = sessions && sessions.length > 0
    ? [...sessions].sort((a, b) => b.updated_ts - a.updated_ts)[0]
    : null;
  const gallerySub = sessions == null
    ? "every night the rig has kept"
    : joinClauses([
        `${sessions.length} ${sessions.length === 1 ? "session" : "sessions"} on the rig`,
        newest ? `last ${newest.name}` : null,
      ]);

  return (
    <div data-testid="screen-settings-general" style={COL}>
      {bootFailed && (
        <BannerCard
          tone="warn"
          text="The boot profile did not fully connect. Check the per-role status and reconnect."
          cta={{ label: "devices", onPress: () => nav.go("/rig/devices") }}
          data-testid="banner-boot-failed"
        />
      )}

      {!canConfig && (
        <Card data-testid="banner-readonly">
          <Mono size={11}>
            {isViewer
              ? `Read-only - you can view rig status and sign in, but connecting rigs and editing profiles needs ${accessPhrase("config.backend")}.`
              : "Your role cannot change backends or profiles. Connection status is shown for reference."}
          </Mono>
        </Card>
      )}

      <SetupCard view={facts.view} />

      <Group label="RIG" testId="group-rig">
        <ListRow
          icon={<ConnectionGlyph />}
          title="CONNECTION"
          sub={connSub}
          right={<Mono tone={wsPhase === "up" ? "good" : "warn"}>{here.toUpperCase()}</Mono>}
          chevron
          onPress={() => nav.sheet("connection")}
          data-testid="row-connection"
        />
        <ListRow
          icon={<NxIcon name="rig" />}
          title="RIG PROFILE"
          sub={rigSub}
          right={<Mono>DEVICES</Mono>}
          chevron
          onPress={() => nav.go("/rig/devices")}
          data-testid="row-rig-profile"
        />
        <ListRow
          icon={<NxIcon name="optics" />}
          title="OPTICS · FIELD OF VIEW"
          sub={opticsSub}
          right={<Mono>EDIT</Mono>}
          chevron
          onPress={() => nav.sheet("optics")}
          data-testid="row-optics"
        />
      </Group>

      <Group label="SKY" testId="group-sky">
        <ListRow
          icon={<NxIcon name="gps" />}
          title="SITES"
          sub={sitesSub}
          right={<Mono>{siteCount == null ? "SITES" : `${siteCount} SITES`}</Mono>}
          chevron
          onPress={() => nav.sheet("sites")}
          data-testid="row-sites"
        />
        <ListRow
          icon={<NxIcon name="horizon" />}
          title="HORIZON AT THIS SITE"
          sub={horizonSub}
          right={<Mono>EDIT</Mono>}
          chevron
          onPress={() => nav.sheet("horizon", { site: "current" })}
          data-testid="row-horizon"
        />
        <ListRow
          icon={<NxIcon name="clock" />}
          title="QUICK SESSION DEFAULTS"
          sub="the hours, filters and extras a quick session starts from"
          right={<Mono tone="dim">from your last session</Mono>}
          chevron
          onPress={() => nav.sheet("quickDefaults")}
          data-testid="row-quick-defaults"
        />
      </Group>

      <PhoneGroup />

      <Group label="LIBRARY" testId="group-library">
        <ListRow
          icon={<NxIcon name="flows" />}
          title="MY FLOWS"
          sub={
            flowCount == null
              ? "saved flows · quick sessions land here"
              : `${flowCount} saved · quick sessions land here`
          }
          right={<Mono>OPEN</Mono>}
          chevron
          onPress={() => nav.go("/session/flows")}
          data-testid="row-flows"
        />
        <ListRow
          icon={<GalleryGlyph />}
          title="GALLERY"
          sub={gallerySub}
          right={<Mono>OPEN</Mono>}
          chevron
          onPress={() => nav.go("/session/gallery")}
          data-testid="row-gallery"
        />
      </Group>

      <MoreGroup />
    </div>
  );
}
