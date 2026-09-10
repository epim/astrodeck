// systemModel.ts - the pure half of the four SYSTEM editors (wave R7, T-R7-12:
// Update, Factory reset, Credits, Restricted assets).
//
// Nothing here reads the store, fetches, or renders. It holds the copy the
// four editors share, the parsers and formatters lifted from the legacy panels
// (`components/settings/{UpdatePanel,FactoryResetPanel,CreditsPanel,
// RestrictedAssetsPanel}.tsx`), and the two decisions that must not be
// re-derived per screen: the typed-word interlock and the rig-idle gate.
//
// The legacy files are NOT edited and NOT imported from anywhere under
// `next/`: `#/classic` keeps its own copies (wave plan section 2.1's finding -
// importing a helper out of a presentation module drags that module's whole
// render tree into the lazily-split next bundle).

import { accessPhrase } from "../../../../../lib/caps";

// ============================================================ the typed word

/** The typed-word interlock. MUST agree with the server's check in
 *  `api/app.py::factory_reset_apply` (trimmed + case-folded): a phone keyboard
 *  auto-capitalises the first letter and it is easy to trail a space, and a
 *  button that lights up on a word the server then rejects is worse than no
 *  button. The word makes the act deliberate; it is not a typing test.
 *
 *  Verbatim from `FactoryResetPanel.tsx:44-47`. A near miss ("RESE", "RESETT",
 *  "RE SET") is NOT a match - the guard is exact after trimming and folding. */
export const RESET_WORD = "RESET";

export function confirmWordOk(typed: string): boolean {
  return (typed || "").trim().toUpperCase() === RESET_WORD;
}

/** Wipe THIS browser's AstroDeck state.
 *
 *  A blanket clear, ON PURPOSE and UNCHANGED from `FactoryResetPanel.tsx:92-95`
 *  (wave plan section 0, ruling 8: "T-R7-12 states the key list and does not
 *  change behaviour"). The app owns this origin; the keys are spread across a
 *  dozen modules (`astrodeck-*`, `astrodeck.equipment.assignments.v1`, the
 *  wizard flag nested inside `astrodeck-coach-seen`, and every
 *  `astrodeck-next-*` preference the new UI writes), and an enumerated list is
 *  a list somebody will forget to extend - which is precisely how a stale
 *  client outlives a reset server.
 *
 *  So this clears EVERY key on the origin, `astrodeck-next-*` included, plus
 *  sessionStorage. Guarded because storage throws in private mode / with
 *  cookies blocked, and a storage failure must not stop the reload. */
export function clearClientState(): void {
  try { globalThis.localStorage?.clear(); } catch { /* storage unavailable */ }
  try { globalThis.sessionStorage?.clear(); } catch { /* storage unavailable */ }
}

// ================================================================ formatters

/** Byte count in the largest unit that keeps it readable. */
export function human(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const v = bytes / 1024 ** i;
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Make a rig-busy reason read as a clause after "while".
 *
 *  The server hands back two shapes - "rig is capturing" and "a sequence is
 *  running" - and only the first wants an article. The old copy hard-coded one
 *  for both and printed "Not while the a sequence is running." */
export function clause(reason: string): string {
  return reason.startsWith("rig ") ? `the ${reason}` : reason;
}

/** A unix timestamp as local time, or "never" when the rig has never checked.
 *  "never" is the honest answer; a dash would read as a formatting failure. */
export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "unreadable timestamp";
  }
}

// ============================================================== update phases

/** The five pipeline phases the server streams over the `update` WS event.
 *  Anything else (idle, or a phase a newer server invents) is not "active". */
export const PHASE_LABEL: Record<string, string> = {
  checking: "Checking...",
  downloading: "Downloading...",
  verifying: "Verifying signature...",
  staging: "Staging...",
  applying: "Installing - the scope will restart...",
};

export function isPipelineActive(phase: string | undefined | null): boolean {
  return !!phase && phase in PHASE_LABEL;
}

export function phaseLabel(phase: string | undefined | null): string {
  return (phase && PHASE_LABEL[phase]) || "";
}

// ============================================================= the idle gates

/** The rig-idle blocker, derived LIVE from the two inputs that ride the 2 s
 *  status frame, exactly as the server derives `hub.restart_blocker`:
 *
 *      busy_label ? f"rig is {busy_label}" : engine.running ? "a sequence is running"
 *
 *  Both the update apply and the factory reset are refused by the server while
 *  the rig is working, and both legacy panels carried the same defect before it
 *  was fixed: the answer arrived on a GET that ran ONCE, at mount, so a
 *  sequence that ended ten minutes ago still refused and one that started since
 *  walked into a 409 on press.
 *
 *  The sequence is tested BEFORE `busy` (the server tests them the other way)
 *  only so the sentence holds still: inside a run, busy_label flickers between
 *  "capturing" and null between subs, and the verdict is "blocked" either way -
 *  only the reason named would have jittered. A paused sequence still owns the
 *  engine task, so it blocks too. */
export function rigBlocker(
  seqState: string | undefined | null,
  busyWord: string | undefined | null,
): string | null {
  if (seqState === "running" || seqState === "paused") return "a sequence is running";
  if (busyWord) return `rig is ${busyWord}`;
  return null;
}

/** True when the server's own blocked reason NAMES a rig state - i.e. it is a
 *  photograph that this client can now see has moved on, and the panel should
 *  re-ask rather than sit on it. The button can go stale-BLOCKED but never
 *  stale-ARMED. */
