// FlowsScreen.tsx - SESSION / FLOWS: MY FLOWS, and the door to the canvas
// (plan section D; proto `29-my-flows.html`, screenshot 29).
//
// THE ONE LIBRARY, AT EVERY BREAKPOINT (wave R7, section 3.A2's collapse and
// section 6.1 defect 7). Until the cutover this screen was the phone's list and
// `components/flows/FlowLibrary` was a SECOND one, rendered by the canvas at
// tablet and desktop: same title, same rows, different chrome, and a filter box
// and folder chips that existed only on the one an operator on a phone could not
// reach. This screen absorbed those controls - the filter, the three folder
// chips with their visible-card counts, and the two creation cells - and the
// canvas now opens on a FLOW and never on a library.
//
// RUN IS NOT RE-IMPLEMENTED. `useFlowRunControls()` owns three facts that cost
// real nights to learn (`inventory-session-monitor.md` section 3.4): STOP is
// `POST /api/sequence/abort` and not a flows route; a timed-out abort is not a
// failed abort; and a 409 `unmapped` is a QUESTION, answered with the confirm
// that lists what the compile drops. This screen calls the hook. It transcribes
// none of it.
//
// WHY RUN OPENS THE FLOW FIRST. `flowsRun()` posts against `flows.record?.id` -
// the flow on the canvas - so a list row has to make its flow the open one
// before it can start it. `flowsOpen(id)` loads the record and calls
// `flowsCompile()`, which is exactly what "RUN re-compiles against tonight's
// ephemeris" means; the server re-compiles again at `/run`, and the UI only
// says so.
//
// AND WHY IT THEN CHECKS WHAT IT GOT (whole-branch review, R5 P0). `flowsOpen`
// swallows its own failure (`flowsSlice.ts:221-223`: the catch writes
// `libraryError` and returns) and leaves the PREVIOUS record in place. So an
// awaited open that 404s, times out or is refused was followed straight into
// `act()`, which posted `/api/flows/<the flow that was already loaded>/run`: a
// press on row B starting flow A, with nothing on screen to say so until the
// wrong mount moved. `openFlowById` re-reads `flows.record.id` and this screen
// posts nothing unless it is the id that was pressed.
//
// WHAT THE PHONE GAINED. A row tap used to be honest-disabled with "the flows
// canvas opens on a tablet or desktop" and there was no way to look inside a
// flow at all. It now opens the `flowStages` sheet - the stage list, the run
// monitor, tap-to-wire and the stage editor (section 4). The LIST-level OPEN THE
// FLOWS CANVAS keeps the reason, because that control really does mean the
// pannable surface, and a phone really cannot draw it.

import { useCallback, useEffect, useMemo, useState, type JSX } from "react";

import { cardMeta, cardStatus } from "../../../../components/flows/FlowLibraryCard";
import { runBlockedReason, useFlowRunControls } from "../../../../components/flows/flowRunControls";
import type { FlowCard } from "../../../../lib/flowsApi";
import { fmtClock } from "../../../../lib/eta";
import { runIsLive } from "../../../../lib/lastSessionFrame";
import {
  accessPhrase, useCanControlCapture, useCanControlMount, useRoleConnected,
} from "../../../../lib/caps";
import { resumeSession } from "../../../../api/sessions";
import { useSeq, useStore } from "../../../../store";
import { nav, useRoute } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import {
  ActionButton, Chip, EmptyCard, ListRow, Mono, TextInput,
} from "../../../ui";
import { useSessionCampaign } from "../crossHub";
import { PLAN_EDITOR_PHONE_REASON } from "../sheets/planEditor";
import { FlowRow, type FlowVerb } from "./FlowRow";
import { FlowsCanvasHost } from "./FlowsCanvasHost";
import {
  FLOW_OPEN_FAILED, flowOpenFailure, leaveFlowEditor, libraryErrorNow, openFlowById,
} from "./openFlow";

/** The proto's footer, verbatim. It is the one place the screen says what RUN
 *  actually does and why some flows are a tablet job. */
export const FLOWS_FOOTER =
  "RUN re-compiles a quick session or night plan against tonight's ephemeris "
  + "and starts it. Campaigns, calibration and templates open on the canvas "
  + "(tablet or desktop) and are monitored here.";

