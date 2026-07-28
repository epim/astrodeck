// FactoryResetPanel.tsx — "return it to a naive state in between people trying
// it out". The QA-handoff button: one tester finishes, this puts the box back to
// a genuine fresh install, the next tester sets it up from scratch.
//
// PLACEMENT (SettingsView renders it last on the Connect tab, full width, under
// a rule): Connect is the tab that HOLDS the setup this destroys — Observing
// Site, drivers, weather, naming — so ending it with "…or throw all of this
// away" reads in the right order, and it is the tab Settings opens on, so the
// feature is findable by plain scrolling rather than by knowing where to look.
// It is also ~6 panels and a full phone-screen of scrolling below anything else,
// so nobody reaches it by accident. It is deliberately NOT on Account (a short
// tab where it would sit near the top, i.e. brushed against) and NOT duplicated
// anywhere: one home, at the end.
//
// THREE interlocks, because this is irreversible: the admin.users capability,
// a typed word (a tap-through confirm is not proportional to the damage), and
// the app's own hold-to-confirm dialog. The two destructive EXTRAS — captured
// frames, sign-in accounts — are separate switches that default OFF at every
// layer (here, in the request body, and in the server function).
//
// The scope statement is MEASURED, not written from memory: GET
// /api/system/factory-reset counts the real profiles/plans/drivers/accounts and
// walks the real capture root, so the numbers on screen are the numbers on disk.
//
// The BROWSER half is not optional. Dismissed-wizard state, device assignments,
// theme and brightness all live in localStorage; a reset that cleared only the
// server would hand the next tester a fresh server behind a stale client — the
// exact confusion this feature exists to prevent, and something that has bitten
// this project before. So the success path clears web storage and reloads.

import { useCallback, useEffect, useState, type JSX, type ReactNode } from "react";
import { api } from "../../api";
import { useStore } from "../../store";
import { Panel, HonestButton, LockedNote, LOCKED_CLASS } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import { accessPhrase, useCanAdminUsers } from "../../lib/caps";

/** The typed-word interlock. MUST agree with the server's check in
 *  `api/app.py::factory_reset_apply` (trimmed + case-folded): a phone keyboard
 *  auto-capitalises the first letter and it is easy to trail a space, and a
 *  button that lights up on a word the server then rejects is worse than no
 *  button. The word makes the act deliberate; it is not a typing test. */
export const RESET_WORD = "RESET";
export function confirmWordOk(typed: string): boolean {
  return (typed || "").trim().toUpperCase() === RESET_WORD;
}

interface CaptureInventory { frames: number; entries: number; bytes: number }
interface ResetPreview {
  site_is_default: boolean;
  profiles: number;
  plans: number;
  drivers: number;
  alert_sinks: number;
  users: number;
  captures: CaptureInventory;
  preserved_capture_entries: string[];
  can_reset: boolean;
  blocked_reason: string;
}

function human(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const v = bytes / 1024 ** i;
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}
function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Wipe THIS browser's AstroDeck state. A blanket clear on purpose: the app owns
 *  this origin, the keys are spread across a dozen modules (`astrodeck-*`,
 *  `astrodeck.equipment.assignments.v1`, the wizard flag nested inside
 *  `astrodeck-coach-seen`), and an enumerated list is a list somebody will
 *  forget to extend — which is precisely how a stale client outlives a reset
 *  server. Guarded because storage throws in private mode / with cookies
 *  blocked, and a storage failure must not stop the reload. */
export function clearClientState(): void {
  try { globalThis.localStorage?.clear(); } catch { /* storage unavailable */ }
  try { globalThis.sessionStorage?.clear(); } catch { /* storage unavailable */ }
}

/** An opt-in row where the WHOLE row is the switch.
 *
 *  The obvious spelling — `<label>` wrapping the shared `<Toggle/>` — renders
 *  identically and is a touch defect: `<label>` only forwards a click to a
 *  native form control, and Toggle is a `<button role="switch">`, so on a phone
 *  the two lines of consequence text look tappable and are dead, leaving a ~36px
 *  target for a switch that decides whether somebody's images survive. One
 *  button carrying `role="switch"` makes the whole row (>=44px, full width) the
 *  target, with no nested-interactive double-fire. */
