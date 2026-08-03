// providerWrite.ts — WHERE a provider-override save lands, and what to tell the
// user before they commit to it (#132).
//
// THE FAILURE THIS ENDS. #129 taught the console to DISPLAY the winning layer:
// the Tasks row now reads "Running AstroDeck native — pinned by profile Rig1.
// Without it this rig would use the global setting, Auto (best available)".
// The write path never learned the same lesson. `POST /api/config/providers`
// writes the GLOBAL block, and the ACTIVE PROFILE beats it inside
// `providers.override_with_layer` — so a user who correctly read "pinned by
// profile Rig1", opened the dropdown, picked a value and saved changed NOTHING
// that runs. Green toast, unchanged rig. The console told the truth and then
// quietly ignored the user, which is worse than the display bug it replaced,
// because now the user has evidence they acted.
//
// THE RULE, decided by the owner: write back to the layer you are reading from.
// Winning layer `profile` → the save overwrites that profile's providers entry
// (`POST /api/profiles/{id}/set-providers`). Anything else → global config,
// exactly as before. An overwrite IS the edit; there is no merge semantic here
// and deliberately no attempt to invent one.
//
// The decision is pure and lives here so both surfaces that own a provider
// dropdown — the Equipment Tasks rows and the Guide view's provider panel —
// take the same branch from the same function. A second copy of "is this
// pinned" is precisely the shape of duplication that produced the original bug.

import type { EffectiveEntry, ProvidersConfig } from "../types";
import { isProfileOverride, overrideProfileName, type ProviderCap } from "./effective";

/** The four capability keys as a complete `ProvidersConfig`, all on `auto`.
 *
 *  Lives here rather than in a panel because THREE surfaces spread it (the
 *  Equipment Tasks panel, the Guide provider panel, and EquipmentView's
 *  load-profile path) and a `ProvidersConfig` POST must always carry all four
 *  fields — the server model has no optional keys, so a partial body would reset
 *  the omitted capabilities to their defaults. */
export const DEFAULT_PROVIDERS: ProvidersConfig = {
  autofocus: "auto",
  polar_align: "auto",
  solve: "auto",
  guide: "auto",
};

/** Which layer a save for this capability must be written to.
 *
 *  `profileId` is non-null exactly when `layer === "profile"`: the provenance
 *  block always carries `profile_id` alongside a `profile` layer, and a
 *  hypothetical entry that claimed the layer without naming the record would be
 *  unaddressable — so it degrades to the global write rather than throwing. That
 *  degradation is the pre-#132 behaviour, i.e. the known-imperfect one, which is
 *  the right thing to fall back to when the server's answer is malformed. */
export interface ProviderWriteTarget {
  layer: "profile" | "config";
  profileId: string | null;
  profileName: string | null;
}

export function providerWriteTarget(
  entry: EffectiveEntry | null,
): ProviderWriteTarget {
  if (isProfileOverride(entry) && entry?.profile_id) {
    return {
      layer: "profile",
      profileId: entry.profile_id,
      // `overrideProfileName` falls back to the id, because "profile 3f2a…" is
      // still an answer and "a profile" is not.
      profileName: overrideProfileName(entry),
    };
  }
  return { layer: "config", profileId: null, profileName: null };
}

/**
 * The sentence shown BEFORE the user commits — "Saving changes the profile
 * Rig1, not the global setting."
 *
 * Returns null for the global case, and that omission is the point rather than
 * an oversight. "This dropdown changes the setting" is the mental model every
 * user already has; printing it under all four rows is copy that describes what
 * is on screen, and copy like that is what trains people to stop reading the
 * lines that DO carry something. The profile case is the one they cannot
 * otherwise learn, and getting it wrong is exactly how the original bug felt.
 */
export function providerWriteNote(
  target: ProviderWriteTarget,
): string | null {
  if (target.layer !== "profile") return null;
  const who = target.profileName ?? "the active profile";
  return (
    `Saving changes the profile “${who}”, not the global setting — that pin is ` +
    `what this rig runs, so the edit lands where it takes effect.`
  );
}

/**
 * The body for a GLOBAL providers write.
 *
 * `globals` MUST be the raw `config.providers` block, never the panel's own
 * draft. The draft is seeded from the EFFECTIVE (winning) values, and POSTing
 * that would copy every profile-won value down into global config as a
 * side-effect of editing one unrelated row: change Autofocus while a profile
 * pins Polar align to the simulator, and global's polar_align silently becomes
 * "sim" too. That write was invisible until the pin was later cleared, at which
 * point the rig fell back to a value nobody had chosen.
 */
export function globalProvidersBody(
  globals: Partial<ProvidersConfig> | null | undefined,
  cap: ProviderCap,
  value: string,
): ProvidersConfig {
  return { ...DEFAULT_PROVIDERS, ...(globals ?? {}), [cap]: value };
}

// ------------------------------------------------------- guide provider rows
//
// The guide capability has no driver-offer vocabulary (no driver advertises a
// "guide" task), so its options come from the SERVER's per-rig eligibility:
// `status.providers.guide.options`, computed by providers.guide_provider_options.