export function staleRigBlock(serverReason: string): boolean {
  return serverReason.startsWith("rig is") || serverReason === "a sequence is running";
}

// ==================================================================== shapes

export interface CaptureInventory { frames: number; entries: number; bytes: number }

/** `GET /api/system/factory-reset` - the MEASURED scope. The route counts the
 *  real profiles/plans/drivers/accounts and walks the real capture root, so the
 *  numbers on screen are the numbers on disk. */
export interface ResetPreview {
  site_is_default: boolean;
  profiles: number;
  plans: number;
  drivers: number;
  alert_sinks: number;
  users: number;
  remote_paired: boolean;
  update_credential: boolean;
  captures: CaptureInventory;
  preserved_capture_entries: string[];
  can_reset: boolean;
  blocked_reason: string;
}

/** `GET /api/licensing/restricted`. `remedy` is the whole shape of the row:
 *  "fetch" means we ship nothing and this machine pulls it from the publisher;
 *  "acknowledge" means there is no asset at all - the CLIENT is what the terms
 *  do not cover, and not-shipping-a-file cannot fix that. */
export interface RestrictedAsset {
  id: string;
  title: string;
  quote: string;
  reading: string;
  remedy: "fetch" | "acknowledge";
  source: string;
  without: string;
  satisfied: boolean;
  consent: { at: number; by: string; note: string } | null;
}

export function remedyLabel(remedy: RestrictedAsset["remedy"]): string {
  return remedy === "fetch" ? "fetched, never shipped" : "needs your word";
}

// ======================================================================= copy
//
// One place for the sentences, so the reason a control gives when PRESSED is
// word-for-word the reason its panel prints when idle.

export const UPDATE_INTRO =
  "AstroDeck checks GitHub for signed releases. Updates are verified (Ed25519 "
  + "and SHA256) before they run, applied only when you confirm, and never while "
  + "the rig is imaging. A failed update rolls back automatically.";

export const UPDATE_LOCK_NOTE =
  `checking for and installing updates needs ${accessPhrase("system.update")}`;

export const UNSUPERVISED_NOTE =
  "Not running under the supervisor - you can check for updates, but installing "
  + "requires the supervised launcher.";

export const NO_PUBKEY_NOTE =
  "No signing key pinned - updates cannot be installed until you set the release "
  + "public key. It is what proves a release is genuinely yours.";

export const CHANNEL_HINT =
  "Stable installs final releases only; Pre-release also offers release candidates.";

export const AUTO_CHECK_NOTE =
  "Polls GitHub on a schedule and surfaces an available update. Installing always "
  + "still needs your confirmation.";

export const PUBKEY_HINT =
  "Base64 Ed25519 public key. Releases must verify against this key or they are "
  + "rejected. Generate with scripts/gen_signing_key.py and keep the private key "
  + "in CI only.";

export const RESET_LEAD =
  "Puts this controller back to a fresh install so the next person sets it up "
  + "from scratch - the first-run wizard reopens at step one. There is no undo "
  + "and no backup.";

/** The parity sentence, verbatim from the wave plan's 3.F8 row. */
export const RESET_CAP_NOTE = `A factory reset needs ${accessPhrase("admin.users")}.`;

export const RESET_ARM_NOTE = `Type ${RESET_WORD} in the box above to arm this.`;

/** What the scope preview says when there IS no snapshot.
 *
 *  The preview route is `admin.users`-gated like the reset itself, so a
 *  non-admin has NO snapshot. Rendering `snap?.profiles ?? 0` there would print
 *  "0 saved profiles" and "0 frames in 0 folders" to somebody who simply is not
 *  allowed to know - a fabricated reassurance about exactly the quantity this
 *  screen exists to be honest about. */
export function noNumbersLine(counting: boolean): string {
  return counting ? "counting..." : `hidden (needs ${accessPhrase("admin.users")})`;
}

export const CREDITS_SEARCH_PLACEHOLDER = "package, licence, or what it requires";

/** Why this one screen has no capability gate at all. */
export const CREDITS_OPEN_NOTE =
  "Every role can read this page: a licence notice only an admin can read is not "
  + "published.";

export const CREDITS_OFFLINE_NOTE =
  "It is built into this app and needs no internet, so a failure here means the "
  + "build is incomplete rather than that the rig is offline.";

// The heading above already says these are not ours to hand on, so the lead
// says what happens INSTEAD, which is the part a reader cannot guess.
export const RESTRICTED_LEAD =
  "Where a licence allows it, we ship nothing and this machine fetches the asset "
  + "from the people who publish it. Where the terms do not cover a product like "
  + "this one at all, there is no file to leave out: it stays switched off until "
  + "you say they cover your use of it.";

export const RESTRICTED_ASKED_NOTE =
  "We have asked for permission in every case. Nothing on this page is settled "
  + "by it being here.";

/** The `config.backend` sentence. The consent is INSTANCE-WIDE: one
 *  acknowledgment covers everyone who uses this rig, so it comes from whoever
 *  administers the deployment rather than from whoever is signed in. */
export const RESTRICTED_LOCK_NOTE =
  `acknowledging a licence needs ${accessPhrase("config.backend")} - it is a `
  + "statement about this whole rig, not about you";

export const RESTRICTED_EMPTY_TITLE = "NOTHING TO DISCLOSE";

export const RESTRICTED_EMPTY_HINT =
  "This server lists no restricted assets - either it is older than the licensing "
  + "route, or nothing it ships needs your word.";