function SwitchRow({ checked, onChange, title, children }: {
  checked: boolean; onChange: (v: boolean) => void;
  title: string; children: ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="w-full flex items-start gap-3 border border-line2 p-2.5
        min-h-[44px] text-left cursor-pointer hover:border-line"
    >
      {/* presentational: the accessible state lives on the row button above */}
      <span
        aria-hidden
        className={`relative w-9 h-5 border shrink-0 mt-0.5 transition-colors
          ${checked ? "bg-accent2/40 border-accent" : "bg-raise border-line2"}`}
      >
        <span className={`absolute top-0.5 w-3.5 h-3.5 transition-all
          ${checked ? "left-[18px] bg-accent" : "left-0.5 bg-dim"}`} />
      </span>
      <span className="text-[11px] leading-relaxed min-w-0">
        {/* ON/OFF in words: the switch's state must not be carried by position
            and fill alone (house rule — status is never colour-only, and at 2am
            through a red filter a 14px knob offset is not a readout). */}
        <span className="text-ink">{title}</span>{" "}
        <span
          className={`mono text-[10px] px-1 border align-[1px]
            ${checked ? "text-bad border-bad" : "text-dim border-line2"}`}
        >
          {checked ? "ON" : "OFF"}
        </span>
        <br />
        <span className="text-dim">{children}</span>
      </span>
    </button>
  );
}

