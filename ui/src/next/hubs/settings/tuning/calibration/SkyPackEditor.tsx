// SkyPackEditor.tsx - the online survey toggle and the offline HiPS pack (wave
// R7, T-R7-14; plan section 3.F17, cutover table section 7).
//
// It replaces `components/settings/SkyAtlasPanel.tsx` at its one mount inside
// the new UI, `settings/sheets/SkyPackSheet.tsx`. The legacy file is untouched;
// `#/classic` still renders it.
//
// THE ONE BEHAVIOUR WORTH GUARDING. The progress bar renders only when the
// server has actually reported a fetch in flight. Before the first
// `GET /api/survey/pack` answers, `status` is null and there is NO bar - not a
// bar at 0 %. A 0 % bar on a cold open reads as "a download is running and has
// achieved nothing", which is the opposite of the truth (nothing is running),
// and it is the shape of claim this codebase keeps having to take back. The
// legacy panel got this right with `{f && ...}`; the rebuild keeps the same
// guard and a test holds it there.
//
// WHAT CHANGED.
//
// 1. NO NATIVE `disabled`. The legacy toggle and both buttons carried
//    `disabled={busy || !!f || !canEdit}` - three different refusals rendered
//    as one grey control. Each is now `lockedReason` + `onExplain`, and the
//    "a download is already running" case names the progress bar as the reason
//    the button will not start a second one.
// 2. THE RETRY HINT LOST ITS EM-DASH (the copy rule); nothing else in it moved.
// 3. The read-only sentence comes from `LockNote` rather than a hand-rolled
//    line, so it is word-for-word what a locked control says when pressed.
//
// The 2 s poll runs ONLY while a fetch is in flight and only while this
// component is mounted, so an idle Settings sheet is not asking the rig for
// pack status forever.

