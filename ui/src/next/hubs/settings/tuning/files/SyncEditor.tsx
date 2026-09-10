// SyncEditor.tsx - the FILE SYNC sheet's body, rebuilt in the design's own
// vocabulary (wave R7, T-R7-13; replaces the mounted
// `components/settings/SyncPanel.tsx`, which is NOT edited and keeps serving
// `#/classic`).
//
// PUSH NOW IS THE POINT OF THE CARD, and it is why the status block is not
// collapsed away. Everything else here is a promise about 02:00 - that frames
// will leave the rig while the night is still going - and a promise nobody can
// test until 02:00 is how this feature would quietly not work. So the card
// carries the runner's real counters, the last pass's own summary, and one
// button that makes a pass happen while the operator is still at the keyboard.
//
// TWO CAPABILITIES, NOT ONE. `config.site_optics` writes the block;
// `view.media` runs a pass and reads the status, because a pass MOVES science
// frames. Both are admin-only in the shipped role map, but the server asks for
// them separately and so does this card: merging them would put the wrong
// sentence on whichever control loses the merge the day the map changes.
//
// FOUR NATIVE `disabled` ATTRIBUTES ARE GONE (`SyncPanel.tsx` :151, :165, :174,
// :196). The most expensive of them was the master toggle: it greys out when no
// destination is set, so a user who had never typed a path saw a dead switch
// and no sentence anywhere saying what to do. It is now `lockedReason`, and the
// reason names the fix.