/** Friendly labels for the guide-provider override VALUES. */
export const GUIDE_PROVIDER_LABELS: Record<string, string> = {
  auto: "Auto (best available)",
  astrodeck: "AstroDeck native",
  backend: "PHD2 / NINA bridge",
  // Legacy: "sim" was offered pre-fix-round and may persist in an old profile
  // snapshot. The server never lists it (it behaved exactly like Auto), so it
  // only ever appears as the sticky stored-value row.
  sim: "Simulator (legacy — same as Auto)",
};

export const guideProviderLabel = (value: string): string =>
  GUIDE_PROVIDER_LABELS[value] ?? value;

/** One rendered choice. `eligible === false` rows are NOT dropped: they render
 *  through the house honest-disabled pattern carrying `reason`, because
 *  "AstroDeck native needs a guide camera assigned and connected" tells the user
 *  what to do and a missing row does not — it reads as "this product cannot
 *  guide", which is the conclusion a real user reached. */
export interface GuideOptionRow {
  value: string;
  label: string;
  eligible: boolean;
  reason: string | null;
  /** True for a stored value the connected rig no longer offers (a legacy "sim"
   *  pin, or a rig that has since been disconnected). Kept listed so a stored
   *  choice never silently vanishes — the sticky-option rule the Tasks rows
   *  already follow. */
  sticky: boolean;
}

/** Server shape for one guide option (hub.poll_status → guide_provider_options). */
export interface GuideOptionWire {
  value: string;
  eligible: boolean;
  reason: string | null;
}

/**
 * The rows to render for the guide provider.
 *
 * `options` is the rich per-rig answer; `eligible` is the older list-only field,
 * kept as the degradation path for a server that predates `options` (and for the
 * pre-first-poll case, where neither exists and only "Auto" is safe to claim).
 * A blocked option coming back with no reason still renders — with a generic
 * line, because a dim row with no sentence is the silent dead end this whole
 * change is about.
 */
export function guideProviderRows(
  options: GuideOptionWire[] | null | undefined,
  eligible: string[] | null | undefined,
  stored: string,
): GuideOptionRow[] {
  let rows: GuideOptionRow[];
  if (options && options.length > 0) {
    rows = options.map((o) => ({
      value: o.value,
      label: guideProviderLabel(o.value),
      eligible: !!o.eligible,
      reason: o.eligible
        ? null
        : (o.reason ??
          "not available on the connected rig — the server did not say why"),
      sticky: false,
    }));
  } else {
    const list = eligible && eligible.length > 0 ? eligible : ["auto"];
    rows = list.map((v) => ({
      value: v,
      label: guideProviderLabel(v),
      eligible: true,
      reason: null,
      sticky: false,
    }));
  }
  if (stored && !rows.some((r) => r.value === stored)) {
    // (see below for why a sticky row is `eligible`)
    // Selectable, because it is ALREADY selected — re-picking it is a no-op, and
    // rendering the current value as blocked would be a control arguing with
    // itself. The sentence carries the actual news.
    rows.push({
      value: stored,
      label: guideProviderLabel(stored),
      eligible: true,
      reason: "Stored for this rig, but the connected rig no longer offers it.",
      sticky: true,
    });
  }
  return rows;
}

/**
 * The sentence for a stored choice that CANNOT run on the connected rig.
 *
 * Without it the row contradicts itself, and the contradiction reads as a bug in
 * the console rather than a fact about the rig: the override disclosure says
 * "Running AstroDeck native — pinned by profile Rig1" (which is true of the
 * CONFIGURED value), the resolver line two lines up says "no guide camera
 * connected — using the PHD2 bridge", and the badge says PHD2. All three are
 * correct and they cannot all be believed at once. This names the missing step —
 * the pin survives, the resolver discarded it for now — so the user reads three
 * consistent facts instead of one apparent defect.
 *
 * Returns null when the stored choice can run, which is the ordinary case.
 */
export function blockedSelectionNote(
  rows: GuideOptionRow[],
  selected: string,
  resolvedKind?: string | null,
): string | null {
  const row = rows.find((r) => r.value === selected);
  if (!row) return null;
  // Derived from what the resolver actually RETURNED, not from whether the offer
  // lists the value. Those are different questions, and the offer is deliberately
  // the narrower of the two: `_resolve_guide` honours an explicit `backend`
  // override unconditionally, and honours `astrodeck` on a NINA rig, while the
  // offer excludes both. Offer ⊂ resolver is the safe direction for deciding
  // what to let someone WRITE — but this sentence claims what will RUN, and
  // reading it off the offer made it lie in exactly that gap: with `astrodeck`
  // pinned on a NINA rig the offer blocks it, so the note said "it cannot run —
  // guiding falls back to the provider on the badge", while the resolver was
  // returning AstroDeck native and the badge said so too. Circular and false.
  //
  // So: say this only when the running provider genuinely differs from the
  // stored one. When the caller has no resolved kind to compare against, fall
  // back to the offer — degraded, but it cannot manufacture a contradiction the
  // way the old unconditional form did.
  if (resolvedKind != null && resolvedKind !== "") {
    if (resolvedKind === selected) return null;
  } else if (row.eligible) {
    return null;
  }
  return (
    `“${row.label}” stays saved, but it cannot run on the rig as connected — ` +
    `guiding falls back to the provider on the badge until the blocker above ` +
    `is cleared.`
  );
}