import { useCallback, useEffect, useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { deletePack, getPackStatus, setSurveyConfig, startPackFetch } from "../../../../../api/backends";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import { packProgressPct, packStatusLabel } from "../../../../../components/settings/skyAtlasMeta";
import { useConfig, useStore } from "../../../../../store";
import type { PackStatus } from "../../../../../types";
import { NxIcon } from "../../../../icons";
import { useLock } from "../../../../lib/gateHook";
import { isLocalOnly, LOCAL_ONLY_REASON } from "../../../../lib/gate";
import { ActionButton, Bar, Card, Label, LockNote, Mono, Switch } from "../../../../ui";
import {
  ONLINE_BLURB, ONLINE_SWITCH_LABEL, ONLINE_TITLE, PACK_ATTRIBUTION, PACK_BUSY_REASON,
  PACK_DELETE_BODY, PACK_DELETE_CONFIRM, PACK_DELETE_FAILED, PACK_DELETE_LABEL,
  PACK_DELETE_TITLE, PACK_DELETED_TOAST, PACK_DOWNLOAD_LABEL, PACK_FETCH_FAILED,
  PACK_FETCHING_REASON, PACK_NO_SPACE, PACK_PROGRESS_ARIA, PACK_RETRY_HINT,
  PACK_STATUS_FALLBACK, PACK_TITLE, PACK_UPDATE_LABEL, SURVEY_SAVE_FAILED, surveyForbidden,
  surveyLockSentence,
} from "./calibrationModel";

const POLL_MS = 2000;

export function SkyPackEditor(): JSX.Element {
  const config = useConfig();
  // `needsLan`: all three writes are fenced - `POST /api/config/survey` (the
  // switch), `POST /api/survey/pack/fetch` (DOWNLOAD) and `DELETE
  // /api/survey/pack` (DELETE). The status GET is not, so the card still reads.
  const lock = useLock({ cap: "config.site_optics", needsLan: true });
  const onlineFetch = config?.survey?.online_fetch ?? false;

  const [status, setStatus] = useState<PackStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Remembered across the end of a fetch: once `fetching` clears, the only
  // evidence that tiles failed is gone, and "run it again" is advice the user
  // cannot act on if nobody says why.
  const [lastFailed, setLastFailed] = useState(0);

  const reload = useCallback(async () => {
    try {
      const s = await getPackStatus();
      setStatus(s);
      if (s.fetching) setLastFailed(s.fetching.failed);
    } catch (e) {
      setErr(e instanceof Error ? e.message : PACK_STATUS_FALLBACK);
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  const fetching = !!status?.fetching;
  useEffect(() => {
    if (!fetching) return;
    const id = window.setInterval(() => { void reload(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [fetching, reload]);

  const toggleOnline = async (v: boolean): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await setSurveyConfig({ online_fetch: v });
      await useStore.getState().loadConfig();
    } catch (e) {
      // `isLocalOnly` FIRST in all three catches below: a `local_only` 403 is
      // the relay fence refusing every role, not a capability this caller is
      // missing, and `surveyForbidden()` would name the wrong blocker.
      setErr(isLocalOnly(e) ? LOCAL_ONLY_REASON
        : e instanceof ApiError
          ? (e.status === 403 ? surveyForbidden() : e.message || SURVEY_SAVE_FAILED)
          : SURVEY_SAVE_FAILED);
    } finally {
      setBusy(false);
    }
  };

  const download = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await startPackFetch();
      await reload();
    } catch (e) {
      setErr(isLocalOnly(e) ? LOCAL_ONLY_REASON
        : e instanceof ApiError
          ? (e.status === 403 ? surveyForbidden()
            : e.status === 507 ? PACK_NO_SPACE
              : e.message || PACK_FETCH_FAILED)
          : PACK_FETCH_FAILED);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (): Promise<void> => {
    const ok = await confirmDialog({
      title: PACK_DELETE_TITLE,
      body: PACK_DELETE_BODY,
      tone: "danger",
      mode: "confirm",
      confirmLabel: PACK_DELETE_CONFIRM,
    });
    if (!ok) return;
    setBusy(true);
    setErr(null);
    try {
      await deletePack();
      await reload();
      useStore.getState().enqueueToast({ level: "success", title: PACK_DELETED_TOAST });
    } catch (e) {
      setErr(isLocalOnly(e) ? LOCAL_ONLY_REASON
        : e instanceof ApiError
          ? (e.status === 403 ? surveyForbidden() : e.message || PACK_DELETE_FAILED)
          : PACK_DELETE_FAILED);
    } finally {
      setBusy(false);
    }
  };

  const f = status?.fetching ?? null;
  const busyReason = busy ? PACK_BUSY_REASON : null;
  const toggleLock = lock.lockedReason ?? busyReason;
  const packLock = toggleLock ?? (f ? PACK_FETCHING_REASON : null);
  const noteReason = lock.lockedReason ? surveyLockSentence(lock.lockedReason) : null;
  const showRetryHint = (f != null && f.failed > 0)
    || (f == null && status?.present === false && lastFailed > 0);

  return (
    <>
      <Card data-testid="pack-online-card">
        <div className="nx-cal-col">
          <Label size={11}>{ONLINE_TITLE}</Label>
          <Switch
            data-testid="pack-online"
            checked={onlineFetch}
            label={ONLINE_SWITCH_LABEL}
            note={ONLINE_BLURB}
            lockedReason={toggleLock}
            onExplain={lock.onExplain}
            onChange={(v) => { void toggleOnline(v); }}
          />
        </div>
      </Card>

      <Card data-testid="pack-card">
        <div className="nx-cal-col">
          <Label size={11}>{PACK_TITLE}</Label>

          <Mono size={11} data-testid="pack-status">{packStatusLabel(status)}</Mono>

          {f != null && (
            <Bar
              data-testid="pack-progress"
              height={6}
              value={packProgressPct(f) / 100}
              label={PACK_PROGRESS_ARIA}
            />
          )}

          <div className="nx-pack-actions">
            <ActionButton
              kind="secondary"
              data-testid="pack-download"
              busy={busy && f == null}
              lockedReason={packLock}
              onExplain={lock.onExplain}
              onPress={() => { void download(); }}
            >
              {status?.present ? PACK_UPDATE_LABEL : PACK_DOWNLOAD_LABEL}
            </ActionButton>
            {status?.present === true && (
              <ActionButton
                kind="danger"
                data-testid="pack-delete"
                lockedReason={packLock}
                onExplain={lock.onExplain}
                onPress={() => { void remove(); }}
              >
                {PACK_DELETE_LABEL}
              </ActionButton>
            )}
          </div>

          {showRetryHint && (
            <p className="nx-cal-warn" data-testid="pack-retry-hint">{PACK_RETRY_HINT}</p>
          )}

          {err != null && (
            <p className="nx-cal-err" data-testid="pack-error">
              <NxIcon name="info" size={12} />
              {err}
            </p>
          )}

          <LockNote reason={noteReason} data-testid="pack-lock" />

          <p className="nx-cal-attrib">{PACK_ATTRIBUTION}</p>
        </div>
      </Card>
    </>
  );
}
