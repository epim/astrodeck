// NowEmpty.tsx - the screen when nothing is running, which is most of the day.
//
// "NO SESSION RUNNING" ON ITS OWN IS A DEAD END, so this screen answers the
// three questions someone actually opens it with: what did last night do, what
// happens next by itself, and what can I start.
//
// RUN ARMED IS NOT "NO RUN ACTIVE". `_set_state` clears the session sub-block on
// every terminal transition, so a night that ended owing 58 frames leaves
// `sequence` as literally `{state: "idle"}` - and the Monitor used to render
// "No run active - plan a session" over exactly that, which is an instruction to
// strand the armed session (a fresh start disarms every other one). The armed
// state comes from `/api/sequence/resume-arm`, which the shell already polls.
//
// THE REPORT LIST HAS THREE STATES AND ALL THREE ARE DRAWN: loading, a load
// FAILURE with a Retry, and genuinely empty. A failed fetch that rendered "no
// reports yet" would tell an operator their archive was gone.
//
// A VIEWER SEES THIS SCREEN UNCHANGED, with every verb honest-disabled carrying
// the sentence `flowRunControls` already uses. Nothing is hidden.

import { useCallback, useEffect, useState, type JSX } from "react";

import { fmtIntegration } from "../../../../api/sessionStack";
import { listReports } from "../../../../api/reports";
import { endReasonMeta } from "../../../../lib/reportChart";
import { useCanControlMount, useRoleConnected } from "../../../../lib/caps";
import { runBlockedReason, useFlowRunControls } from "../../../../components/flows/flowRunControls";
import { useResumeArm, useSafety, useSeq, useStore } from "../../../../store";
import type { SessionReportSummary } from "../../../../types";
import { ActionButton, Card, EmptyCard, Label, ListRow, Mono } from "../../../ui";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import { nav } from "../../../router";
import { useCampaignFlowId } from "./useCampaign";
import { useFlowLibrary } from "./sessionData";

type ReportsState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; rows: SessionReportSummary[] };

function useReportList(): { state: ReportsState; retry: () => void } {
  const [state, setState] = useState<ReportsState>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    void listReports().then(
      (rows) => { if (alive) setState({ kind: "ready", rows: Array.isArray(rows) ? rows : [] }); },
      (e: Error) => { if (alive) setState({ kind: "error", message: e.message }); },
    );
    return () => { alive = false; };
  }, [nonce]);
  return { state, retry: useCallback(() => setNonce((n) => n + 1), []) };
}

export function NowEmpty({ compact = false }: { compact?: boolean }): JSX.Element {
  const seq = useSeq();
  const resumeArm = useResumeArm();
  const safety = useSafety();
  const canControl = useCanControlMount();
  const camera = useRoleConnected("camera");
  const { cards } = useFlowLibrary();
  const { id: campaignFlowId } = useCampaignFlowId();
  const flowControls = useFlowRunControls();
  const flowsOpen = useStore((s) => s.flowsOpen);
  const { state, retry } = useReportList();

  const armed = resumeArm?.armed ?? null;
  const hold = resumeArm?.hold ?? null;
  const last = state.kind === "ready" ? state.rows[0] ?? null : null;

  const runReason = runBlockedReason(canControl, camera.connected, false);

  const title = armed ? "RUN ARMED" : "NO SESSION RUNNING";
  const hint = armed
    ? "It starts by itself when its window opens."
    : "Pick a target in Sky, or run a saved flow.";

  const runFlow = (id: string) => () => {
    // The SHARED run control, not a second copy of it: `useFlowRunControls`
    // owns the 409-unmapped question, the abort route and the timeout rule.
    // Selecting the flow first is the only thing this screen adds.
    void flowsOpen(id).then(() => flowControls.act());
  };

  return (
    <div data-testid="now-empty" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <EmptyCard
        title={title}
        hint={
          <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span>{hint}</span>
            {armed && (
              <Mono size={10} tone="dim">
                {armed.name} - {armed.owed} frames owed ({armed.accepted}/{armed.total})
              </Mono>
            )}
            {armed && hold && (
              <Mono size={10} tone="warn">
                Holding: {hold.reason}. It starts by itself when that clears.
              </Mono>
            )}
            {state.kind === "loading" && <Mono size={10} tone="dim">reading the report archive...</Mono>}
            {state.kind === "error" && (
              <Mono size={10} tone="warn">
                Couldn&apos;t read the list of session reports: {state.message}
              </Mono>
            )}
            {last && (
              <Mono size={10} tone="dim">
                Last night: {last.plan_name} - {fmtIntegration(last.integration_s)} banked,{" "}
                {endReasonMeta(last.end_reason).word.toLowerCase()}.
              </Mono>
            )}
            {state.kind === "ready" && !last && (
              <Mono size={10} tone="dim">No reports yet - the first finished run writes one.</Mono>
            )}
          </span>
        }
        action={
          <>
            <ActionButton
              kind="primary"
              size={compact ? "md" : "xl"}
              onPress={() => nav.hub("sky")}
              data-testid="now-find-target"
            >
              FIND A TARGET
            </ActionButton>
            {state.kind === "error" && (
              <ActionButton kind="secondary" onPress={retry} data-testid="now-reports-retry">
                RETRY
              </ActionButton>
            )}
            {last && (
              <ActionButton
                kind="secondary"
                onPress={() => nav.sheet("files", { src: last.id })}
                data-testid="now-last-files"
              >
                {(last.plan_name || "LAST RUN").toUpperCase()} FILES
              </ActionButton>
            )}
          </>
        }
      />

      {Array.isArray(cards) && cards.length > 0 && (
        <Card>
          <Label>RECENT FLOWS</Label>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 6 }}>
            {[...(Array.isArray(cards) ? cards : [])]
              .sort((a, b) => (b.last_run ?? 0) - (a.last_run ?? 0) || b.updated_ts - a.updated_ts)
              .slice(0, 3)
              .map((c) => {
                const live = seq.state !== "idle" && campaignFlowId === c.id;
                const resumable = !live && armed?.origin === "flow" && armed.origin_id === c.id;
                const verb = live ? "LIVE" : resumable ? "RESUME" : "RUN";
                return (
                  <ListRow
                    key={c.id}
                    icon={<NxIcon name="flows" size={16} />}
                    title={c.name}
                    sub={c.tagline || `${c.stages} stages`}
                    right={
                      <ActionButton
                        kind={live ? "ghost" : "secondary"}
                        onPress={live ? () => nav.hub("session", "now") : runFlow(c.id)}
                        lockedReason={live ? null : runReason}
                        onExplain={explainLock}
                        data-testid={`recent-flow-${c.id}`}
                      >
                        {verb}
                      </ActionButton>
                    }
                  />
                );
              })}
          </div>
        </Card>
      )}

      {armed && safety?.connected === false && (
        <span data-testid="now-armed-no-safety">
          <Mono size={10.5} tone="warn">
            auto-resume armed without a safety monitor - rig may start in bad weather
          </Mono>
        </span>
      )}
    </div>
  );
}