/** Plan section D.3. Says what still works here, so the sentence is a
 *  redirection rather than a refusal. Since the cutover it belongs to the
 *  LIST-level control alone: a row tap opens the stage list instead. */
export const CANVAS_PHONE_REASON =
  "The flows canvas opens on a tablet or desktop. Tap a flow to open its stages, "
  + "and RUN works here.";

/** Why OPEN THE FLOWS CANVAS cannot act with a library that has nothing in it.
 *  The canvas edits one flow; there is no "blank canvas" to open, because a
 *  graph with no record behind it could not be saved. */
export const NO_CANVAS_TARGET =
  "The canvas opens one flow at a time, and there is no flow here to open.";

/** Why a row's RUN cannot act while another flow is already running.
 *
 *  It used to act: `start()` saw `runControls.running`, navigated to SESSION /
 *  NOW and returned, so every row in the library was a live-looking RUN that
 *  silently did something else. Two presses of "RUN" that do different things
 *  is the shape of the defect; naming the blocker is the fix. The live row
 *  itself still says LIVE and still navigates, which is a different word for a
 *  different action. */
export const RUN_IN_PROGRESS_REASON =
  "A run is already in progress - open SESSION / NOW to watch or stop it.";

/** The SKY path, which is a different thing from the QUICK FLOW sheet beside it:
 *  SKY starts from a target with tonight's altitude window already worked out. */
export const CREATE_PHONE_HINT = "Quick sessions can also be created from a target in SKY";

/** Placeholder and label for the filter, from `FlowLibrary.tsx:183` (its ellipsis
 *  character becomes three dots - the house rule is plain ASCII punctuation). */
export const FILTER_PLACEHOLDER = "Filter flows...";

/** `FlowLibrary.tsx:260`, em-dash replaced by a hyphen. */
export const NO_MATCH_HINT = "Try a target name, filter, or technique - or clear the search.";

/** The three folder chips (`FlowLibrary.tsx:28-32`).
 *
 *  The middle one reads MINE rather than MY FLOWS on purpose: the screen's own
 *  heading is MY FLOWS, and two elements with that exact text on one screen is
 *  the shape of the defect this collapse exists to remove - a reader scanning
 *  for the list would find a filter chip. The group is labelled, so MINE reads
 *  against ALL and EXAMPLES without ambiguity. */
const CHIPS = [
  { key: "all", label: "ALL" },
  { key: "mine", label: "MINE" },
  { key: "examples", label: "EXAMPLES" },
] as const;

type ChipKey = (typeof CHIPS)[number]["key"];

const DOT_LIVE = "#00D2FF";
const DOT_CAMPAIGN = "#9B51E0";
const DOT_OK = "#3ddc97";
const DOT_WARN = "#ffb454";
const DOT_BAD = "#ff5470";
const DOT_NEVER = "#7683a5";

function dotFor(lastResult: string, live: boolean, campaign: boolean): string {
  if (live) return DOT_LIVE;
  if (campaign) return DOT_CAMPAIGN;
  if (lastResult === "ok") return DOT_OK;
  if (lastResult === "warn") return DOT_WARN;
  if (lastResult === "bad") return DOT_BAD;
  return DOT_NEVER;
}

