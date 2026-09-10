// FactoryResetEditor.tsx - Settings > FACTORY RESET, rebuilt in the design
// vocabulary (wave R7, T-R7-12; parity table 3.F8).
//
// "Return it to a naive state in between people trying it out." One tester
// finishes, this puts the box back to a genuine fresh install, the next tester
// sets it up from scratch.
//
// THREE INTERLOCKS, because this is irreversible, and all three are carried
// across from `components/settings/FactoryResetPanel.tsx` unchanged: the
// `admin.users` capability, a typed word (a tap-through confirm is not
// proportional to the damage), and the app's own hold-to-confirm dialog. The
// three destructive EXTRAS - captured frames, sign-in accounts, relay pairing -
// are separate switches that default OFF at every layer (here, in the request
// body, and in the server function).
//
// THE SCOPE STATEMENT IS MEASURED, NOT WRITTEN FROM MEMORY. GET
// /api/system/factory-reset counts the real profiles/plans/drivers/accounts and
// walks the real capture root, so the numbers on screen are the numbers on
// disk - and when the caller may not read that route, the copy drops to the
// bare noun and says why rather than printing a fabricated zero.
//
// THE BROWSER HALF IS NOT OPTIONAL. Dismissed-wizard state, device
// assignments, theme and brightness all live in localStorage; a reset that
// cleared only the server would hand the next tester a fresh server behind a
// stale client. `clearClientState()` (systemModel.ts) clears the whole origin,
// unchanged from the legacy panel - wave plan section 0, ruling 8.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import { api } from "../../../../../api";
import { useSequence, useStatus, useStore } from "../../../../../store";
import { useCanAdminUsers } from "../../../../../lib/caps";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import { ActionButton, Card, Field, Label, LockNote, Mono, Switch, TextInput } from "../../../../ui";
import { explainLock } from "../../../../shell/explain";
import {
  RESET_ARM_NOTE, RESET_CAP_NOTE, RESET_LEAD, RESET_WORD, clause, clearClientState,
  confirmWordOk, human, noNumbersLine, plural, rigBlocker, type ResetPreview,
} from "./systemModel";
import "./system.css";

