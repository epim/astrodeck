// useSetup.ts - the live snapshot the setup card and the setup sheet share.
//
// The snapshot is assembled here, ONCE, from narrow store selectors plus the
// single fetch the wizard has always needed (`GET /api/profiles`, `view.status`
// - a viewer may read it). `computeSetup` stays pure and unit-tested; this file
// is the only place that knows where each signal lives.
//
// Why the profiles fetch is here and not in `computeSetup`: it is the ONE
// wizard signal that is not in the store (`components/FirstRunWizard.tsx:146-179`
// documents the measured defect that follows from getting it wrong - the step
// sat unticked for a whole session after a successful save). We fetch on mount
// and again when `equipConnected` flips, which is the only event that can make
// a profile appear without a reload; unlike the legacy wizard we do NOT poll,
// because this screen is not the surface a user saves a profile from.

import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import {
  useStore, useConfig, useSite, useEquipConnected, usePlan, usePreviews,
  usePrincipal, useWsPhase,
} from "../../../../store";
import { listProfiles } from "../../../../api/backends";
import { deriveBase } from "../../../../lib/base";
import { activeSiteName } from "../../../../lib/site";
import type { ProfileRow } from "../../../../types";
import { summary as horizonSummaryOf } from "../../../lib/horizonModel";
import { computeSetup, type SetupSnapshot, type SetupView } from "./setupSteps";

export interface SetupFacts {
  view: SetupView;
  profiles: ProfileRow[];
  /** The row `config.active_profile_id` names, or null when nothing does. */
  activeProfile: ProfileRow | null;
  /** Devices reporting `connected` in the live status. */
  deviceCount: number;
  /** The active obstruction line's summary, or null when none is drawn. */
  horizonSummary: string | null;
  siteName: string;
  snapshot: SetupSnapshot;
}

export function useSetupFacts(): SetupFacts {
  const config = useConfig();
  const site = useSite();
  const equipConnected = useEquipConnected();
  const targetCount = usePlan().targets.length;
  const frameCount = usePreviews().length;
  const principal = usePrincipal();
  const wsPhase = useWsPhase();
  const hasCooler = useStore((s) => !!s.status?.camera?.can_cool);
  const coolerActive = useStore((s) => !!s.status?.camera?.cooler?.on);
  const deviceCount = useStore(
    (s) => Object.values(s.status?.connected ?? {}).filter((d) => d.connected).length,
  );
  const horizonPoints = useStore(
    useShallow((s) => s.config?.safety?.horizon ?? null),
  );

  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  useEffect(() => {
    let alive = true;
    void listProfiles()
      .then((rows) => { if (alive) setProfiles(rows); })
      .catch(() => { /* older server, offline, or a role that cannot read it */ });
    return () => { alive = false; };
  }, [equipConnected]);

  const activeProfile =
    profiles.find((p) => p.id === config?.active_profile_id) ?? null;

  // `site` is the live mirror, `config.site` the persisted record; either
  // answers, and `store.loadConfig()` keeps them in lock-step. Defaulting to
  // TRUE means an un-hydrated store reads as "not set yet" rather than
  // silently ticking the step.
  const siteIsDefault = site?.is_default ?? config?.site?.is_default ?? true;
  const siteName = activeSiteName(site ?? config?.site);

  const horizonSummary =
    horizonPoints && horizonPoints.length > 0
      ? horizonSummaryOf(horizonPoints.map(([az, alt]) => ({ az, alt })))
      : null;

  const pathname = typeof window === "undefined" ? "/" : window.location.pathname;

  const snapshot: SetupSnapshot = {
    // wizard half
    siteIsDefault,
    equipConnected,
    profileCount: profiles.length,
    targetCount,
    hasCooler,
    coolerActive,
    frameCount,
    // new half
    linkUp: wsPhase === "up",
    identityResolved: principal != null,
    email: principal?.email ?? null,
    reach: deriveBase(pathname) === "" ? "direct" : "relay",
    deviceCount,
    profileName: activeProfile?.name ?? null,
    haveOptics: config?.optics_computed?.have_optics === true,
    fovW: config?.optics_computed?.fov_w_deg ?? null,
    fovH: config?.optics_computed?.fov_h_deg ?? null,
    siteName: siteIsDefault ? null : siteName,
    horizonSummary,
  };

  return {
    view: computeSetup(snapshot),
    profiles,
    activeProfile,
    deviceCount,
    horizonSummary,
    siteName,
    snapshot,
  };
}