import { useEffect, useRef, useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { getSyncPushStatus, pushSyncNow, setSyncPushConfig } from "../../../../../api/backends";
import { accessPhrase, useCan } from "../../../../../lib/caps";
import { formatAge } from "../../../../../lib/telemetry";
import type { SyncPushStatus } from "../../../../../types";
import { useConfig, useStore } from "../../../../../store";
import { useLock } from "../../../../lib/gateHook";
import { ActionButton, Card, Field, Label, LockNote, Mono, Switch, TextInput } from "../../../../ui";
import {
  filesLockSentence, lastPassLine, pushBlockedReason, PUSH_SUBJECT, SYNC_BLURB,
  SYNC_DEST_HINT, SYNC_DEST_PLACEHOLDER, SYNC_SUBJECT, syncCadenceLine,
  syncEnableBlockedReason, syncSummary,
} from "./filesModel";
import "./files.css";

/** Only while a pass is in flight - a pass takes tens of seconds on a full
 *  night and the card is otherwise static. Same idiom the legacy panel used. */
const POLL_MS = 3000;

export function SyncEditor(): JSX.Element {
  const config = useConfig();
  const saved = config?.sync_push;
  const showToast = useStore((s) => s.showToast);

  const canPush = useCan("view.media");
  const editGate = useLock({ cap: "config.site_optics" });
  const pushGate = useLock({ cap: "view.media" });
  const editLock = filesLockSentence(
    editGate.lockedReason, accessPhrase("config.site_optics"), SYNC_SUBJECT,
  );
  const pushLock = filesLockSentence(
    pushGate.lockedReason, accessPhrase("view.media"), PUSH_SUBJECT,
  );
  const explain = editGate.onExplain;

  const [status, setStatus] = useState<SyncPushStatus | null>(null);
  const [path, setPath] = useState(saved?.path ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Prop-derived draft, synced during render rather than in an effect: the
  // effect paints one frame of the old path after a save lands, and on this
  // card that frame is the difference between "saved" and "did not save".
  const seenPath = useRef<string | undefined>(saved?.path);
  if (seenPath.current !== saved?.path) {
    seenPath.current = saved?.path;
    setPath(saved?.path ?? "");
  }

  const reload = async (): Promise<void> => {
    if (!canPush) return;
    try {
      setStatus(await getSyncPushStatus());
    } catch (e) {
      // A 403 here is the role map answering honestly, not a fault: the card
      // already says the status needs view.media. Anything else is news.
      if (!(e instanceof ApiError && e.status === 403)) {
        setErr(e instanceof Error ? e.message : "Could not read the sync status.");
      }
    }
  };

  // Re-read whenever the CONFIG moves, not only on mount. The config can change
  // without this card doing the changing (another tab, or the WS `config`
  // broadcast after any save), and a status frozen at mount would leave PUSH
  // NOW locked against a destination that is now live.
  useEffect(() => { void reload(); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [canPush, saved?.enabled, saved?.path]);

  const running = !!status?.running;
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => { void reload(); }, POLL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const save = async (next: { enabled?: boolean; path?: string }): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await setSyncPushConfig({
        enabled: next.enabled ?? saved?.enabled ?? false,
        kind: "local_dir",
        path: (next.path ?? path).trim(),
        label: saved?.label ?? "",
        limit_per_pass: saved?.limit_per_pass ?? 0,
      });
      await useStore.getState().loadConfig();
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? `Refused - ${accessPhrase("config.site_optics")} is needed here.`
          : e.status === 422 ? "Enter a destination folder before turning sync on."
            : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  const push = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const s = await pushSyncNow();
      setStatus(s);
      // The RESULT, not "started" - this is the whole point of the button.
      if (s.last?.error) showToast("error", s.last.error, { verbatim: true });
      else if (s.last) showToast("success", s.last.summary);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 403
        ? `Refused - ${accessPhrase("view.media")} is needed to push frames.`
        : e instanceof Error ? e.message : "The push failed.");
    } finally { setBusy(false); }
  };

  const enabled = saved?.enabled ?? false;
  const dirty = (saved?.path ?? "") !== path.trim();
  const ageWords = status?.last_ok_at ? formatAge(Date.now() - status.last_ok_at * 1000) : null;
  const sum = syncSummary(status, ageWords);
  const lastLine = lastPassLine(status);
  const cadence = syncCadenceLine(status);
  const enableLock = syncEnableBlockedReason(editLock, enabled, path);
  const pushBlocked = pushBlockedReason(pushLock, status, busy);

  return (
    <div className="nx-files-stack" data-testid="sync-editor">
      <Card>
        <div className="nx-files-col">
          <Switch
            checked={enabled}
            onChange={(v) => void save({ enabled: v })}
            label="Push frames to a folder"
            note={SYNC_BLURB}
            lockedReason={enableLock}
            onExplain={explain}
            data-testid="sync-enabled"
          />

          <div className="nx-files-pair">
            <Field label="DESTINATION FOLDER" hint={SYNC_DEST_HINT} className="nx-files-grow">
              <TextInput
                value={path}
                onChange={setPath}
                mono
                placeholder={SYNC_DEST_PLACEHOLDER}
                ariaLabel="Destination folder"
                lockedReason={editLock}
                data-testid="sync-dest"
              />
            </Field>
            {dirty && (
              <ActionButton
                kind="secondary"
                busy={busy}
                lockedReason={editLock}
                onExplain={explain}
                onPress={() => void save({ path })}
                data-testid="sync-save-dest"
              >
                SAVE DESTINATION
              </ActionButton>
            )}
          </div>
        </div>
      </Card>

      <Card data-testid="sync-status">
        <div className="nx-files-col">
          <Label size={11}>WHAT THE PUSH RUNNER HAS DONE</Label>
          <Mono
            size={11.5}
            tone={sum.tone === "warn" ? "warn" : sum.tone === "ok" ? "good" : "dim"}
            data-testid="sync-summary"
          >
            {sum.text}
          </Mono>
          {lastLine && <Mono size={10.5} tone="dim" data-testid="sync-last">{lastLine}</Mono>}

          <ActionButton
            kind="secondary"
            busy={busy || running}
            lockedReason={pushBlocked}
            onExplain={pushGate.onExplain}
            onPress={() => void push()}
            data-testid="sync-push-now"
          >
            {running ? "PUSHING..." : "PUSH NOW"}
          </ActionButton>
          {cadence && <p className="nx-files-note" data-testid="sync-cadence">{cadence}</p>}
        </div>
      </Card>

      {err && <p className="nx-files-err" data-testid="sync-error">{err}</p>}
      <LockNote reason={editLock} data-testid="sync-lock" />
    </div>
  );
}