export function FactoryResetEditor(): JSX.Element {
  const canAdmin = useCanAdminUsers();
  const showToast = useStore((s) => s.showToast);
  const [snap, setSnap] = useState<ResetPreview | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [alsoCaptures, setAlsoCaptures] = useState(false); // default OFF - data
  const [alsoAuth, setAlsoAuth] = useState(false);         // default OFF - lockout
  const [alsoRemote, setAlsoRemote] = useState(false);     // default OFF - orphaning
  const [busy, setBusy] = useState(false);
  const runningRef = useRef(false);

  const refresh = useCallback(async () => {
    // Never fire the preview GET for somebody the route will refuse: an
    // admin.users-gated read from a viewer is a 403 in the log and a red box on
    // screen, for a number they are not allowed to see anyway.
    if (!canAdmin) return;
    try {
      setSnap(await api.get<ResetPreview>("/api/system/factory-reset"));
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "could not read the reset scope");
    }
  }, [canAdmin]);

  // The server refuses a reset while the rig is working and the preview GET
  // carries that answer - but the GET runs once, at mount. Derive it live from
  // the 2 s status frame instead, and keep the snapshot as the fallback for the
  // moment before the first frame lands.
  const rig = useStatus();
  const seqState = useSequence().state;
  const rigBlock = rig
    ? rigBlocker(seqState, rig.busy)
    : snap && !snap.can_reset
      ? snap.blocked_reason || "the rig is busy"
      : null;

  // Re-count when the rig goes idle (and once at mount). Keyed on the BOOLEAN,
  // not the sentence: during a run the reason can change wording without the
  // verdict changing, and each refresh walks the whole capture tree. The run
  // that was blocking the reset is also what changed the frame counts, so this
  // is the moment they most need re-measuring.
  const rigIdle = rigBlock == null;
  useEffect(() => { void refresh(); }, [refresh, rigIdle]);

  // ------------------------------------------------------------ the gate chain
  // Capability first (it is the only one that will not change on its own), then
  // what the screen could not read, then the rig, then the typed word. Each is
  // a sentence a user can act on; none of them is a native `disabled`.
  const capReason = canAdmin ? null : RESET_CAP_NOTE;
  const blocked =
    capReason
    ?? (loadErr ? `Cannot read what a reset would clear - ${loadErr}.` : null)
    ?? (rigBlock ? `Cannot reset while ${clause(rigBlock)}.` : null)
    ?? (confirmWordOk(typed) ? null : RESET_ARM_NOTE);

  const counting = canAdmin && !snap && !loadErr;
  const noNumbers = noNumbersLine(counting);
  /** "3 saved profiles" with a snapshot; "Saved profiles" without one. */
  const count = (n: number | undefined, one: string, many = `${one}s`) =>
    snap ? plural(n ?? 0, one, many) : many;

  const cap = snap?.captures;
  const frameLine = cap
    ? `${plural(cap.frames, "frame")} in ${plural(cap.entries, "folder")} (${human(cap.bytes)})`
    : noNumbers;

  const run = async () => {
    // A REF as well as `busy`, because `busy` is only set AFTER the confirm
    // resolves: two taps inside one React batch share a render and would push
    // two hold-to-confirm dialogs, the second replacing the first and leaving
    // its promise unresolved for the length of the session.
    if (blocked || busy || runningRef.current) return;
    runningRef.current = true;
    // Confirmation proportional to the damage: the app's own dialog, danger
    // tone, HOLD to proceed (never a tap), and a body that repeats the EXACT
    // choices - so the last thing read before committing is what this
    // particular press will and will not destroy.
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
          {alsoRemote ? (
            <b>
              Remote-relay pairing WILL be cleared - this box loses its link to
              the current owner&apos;s relay (for resale or transfer).
            </b>
          ) : (
            <>Remote-relay pairing is <b>kept</b>.</>
          )}{" "}
          This cannot be undone.
        </>
      ),
    });
    if (!ok) { runningRef.current = false; return; } // dismissing NEVER resets
    setBusy(true);
    try {
      await api.post("/api/system/factory-reset", {
        confirm: typed,
        delete_captures: alsoCaptures,
        reset_auth: alsoAuth,
        reset_remote: alsoRemote,
      });
      // Server first, then THIS browser, then a full reload - landing the tester
      // on a genuine first run instead of a fresh server behind a stale client.
      clearClientState();
      globalThis.location?.reload();
    } catch (e) {
      runningRef.current = false;
      setBusy(false);
      showToast("error", e instanceof Error ? e.message : "factory reset failed", { verbatim: true });
      void refresh();
    }
  };

  return (
    <div className="nx-sys-stack">
      <Card padding={12} data-testid="reset-scope">
        <div className="nx-sys-stack">
          {/* The sheet header and the DANGER ZONE label above already say
              irreversible. What this eyebrow adds is where the numbers below
              come from, which is the difference between a scope statement and
              a guess. */}
          <div className="nx-sys-head">
            <Label size={11}>Scope</Label>
            <Mono size={10.5}>counted on this box, not from memory</Mono>
          </div>
          <p className="nx-sys-note">{RESET_LEAD}</p>

          {/* MEASURED scope, stated BEFORE the user commits. Two lists of plain
              sentences rather than a single "resets everything", because
              "everything" is exactly the word that would make a tester assume
              their images were gone. */}
          <div className="nx-sys-cols">
            <div className="nx-sys-col" data-testid="reset-cleared">
              <Label size={11}>Cleared</Label>
              <ul className="nx-sys-list">
                <li>Observing site, optics and horizon, back to the (0, 0) default</li>
                <li>
                  {count(snap?.profiles, "saved profile")},{" "}
                  {count(snap?.plans, "saved plan")},{" "}
                  {count(snap?.drivers, "configured driver")}
                </li>
                <li>Saved locations, filter names, guider calibrations</li>
                <li>
                  Safety, weather, naming, calibration and{" "}
                  {count(snap?.alert_sinks, "alert channel")}
                </li>
                <li>This browser: dismissed wizard, device assignments, theme and brightness</li>
              </ul>
            </div>
            <div className="nx-sys-col" data-testid="reset-kept">
              <Label size={11}>Kept</Label>
              <ul className="nx-sys-list">
                <li className={alsoCaptures ? "nx-sys-struck" : undefined}>
                  {`Captured frames and session reports - ${frameLine}`}
                </li>
                <li className={alsoAuth ? "nx-sys-struck" : undefined}>
                  {count(snap?.users, "sign-in account")}
                </li>
                <li className={alsoRemote ? "nx-sys-struck" : undefined}>
                  Remote-relay pairing, this box&apos;s link to its owner&apos;s relay
                </li>
                <li>Self-update signing key, repo and channel</li>
                <li>
                  {`Offline sky pack, survey and radar caches and logs${
                    snap ? ` (${snap.preserved_capture_entries.join(", ")})` : ""
                  }`}
                </li>
              </ul>
            </div>
          </div>
        </div>
      </Card>

      {/* The opt-ins. Separate switches, all OFF, each carrying its own
          consequence - a tester's images and a tester's account are different
          kinds of loss and neither should ride along on a settings reset. */}
      <Card padding={12} data-testid="reset-extras">
        <div className="nx-sys-stack">
          <Label size={11}>Not cleared unless you say so</Label>
          <Switch
            checked={alsoCaptures}
            onChange={setAlsoCaptures}
            label="Also delete captured frames and session reports"
            note={`Off by default. These are the tester's images - ${frameLine}. Nothing recovers them.`}
            lockedReason={capReason}
            onExplain={explainLock}
            data-testid="reset-also-captures"
          />
          <Switch
            checked={alsoAuth}
            onChange={setAlsoAuth}
            label="Also remove sign-in accounts"
            note={"Off by default. Signs everyone out and leaves this server open again - do it "
              + "when the last tester made an account you cannot hand on, not out of habit."}
            lockedReason={capReason}
            onExplain={explainLock}
            data-testid="reset-also-auth"
          />
          {snap && (snap.remote_paired || snap.update_credential) ? (
            <Switch
              checked={alsoRemote}
              onChange={setAlsoRemote}
              label="Prepare for sale or transfer (clear remote-relay pairing)"
              note={"Off by default. Turn this on ONLY when the box is changing hands: it removes "
                + "the relay pairing and the stored update credential so the previous owner keeps "
                + "no way in over the internet. It does NOT remove sign-in accounts - turn on "
                + "\"Also remove sign-in accounts\" too, or the seller's login stays."}
              lockedReason={capReason}
              onExplain={explainLock}
              data-testid="reset-also-remote"
            />
          ) : null}
        </div>
      </Card>

      {/* -------------------------------------------------- typed-word arming */}
      <Card padding={12} data-testid="reset-arm">
        <div className="nx-sys-stack">
          <Field label={`Type ${RESET_WORD} to enable`} htmlFor="factory-reset-confirm">
            <TextInput
              id="factory-reset-confirm"
              value={typed}
              onChange={setTyped}
              placeholder={RESET_WORD}
              mono
              ariaLabel={`Type ${RESET_WORD} to enable the factory reset`}
              lockedReason={capReason}
              data-testid="reset-confirm"
            />
          </Field>

          <div className="nx-sys-actions">
            <ActionButton
              kind="danger"
              onPress={() => void run()}
              busy={busy}
              lockedReason={busy ? "the reset is already running" : blocked}
              onExplain={explainLock}
              data-testid="reset-run"
            >
              FACTORY RESET
            </ActionButton>
          </div>

          {/* The reason is VISIBLE text, so it reaches a sighted user who never
              presses the button, and it is the same string the press states. */}
          {blocked && !capReason && (
            <p className="nx-sys-warn" data-testid="reset-reason">{blocked}</p>
          )}
          <LockNote reason={capReason} data-testid="reset-lock-note" />
          {!canAdmin && (
            <p className="nx-sys-note" data-testid="reset-hidden-note">
              {`The counts above are ${noNumbers} - they come from the same route the reset `
                + "itself uses, so this screen shows you nouns instead of inventing numbers."}
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}

export default FactoryResetEditor;
