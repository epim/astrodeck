// CalibrationLibraryEditor.tsx - the rebuilt master library (wave R7, T-R7-14;
// plan section 3.F15, cutover table section 7).
//
// It replaces `components/settings/CalibrationLibraryPanel.tsx` at its one
// mount inside the new UI, `settings/sheets/CalibrationSheet.tsx`. The legacy
// file is untouched; `#/classic` still renders it.
//
// THREE THINGS CHANGED, AND WHY.
//
// 1. THE DELETE BUTTON NO LONGER DISAPPEARS. The legacy row rendered its trash
//    button only `{canWrite && ...}`, so a viewer saw a library with no way to
//    manage it and no statement that one exists. Every row now carries the
//    control, honest-locked with the reason (ARCHITECTURE section 6). Hiding a
//    control is the one thing worse than greying it out: there is nothing left
//    to press to find out why.
//
// 2. THE BUILD RESULT STAYS ON SCREEN. `POST /api/calibration/build` answers
//    with the three numbers that say what it did - masters built, frames
//    indexed, matching groups - and the legacy panel put them in a toast that
//    left after 2.8 s. A rebuild that finds nothing to do looks identical to
//    one that rebuilt everything unless those numbers are readable, so they are
//    rendered here and stay until the next build.
//
// 3. A LOAD FAILURE IS NOT AN EMPTY LIBRARY. That distinction is the legacy
//    panel's own `loadErr` idiom and it is kept verbatim in shape: an error
//    renders as an error with a RETRY, never as "no masters yet" - which is an
//    instruction to go and shoot calibration frames you may already have.
//
// The masters list is the SHARED store slice (`useMasters`), so a build or a
// delete here also refreshes the live pre-flight coverage row.

import { useCallback, useEffect, useState, type JSX } from "react";
import { api, ApiError } from "../../../../../api";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import {
  groupMasters, masterRowSummary,
  type CalibrationBuildReport, type MasterRow,
} from "../../../../../lib/calibrationLibrary";
import { useMasters, useStore } from "../../../../../store";
import { NxIcon } from "../../../../icons";
import { useLock } from "../../../../lib/gateHook";
import {
  ActionButton, Card, EmptyCard, honestPress, Label, LockNote, lockedAttrs, lockedClass, Mono,
} from "../../../../ui";
import {
  BUILD_FAILED, BUILD_LABEL, DELETE_CONFIRM_BODY_TAIL, DELETE_FAILED, deleteConfirmTitle,
  buildReportLine, groupHeading, LIB_EMPTY_HINT, LIB_EMPTY_TITLE, LIB_INTRO,
  LIB_LOAD_FALLBACK, LIB_LOAD_RETRY, LIB_TITLE, libraryLoadError, libraryLockSentence,
} from "./calibrationModel";

export function CalibrationLibraryEditor(): JSX.Element {
  const masters = useMasters();
  const lock = useLock({ cap: "control.capture" });

  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [report, setReport] = useState<CalibrationBuildReport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Fetch into the SHARED slice rather than local state: the live pre-flight
  // coverage row reads the same `masters`, so a rebuild here has to move it.
  const refresh = useCallback(async () => {
    try {
      const rows = await api.get<MasterRow[]>("/api/calibration/masters");
      useStore.setState({ masters: rows });
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : LIB_LOAD_FALLBACK);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const build = async (): Promise<void> => {
    if (building) return;
    setBuilding(true);
    setErr(null);
    try {
      const rep = await api.post<CalibrationBuildReport>("/api/calibration/build");
      setReport(rep);
      await refresh();
    } catch (e) {
      setReport(null);
      setErr(e instanceof ApiError ? (e.message || BUILD_FAILED) : BUILD_FAILED);
    } finally {
      setBuilding(false);
    }
  };

  const del = async (m: MasterRow): Promise<void> => {
    const summary = masterRowSummary(m);
    const ok = await confirmDialog({
      title: deleteConfirmTitle(),
      body: `${summary}. ${DELETE_CONFIRM_BODY_TAIL}`,
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    setErr(null);
    try {
      await api.del(`/api/calibration/masters/${m.id}`);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.message || DELETE_FAILED) : DELETE_FAILED);
    }
  };

  // `building` is a REASON, not a swallowed press: a second tap while the
  // rebuild runs says why instead of doing nothing.
  const busyReason = building ? "the rebuild is still running" : null;
  const writeLock = lock.lockedReason ?? busyReason;
  const noteReason = lock.lockedReason ? libraryLockSentence(lock.lockedReason) : null;
  const groups = groupMasters(masters);

  return (
    <Card data-testid="cal-library">
      <div className="nx-cal-col">
        <div className="nx-cal-head">
          <Label size={11}>{LIB_TITLE}</Label>
          <ActionButton
            kind="secondary"
            data-testid="cal-build"
            busy={building}
            lockedReason={writeLock}
            onExplain={lock.onExplain}
            onPress={() => { void build(); }}
          >
            {BUILD_LABEL}
          </ActionButton>
        </div>

        <p className="nx-cal-intro">{LIB_INTRO}</p>

        {report != null && (
          <p className="nx-cal-report" data-testid="cal-build-report">
            {buildReportLine(report)}
          </p>
        )}

        {err != null && (
          <p className="nx-cal-err" data-testid="cal-error">
            <NxIcon name="info" size={12} />
            {err}
          </p>
        )}

        {loadErr != null && masters.length === 0 ? (
          <div className="nx-cal-loaderr" data-testid="cal-load-error">
            <Mono size={11} tone="bad">{libraryLoadError(loadErr)}</Mono>
            <ActionButton
              kind="ghost"
              data-testid="cal-retry"
              onPress={() => { void refresh(); }}
            >
              {LIB_LOAD_RETRY}
            </ActionButton>
          </div>
        ) : masters.length === 0 ? (
          <EmptyCard title={LIB_EMPTY_TITLE} hint={LIB_EMPTY_HINT} data-testid="cal-empty" />
        ) : (
          <div className="nx-cal-groups">
            {groups.map((g) => (
              <div className="nx-cal-group" key={g.type}>
                <Label size={10}>{groupHeading(g.type, g.rows.length)}</Label>
                {g.rows.map((m) => (
                  <div className="nx-cal-row" key={m.id} data-testid="cal-master-row">
                    <span className="nx-cal-rowtext">{masterRowSummary(m)}</span>
                    <button
                      type="button"
                      className={lockedClass(writeLock, "nx-cal-del")}
                      data-testid="cal-master-delete"
                      aria-label={`Delete master ${masterRowSummary(m)}`}
                      onClick={honestPress(writeLock, lock.onExplain, () => { void del(m); })}
                      {...lockedAttrs(writeLock)}
                    >
                      <NxIcon name="x" size={14} />
                    </button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        <LockNote reason={noteReason} data-testid="cal-library-lock" />
      </div>
    </Card>
  );
}