export function FlowsScreen(): JSX.Element {
  const route = useRoute();
  const bp = useBreakpoint();
  const phone = bp === "phone";

  const cards = useStore((s) => s.flows.cards);
  const folders = useStore((s) => s.flows.folders);
  const libraryLoaded = useStore((s) => s.flows.libraryLoaded);
  const libraryError = useStore((s) => s.flows.libraryError);
  const query = useStore((s) => s.flows.ui.query);
  const folderChip = useStore((s) => s.flows.ui.folderChip);
  const screen = useStore((s) => s.flows.ui.screen);
  const dirty = useStore((s) => s.flows.dirty);
  const highlightId = useStore((s) => s.flows.ui.highlightId);
  const loadLibrary = useStore((s) => s.flowsLoadLibrary);
  const flowsSetUi = useStore((s) => s.flowsSetUi);
  const enqueueToast = useStore((s) => s.enqueueToast);

  const seq = useSeq();
  const camp = useSessionCampaign();
  const runControls = useFlowRunControls();
  const canCreate = useCanControlCapture();
  const canControlMount = useCanControlMount();
  const camera = useRoleConnected("camera");
  const openRecordId = useStore((s) => s.flows.record?.id ?? null);

  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => { void loadLibrary(); }, [loadLibrary]);

  const createReason = canCreate ? null : `Creating a flow needs ${accessPhrase("control.capture")}.`;

  // The hook's own `reason` narrows on whether a run is LIVE, because its button
  // is a RUN/STOP toggle. A list row is not: it shows LIVE and navigates instead
  // of offering to stop, so the sentence it wants is always the starting one.
  // The sentence itself still comes from `runBlockedReason` - only the third
  // argument is ours.
  const runReason = runBlockedReason(canControlMount, camera.connected, false);

  // The rig's own refusal outranks ours: a viewer with a run in flight is still
  // refused for the reason that will be true after the run ends, so they are not
  // told to go and stop something they could not have started.
  const listRunReason = runReason
    ?? (runControls.running ? RUN_IN_PROGRESS_REASON : null);

  // `flows.run.phase` is written optimistically by `flowsRun` and by nothing
  // server-side (`inventory-session-monitor.md` section 3.0), so it leads the
  // engine's own `sequence.state` by however long the socket takes. Both count:
  // a row that still said RUN in that gap would offer to start a second run, and
  // `act()` would stop the first one instead.
  const startedFlowId = runControls.running ? openRecordId : null;
  const seqLive = runIsLive(seq);

  // --------------------------------------------------------------- filtering
  //
  // WHICH FOLDER A CARD IS IN comes from `flows.folders` when the server answered
  // that call, and from the card's own `readonly` flag when it did not. The
  // legacy library filtered folders FIRST and listed the cards inside each, so a
  // failed `GET /api/flows/folders` rendered a library with no flows in it at
  // all - a load error shown as data loss. Reading the card is the fallback that
  // cannot lose a row.
  const exampleFolder = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const f of folders) m.set(f.name, f.readonly);
    return m;
  }, [folders]);
  const isExample = useCallback(
    (c: FlowCard) => exampleFolder.get(c.folder) ?? c.readonly,
    [exampleFolder],
  );

  // Verbatim from the prototype (`FlowLibrary.tsx:127-128`): substring over
  // name + tagline, case-insensitive, trimmed.
  const q = query.trim().toLowerCase();
  const matched = useMemo(
    () => cards.filter((c) => !q || `${c.name} ${c.tagline}`.toLowerCase().includes(q)),
    [cards, q],
  );

  // Counts of what is VISIBLE, not the server's folder totals: with a filter
  // active a server count would disagree with the rows the operator can see
  // (`FlowLibrary.tsx:273-276`).
  const chipCount: Record<ChipKey, number> = useMemo(() => ({
    all: matched.length,
    mine: matched.filter((c) => !isExample(c)).length,
    examples: matched.filter((c) => isExample(c)).length,
  }), [matched, isExample]);

  const visible = useMemo(() => (
    folderChip === "all" ? matched
      : folderChip === "examples" ? matched.filter((c) => isExample(c))
        : matched.filter((c) => !isExample(c))
  ), [matched, folderChip, isExample]);

  // Under ALL the rows come from more than one folder, and the legacy screen
  // said which by grouping them under folder headings. One flat list is the
  // point of the collapse, so the folder rides in the row's own meta line
  // instead - the same fact, one row shorter.
  const showFolder = folderChip === "all"
    && new Set(cards.map((c) => c.folder)).size > 1;

  const openCanvas = useCallback((id: string) => {
    nav.go(`/session/flows?open=${encodeURIComponent(id)}`);
  }, []);

  const openStages = useCallback((id: string) => {
    nav.sheet("flowStages", { open: id });
  }, []);

  const start = useCallback(async (id: string) => {
    // A list row NEVER stops a run. `useFlowRunControls().act()` is a toggle, so
    // pressing RUN on a second row while one is going would abort the first -
    // and the row that did it would be the one that looked idle. The row is
    // honest-disabled for that (`RUN_IN_PROGRESS_REASON`); this is the guard
    // behind the lock, not the lock.
    if (runControls.running) { runControls.explain(RUN_IN_PROGRESS_REASON); return; }
    if (runReason) { runControls.explain(runReason); return; }
    setBusyId(id);
    let landed = false;
    const before = libraryErrorNow();
    try {
      // The record has to be the open one before `flowsRun` can post against
      // it, and opening it is also the local re-compile the footer promises.
      landed = await openFlowById(id);
    } finally {
      setBusyId(null);
    }
    if (!landed) {
      // POST NOTHING. The record still loaded belongs to whatever was open
      // before this press, and running it is the one outcome nobody asked for.
      enqueueToast({
        level: "error",
        title: FLOW_OPEN_FAILED,
        detail: `${flowOpenFailure(before)} Nothing was started.`,
      });
      return;
    }
    runControls.act();
  }, [enqueueToast, runControls, runReason]);

  const resume = useCallback(async (sessionId: string) => {
    // Same gate as RUN: resuming a campaign starts the engine, so a second one
    // while a run is live is the same collision.
    if (listRunReason) { runControls.explain(listRunReason); return; }
    setBusyId(sessionId);
    try {
      const r = await resumeSession(sessionId);
      enqueueToast(r.resumed
        ? { level: "success", title: "Campaign resumed", detail: `${r.remaining} frames still owed` }
        : { level: "warning", title: "Nothing to resume", detail: "The session owes no frames." });
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Resume did not land",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusyId(null);
    }
  }, [enqueueToast, listRunReason, runControls]);

  const rows = useMemo(() => visible.map((card) => {
    const isCampaign = camp != null && camp.flowId === card.id;
    const isLive = startedFlowId === card.id
      || (seqLive && (isCampaign ? camp.live : seq.plan_name === card.name));
    const verb: FlowVerb = isLive
      ? "live"
      : (isCampaign && camp.parked && camp.sessionId) ? "resume" : "run";

    let meta: string;
    if (isCampaign) {
      const head = `campaign · night ${camp.night} of ~${camp.totalNights}`;
      if (isLive) meta = `${head} · running now`;
      else if (camp.parked) {
        meta = `${head} · parked · resumes at dusk${
          camp.duskMs != null ? ` ${fmtClock(camp.duskMs)}` : ""}`;
      } else meta = `${head} · ${camp.bankedH.toFixed(1)} of ${camp.goalH} h banked`;
    } else {
      // `cardStatus` has designed copy for exactly two of the server's four
      // values; `warn` and `bad` print the server's OWN word rather than
      // invented copy (`FlowLibraryCard.tsx:53-60`).
      const status = cardStatus(card.last_result);
      meta = card.last_result === "ok" || card.last_result === "warn" || card.last_result === "bad"
        ? `${cardMeta(card)} · ${status.text.toLowerCase()}`
        : cardMeta(card);
    }
    if (showFolder) meta = `${card.folder} · ${meta}`;

    return {
      card,
      meta,
      verb,
      dotColor: dotFor(card.last_result, isLive, isCampaign),
      sessionId: isCampaign ? camp.sessionId : null,
    };
  }), [visible, camp, seqLive, startedFlowId, seq.plan_name, showFolder]);

  // The canvas takes the whole hub body when the route asks for it. Reachable
  // only at 768 px and up: OPEN THE FLOWS CANVAS is honest-disabled below that,
  // and a hand-typed hash lands on the same reason rather than on a canvas
  // nobody can pan.
  //
  // `?open=` is the primary answer, because the canvas has to survive a reload
  // and a share (section 4). It is not the ONLY answer: `nav.sheet` rebuilds the
  // hash from the params it is handed, so the stage editor - opened from a node
  // card with `{ node }` - clears `?open=` on the way in. `flows.ui.screen` is
  // the store's own record of which face Flows is showing (`flowsOpen` writes
  // "editor", the canvas's BACK writes "library"), and it is what stops the
  // canvas vanishing under its own sheet. Both are needed: the param alone
  // loses the canvas to a sheet, the flag alone loses it to a reload.
  const open = route.params.open ?? "";
  const canvasId = open || (screen === "editor" && openRecordId ? openRecordId : "");

  // THE PHONE'S OWN WAY OUT OF THE EDITOR, for the exits that are not a button.
  //
  // At 768 px and up `FlowsCanvasHost` catches this itself: it is mounted while
  // the editor is up, so it can see the route stop naming its flow. On a phone
  // the editor is the `flowStages` SHEET and nothing of it is mounted here, so
  // a FLOWS sub-nav chip press (or the browser's Back button) out of that sheet
  // would leave the list showing with an edited record still in the store and
  // never stored. This is that case, and `leaveFlowEditor` is the same door
  // (save, clear, reload) the sheet's own BACK uses.
  //
  // GATED ON `dirty`, deliberately. `flowsOpen` sets `ui.screen` to "editor" for
  // every caller, including a RUN pressed on a LIST ROW - which loads the record
  // purely so `flowsRun` has an id to post against, with the list still on
  // screen. Closing the editor there would clear that record out from under the
  // run about to start. A freshly opened record is never dirty, so the gate
  // separates the two cases exactly, and it is also the only case with anything
  // to lose.
  const canvasShowing = canvasId !== "" && !phone;
  const strandedEdit = !canvasShowing && route.sheets.length === 0
    && dirty && openRecordId != null;
  useEffect(() => {
    if (strandedEdit) void leaveFlowEditor();
  }, [strandedEdit]);

  if (canvasShowing) return <FlowsCanvasHost open={canvasId} />;

  const count = libraryLoaded ? cards.length : null;
  const filtered = count != null && visible.length !== count;

  // Which flow the list-level control opens. The one already loaded when it is
  // still in the visible list, else the first row: never `library`, which used
  // to open a second copy of this screen.
  const canvasTarget = visible.find((c) => c.id === openRecordId)?.id
    ?? visible[0]?.id
    ?? null;
  const canvasReason = phone
    ? CANVAS_PHONE_REASON
    : canvasTarget == null ? NO_CANVAS_TARGET : null;

  return (
    <div data-testid="session-flows" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <span className="nx-display" style={{ fontSize: 15, letterSpacing: ".1em" }}>MY FLOWS</span>
        <span data-testid="flows-summary">
          <Mono size={10} tone="dim">
            {count == null
              ? "reading the library"
              : filtered ? `${visible.length} of ${count} shown` : `${count} saved`}
            {" · quick sessions land here"}
          </Mono>
        </span>
      </div>

      {/* The filter and the two things that MAKE a flow, then the folder chips.
          The split is `FlowLibrary.tsx:160-164`'s and it is measured: all five
          in one wrapping row cost three rows at 412 px and put the first card
          below the fold. */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <span style={{ flex: "1 1 140px", minWidth: 0, display: "flex" }}>
          <TextInput
            data-testid="flows-filter"
            value={query}
            onChange={(next) => flowsSetUi({ query: next })}
            placeholder={FILTER_PLACEHOLDER}
            ariaLabel="Filter flows by name or tagline"
          />
        </span>
        <Chip
          data-testid="flows-new"
          lockedReason={createReason}
          onExplain={explainLock}
          onClick={() => nav.sheet("flowNew")}
        >
          + NEW FLOW
        </Chip>
        <Chip
          data-testid="flows-quick"
          lockedReason={createReason}
          onExplain={explainLock}
          onClick={() => nav.sheet("flowQuick")}
        >
          QUICK FLOW
        </Chip>
      </div>

      <div
        role="group"
        aria-label="Filter by folder"
        style={{ display: "flex", flexWrap: "wrap", gap: 7 }}
      >
        {CHIPS.map((c) => (
          <Chip
            key={c.key}
            data-testid={`flows-folder-${c.key}`}
            active={folderChip === c.key}
            count={chipCount[c.key]}
            onClick={() => flowsSetUi({ folderChip: c.key })}
          >
            {c.label}
          </Chip>
        ))}
      </div>

      {/* A library the client could not reach must never render as an empty one
          - that reads as data loss. Word and glyph, never a colour alone, and a
          RETRY beside it, because the slice deliberately leaves `libraryLoaded`
          false on a failure so nothing retries on its own. */}
      {libraryError && (
        <div
          role="status"
          style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}
        >
          <span data-testid="flows-error">
            <Mono size={10.5} tone="bad">Could not read the flow library: {libraryError}</Mono>
          </span>
          <ActionButton
            kind="secondary"
            data-testid="flows-retry"
            onPress={() => { void loadLibrary(); }}
          >
            RETRY
          </ActionButton>
        </div>
      )}

      {libraryLoaded && cards.length === 0 ? (
        <EmptyCard
          data-testid="flows-empty"
          title="NO SAVED FLOWS"
          hint="A quick session saves itself here the moment you start one from a target in SKY."
        />
      ) : visible.length === 0 && q ? (
        <EmptyCard
          data-testid="flows-no-match"
          title={`NO FLOWS MATCH "${query.trim()}"`}
          hint={NO_MATCH_HINT}
        />
      ) : visible.length === 0 && libraryLoaded ? (
        <EmptyCard
          data-testid="flows-folder-empty"
          title="NOTHING IN THIS FOLDER"
          hint={folderChip === "examples"
            ? "The rig ships example flows; this one has none installed."
            : "Flows you make land here. The examples are under EXAMPLES."}
        />
      ) : (
        <div
          data-testid="flows-list"
          style={{
            border: "1px solid rgba(120,140,200,.18)", borderRadius: 16,
            background: "rgba(12,14,22,.92)", overflow: "hidden",
          }}
        >
          {rows.map((r) => (
            <FlowRow
              key={r.card.id}
              card={r.card}
              meta={r.meta}
              dotColor={r.dotColor}
              verb={r.verb}
              // `busyId != null` FIRST. `r.sessionId` is null on every row that
              // is not the campaign, so `busyId === r.sessionId` was true for
              // all of them while nothing at all was busy - every RUN in the
              // list wore a spinner and claimed `aria-busy` from first paint
              // (found in the probe shot at 820, not in a test).
              busy={busyId != null && (busyId === r.card.id || busyId === r.sessionId)}
              highlight={r.card.id === highlightId}
              openTarget={phone ? "stages" : "canvas"}
              // While a run is live every OTHER row's RUN is refused by name.
              // `FlowRow` drops the reason for the LIVE row itself, whose verb
              // is LIVE and whose press is a navigation, so the one row that
              // can act still can.
              runReason={listRunReason}
              openReason={null}
              onRun={() => { void start(r.card.id); }}
              onResume={() => { if (r.sessionId) void resume(r.sessionId); }}
              onLive={() => nav.go("/session/now")}
              onOpen={() => (phone ? openStages(r.card.id) : openCanvas(r.card.id))}
              onExplain={explainLock}
            />
          ))}
        </div>
      )}

      {/* The list-level OPEN. The design puts it at the bottom because on a
          tablet it is the way INTO the workspace, not a per-row alternative -
          and unlike a row it means the pannable canvas specifically, which is
          why it keeps the phone reason a row no longer needs. */}
      <ActionButton
        kind="secondary"
        size="lg"
        full
        data-testid="flows-open-canvas"
        lockedReason={canvasReason}
        onExplain={explainLock}
        onPress={() => { if (canvasTarget) openCanvas(canvasTarget); }}
      >
        OPEN THE FLOWS CANVAS
      </ActionButton>

      {phone && (
        <ListRow
          data-testid="flows-create-phone"
          icon={<NxIcon name="sky" size={16} />}
          title={CREATE_PHONE_HINT}
          sub="SKY starts from a target and its altitude window; the flow lands in this list."
          chevron
          onPress={() => nav.hub("sky")}
        />
      )}

      {/* The doorway to everything a flow cannot express - plan identity,
          import/export, per-target scheduling, the quota guards and the
          when/then rules (plan section D.4, GAP-ANALYSIS section 7). */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip
          data-testid="flows-plan-editor"
          lockedReason={phone ? PLAN_EDITOR_PHONE_REASON : null}
          onExplain={explainLock}
          onClick={() => nav.sheet("planEditor")}
        >
          PLAN EDITOR
        </Chip>
      </div>

      {/* Readable without a hover a touch screen cannot perform - the same
          sentence the row's own `title` carries, never a second wording. */}
      {listRunReason && (
        <span data-testid="flows-run-reason">
          <Mono size={10.5} tone="warn">{listRunReason}</Mono>
        </span>
      )}
      {canvasReason && (
        <span data-testid="flows-canvas-reason">
          <Mono size={10.5} tone="dim">{canvasReason}</Mono>
        </span>
      )}

      <p style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
        {FLOWS_FOOTER}
      </p>
    </div>
  );
}

export default FlowsScreen;