export default function FactoryResetPanel(): JSX.Element {
  const canAdmin = useCanAdminUsers();
  const showToast = useStore((s) => s.showToast);
  const [snap, setSnap] = useState<ResetPreview | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [alsoCaptures, setAlsoCaptures] = useState(false); // default OFF — data
  const [alsoAuth, setAlsoAuth] = useState(false);         // default OFF — lockout
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!canAdmin) return;
    try {
      setSnap(await api.get<ResetPreview>("/api/system/factory-reset"));
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't read the reset scope");
    }
  }, [canAdmin]);
  useEffect(() => { void refresh(); }, [refresh]);

  // ------------------------------------------------------------ honest gating
  // A non-admin gets the panel DIMMED with a stated reason, never a dead grey
  // box and never the native `disabled` attribute (house rule §11.8).
  const capReason = canAdmin ? null : `A factory reset needs ${accessPhrase("admin.users")}.`;
  const blocked =
    capReason ??
    (loadErr ? `Can't read what a reset would clear — ${loadErr}` : null) ??
    (snap && !snap.can_reset ? `Not while the ${snap.blocked_reason}.` : null) ??
    (!confirmWordOk(typed)
      ? `Type ${RESET_WORD} in the box above to arm this.`
      : null);

  // --------------------------------------------------- never invent a number
  // The preview route is admin.users-gated like the reset itself, so a non-admin
  // has NO snapshot. Rendering `snap?.profiles ?? 0` there would print "0 saved
  // profiles" and "0 frames in 0 folders" to somebody who simply isn't allowed
  // to know — a fabricated reassurance about exactly the quantity this panel
  // exists to be honest about, and the same defect class as a permanent
  // "counting…" that never resolves. So when there is no snapshot the copy drops
  // to the bare noun and says why.
  const counting = canAdmin && !snap && !loadErr;
  const noNumbers = counting
    ? "counting…"
    : `hidden (needs ${accessPhrase("admin.users")})`;
  /** "3 saved profiles" with a snapshot; "Saved profiles" without one. */
  const count = (n: number | undefined, one: string, many = `${one}s`) =>
    snap ? plural(n ?? 0, one, many) : many;

  const cap = snap?.captures;
  const frameLine = cap
    ? `${plural(cap.frames, "frame")} in ${plural(cap.entries, "folder")} (${human(cap.bytes)})`
    : noNumbers;

  const run = async () => {
    if (blocked || busy) return;
    // Confirmation proportional to the damage: the app's own dialog, danger
    // tone, HOLD to proceed (never a tap), Cancel focused first, and a body that
    // repeats the EXACT choices — so the last thing read before committing is
    // what this particular press will and will not destroy.
    const ok = await confirmDialog({
      title: "Factory reset this controller?",
      tone: "danger",
      mode: "hold",
      confirmLabel: "Factory reset",
      cancelLabel: "Keep everything",
      body: (
        <>
          Site, profiles, drivers, plans and every other setting go back to
          defaults, and this browser is signed out of its saved state.{" "}
          {alsoCaptures ? (
            <b>Captured frames and session reports WILL be deleted ({frameLine}).</b>
          ) : (
            <>Captured frames and session reports are <b>kept</b> ({frameLine}).</>
          )}{" "}
          {alsoAuth ? (
            <b>Sign-in accounts WILL be removed and everyone signed out.</b>
          ) : (
            <>Sign-in accounts are <b>kept</b>.</>
          )}{" "}
          This cannot be undone.
        </>
      ),
    });
    if (!ok) return; // dismissing NEVER resets
    setBusy(true);
    try {
      await api.post("/api/system/factory-reset", {
        confirm: typed,
        delete_captures: alsoCaptures,
        reset_auth: alsoAuth,
      });
      // Server first, then THIS browser, then a full reload — landing the tester
      // on a genuine first run instead of a fresh server behind a stale client.
      clearClientState();
      globalThis.location?.reload();
    } catch (e) {
      setBusy(false);
      showToast("error", e instanceof Error ? e.message : "factory reset failed");
      void refresh();
    }
  };

  return (
    <Panel
      title="Factory reset"
      className="border-bad/50"
      right={
        <span className="label text-bad flex items-center gap-1.5">
          <Icon name="alert" size={12} aria-hidden />
          Irreversible
        </span>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-xs text-dim leading-relaxed">
          Puts this controller back to a fresh install so the next person sets it
          up from scratch — the first-run wizard reopens at step one. There is no
          undo and no backup.
        </p>

        {/* ---------------------------------------------- MEASURED scope, stated
            BEFORE the user commits. Two columns of plain sentences rather than a
            single "resets everything", because "everything" is exactly the word
            that would make a tester assume their images were gone. */}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="border border-line2 bg-raise/40 p-3">
            <p className="label text-bad mb-1.5">Cleared</p>
            <ul className="text-[11px] text-dim leading-relaxed list-disc pl-4 space-y-0.5">
              <li>Observing site, optics and horizon — back to the (0, 0) default</li>
              <li className="first-letter:uppercase">
                {count(snap?.profiles, "saved profile")},{" "}
                {count(snap?.plans, "saved plan")},{" "}
                {count(snap?.drivers, "configured driver")}
              </li>
              <li>Saved locations, filter names, guider calibrations</li>
              <li>
                Safety, weather, naming, calibration and{" "}
                {count(snap?.alert_sinks, "alert channel")}
              </li>
              <li>
                This browser: dismissed wizard, device assignments, theme and
                brightness
              </li>
            </ul>
          </div>
          <div className="border border-line2 bg-raise/40 p-3">
            <p className="label text-good mb-1.5">Kept</p>
            <ul className="text-[11px] text-dim leading-relaxed list-disc pl-4 space-y-0.5">
              <li className={alsoCaptures ? "line-through opacity-60" : ""}>
                <span className="text-ink">Captured frames and session reports</span>{" "}
                — {frameLine}
              </li>
              <li className={alsoAuth ? "line-through opacity-60" : ""}>
                <span className="text-ink first-letter:uppercase">
                  {count(snap?.users, "sign-in account")}
                </span>
              </li>
              <li>Remote-relay pairing and self-update settings</li>
              <li>
                Offline sky pack, survey/radar caches and logs
                {snap
                  ? ` (${snap.preserved_capture_entries.join(", ")})`
                  : ""}
              </li>
            </ul>
          </div>
        </div>

        {/* ------------------------------------------------- the two opt-ins.
            Separate switches, both OFF, each carrying its own consequence — a
            tester's images and a tester's account are different kinds of loss
            and neither should ride along on a settings reset. */}
        <div
          aria-disabled={capReason ? true : undefined}
          className={`flex flex-col gap-2 ${capReason ? LOCKED_CLASS : ""}`}
        >
          <SwitchRow
            checked={alsoCaptures}
            onChange={setAlsoCaptures}
            title="Also delete captured frames and session reports"
          >
            Off by default. These are the tester's images — {frameLine}. Nothing
            recovers them.
          </SwitchRow>
          <SwitchRow
            checked={alsoAuth}
            onChange={setAlsoAuth}
            title="Also remove sign-in accounts"
          >
            Off by default. Signs everyone out and leaves this server open again —
            do it when the last tester made an account you can't hand on, not out
            of habit.
          </SwitchRow>
        </div>

        {/* -------------------------------------------------- typed-word arming */}
        <div
          aria-disabled={capReason ? true : undefined}
          className={`flex flex-col gap-2 ${capReason ? LOCKED_CLASS : ""}`}
        >
          <label
            htmlFor="factory-reset-confirm"
            className="label text-dim"
          >
            Type {RESET_WORD} to enable
          </label>
          <input
            id="factory-reset-confirm"
            className="field tap"
            type="text"
            value={typed}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="characters"
            spellCheck={false}
            inputMode="text"
            placeholder={RESET_WORD}
            aria-describedby={blocked ? "factory-reset-reason" : undefined}
            onChange={(e) => setTyped(e.target.value)}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <HonestButton
            className="btn btn-danger tap"
            reason={busy ? "Reset in progress…" : blocked}
            onClick={() => void run()}
            onExplain={(r) => showToast("info", r)}
          >
            {busy ? "Resetting…" : "Factory reset"}
          </HonestButton>
          {/* The reason is VISIBLE text (so it reaches a sighted user who never
              presses the button) AND the input's description — one string in one
              place, rather than a second sr-only copy a screen reader would then
              read twice. */}
          {blocked && (
            <span id="factory-reset-reason">
              <LockedNote reason={blocked} className="!text-[11px]" />
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}
