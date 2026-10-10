// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// openFlow.ts - the two doors in and out of the Flows editor, in one
// component-free module so every surface uses the same one.
//
// WHY A MODULE AND NOT A LINE AT EACH CALL SITE. Both doors were defects found
// by the whole-branch review, and both are the shape that comes back the moment
// there are two copies of them:
//
//   THE WAY OUT (P0). `flowsCloseEditor()` SAVES first and then reloads the
//   library - that is the whole sentence the legacy `FlowHeader.tsx:167-177`
//   wrote beside its `< LIBRARY` button. Nothing under `next/**` called it, so
//   BACK, the FLOWS sub-nav chip and a reload each dropped every edit on the
//   floor without a word. There are four ways out of the editor now (the MY
//   FLOWS button, the sub-nav chip, the phone sheet's BACK and the browser's own
//   Back), and a save that lives on only three of them is the same defect with a
//   smaller blast radius.
//
//   THE WAY IN (P0). `flowsOpen(id)` swallows its own failure (its catch sets
//   `libraryError` and returns), and it leaves the PREVIOUSLY loaded record in
//   place when it does. So `await flowsOpen(B)` followed by `flowsRun()` posts
//   `/api/flows/A/run` - a press on one row starting a different flow,
//   invisible until the wrong mount moves. Every caller that acts on the
//   record it just asked for has to check that it got it, and this is that
//   check.
//
// `flowsOpen` still returns `void` and still swallows; the identity of what
// landed is readable from `useStore.getState().flows.record`, and the failure
// text from `flows.libraryError`.
//
// AND AN OPEN IS A WAY OUT (#450). The save-first rule above was kept only by
// the exits that are components, and this root renders only the active hub:
// a hub switch unmounts every one of them at once, so an edited graph stayed
// in the store and the next `flowsOpen` from another hub replaced it without
// a word. The store keeps the rule now: `flowsOpen` saves a dirty open record
// of another id first, and REFUSES when that save does not keep its edits,
// leaving that record in place, as a failed read does. That makes a refusal
// one more way for the record to stay what it was, which the check here
// already catches (a caller that skips it acts on the other flow, #499), and
// `libraryError` then carries the refusal's sentence (flowsSlice
// `FLOW_OPEN_OVER_UNSAVED`) for `flowOpenFailure` to say.

import { useStore } from "../../../../store";

/** Toast title when the flow a control names did not load.
 *
 *  It says what did NOT happen, because the dangerous reading of a silent
 *  failure is "it started". */
export const FLOW_OPEN_FAILED = "That flow did not open";

/** The fallback detail when `flowsOpen` answered with a different record and
 *  wrote no error of its own - a 200 carrying another flow's id. Rare, and
 *  exactly the case a bare `record?.id` check exists to catch. */
export const FLOW_OPEN_MISMATCH =
  "The server answered with a different flow, so nothing was started.";

/** `flowsOpen(id)` and then: did `id` actually land?
 *
 *  Resolves true only when `flows.record.id` is the id that was asked for.
 *  On false the caller must act on NOTHING - not on the record that is still
 *  loaded, which belongs to whatever was open before. */
export async function openFlowById(id: string): Promise<boolean> {
  await useStore.getState().flowsOpen(id);
  return useStore.getState().flows.record?.id === id;
}

/** The sentence to put under {@link FLOW_OPEN_FAILED}: the server's own words
 *  when `flowsOpen` wrote any, its refusal when it would not open over unsaved
 *  edits (#450), and the mismatch sentence when it wrote neither.
 *
 *  Read AFTER {@link openFlowById} resolves false. `libraryError` is the field
 *  `flowsOpen` writes on a failed read and on that refusal. The library LOAD
 *  never sets it (it has `libraryLoadError`, #877), so a load that fails
 *  while this open is in flight cannot be reported here as the open's reason;
 *  a load's success clears only what was there before the load started.
 *
 *  `previousError` IS NO LONGER COMPARED (#555). `flowsOpen` now clears
 *  `libraryError` itself before every open that gets far enough to try a read
 *  (see its comment, "CLEARED HERE"), so any value left once `openFlowById`
 *  resolves false was written by THIS attempt - whether or not its text
 *  happens to match an earlier failure's. The old `now !== previousError`
 *  text comparison read two identical, back-to-back "no flow named <id>"
 *  errors as "nothing changed" and reported the SECOND one as
 *  {@link FLOW_OPEN_MISMATCH} - a claim ("the server answered with a
 *  different flow") the code path never checked. The parameter stays so
 *  every existing call site keeps its shape; a caller may pass `null`. */
export function flowOpenFailure(previousError: string | null): string {
  void previousError;
  const now = useStore.getState().flows.libraryError;
  return now ?? FLOW_OPEN_MISMATCH;
}

/** The error that was showing before an open, for {@link flowOpenFailure}. */
export function libraryErrorNow(): string | null {
  return useStore.getState().flows.libraryError;
}

// ----------------------------------------------- the cold-load race (#658)

/** True once `FlowsScreen`'s own `flowsLoadLibrary()` - started the SAME
 *  render a cold `?open=<id>` mounts on - has answered. */
export function libraryHasLoaded(): boolean {
  return useStore.getState().flows.libraryLoaded;
}

/** How long {@link waitForLibrary} waits for the flows list to land before
 *  giving up on it and retrying the open anyway (#658). Not tied to
 *  `flows.libraryLoadError`: the list's load has had its own field since #877
 *  (it used to share `libraryError` with single-flow reads, so "the list
 *  failed" could not be told from "this read just failed"), but the load asks
 *  again by itself for up to about 82 s (#859), so that field says nothing
 *  inside this wait. A bound this generous is invisible on a live rig and
 *  still finite on a dead one. */
export const LIBRARY_RETRY_WAIT_MS = 5000;

/** A {@link waitForLibrary} in progress: `promise` resolves once the flows
 *  list has loaded or the bound has passed, and `cancel` releases the store
 *  subscription and the timer early, for a caller whose own effect unmounts
 *  or re-fires before either happens. */
export interface LibraryWait {
  promise: Promise<void>;
  cancel: () => void;
}

/** Resolves once the flows list has loaded, or after {@link
 *  LIBRARY_RETRY_WAIT_MS}, whichever comes first - the one wait the cold-load
 *  retry (#658) gives the list before a caller's open effect tries again.
 *  Lives here, in the component-free door module both `FlowsCanvasHost.tsx`
 *  and `FlowStagesPhoneSheet.tsx` already import, rather than in either one
 *  of them: `FlowsCanvasHost.tsx` pulls the canvas barrel in
 *  (`./canvas/index.ts`), which re-exports `FlowStagesPhoneSheet` - so an
 *  import the other way, from the phone sheet back to the host, would be a
 *  cycle (W7 follow-on, #658 class: one race, one fix, read from one place).
 *
 *  THE SUBSCRIPTION AND THE TIMER OUTLIVE `finish()` ALONE, UNLESS CANCELLED.
 *  Before `cancel` existed (on the host's own first version of this helper),
 *  a caller whose component unmounted mid-wait had no way to let go of
 *  either early, so a subscription to the WHOLE store and a pending timer
 *  sat there for up to {@link LIBRARY_RETRY_WAIT_MS} after the component
 *  that asked for them was gone. Every caller's own effect cleanup calls
 *  `cancel()`, so neither outlives the effect that started it. `cancel`
 *  never resolves `promise`: the caller that cancels is already tearing down
 *  (or about to re-fire with new deps), so nothing is left to act on a late
 *  resolution anyway. */
export function waitForLibrary(): LibraryWait {
  if (libraryHasLoaded()) return { promise: Promise.resolve(), cancel: () => {} };
  let settled = false;
  let unsubscribe: () => void = () => {};
  let timer: ReturnType<typeof setTimeout> | null = null;
  const promise = new Promise<void>((resolve) => {
    const finish = (): void => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      unsubscribe();
      resolve();
    };
    unsubscribe = useStore.subscribe((s) => { if (s.flows.libraryLoaded) finish(); });
    timer = setTimeout(finish, LIBRARY_RETRY_WAIT_MS);
  });
  const cancel = (): void => {
    if (settled) return;
    settled = true;
    if (timer !== null) clearTimeout(timer);
    unsubscribe();
  };
  return { promise, cancel };
}

/** One close at a time.
 *
 *  Two doors can fire at once for real: the phone sheet's BACK calls this and
 *  then pops the route, and the Flows screen's own effect sees "the list is
 *  showing while the store still holds an open record" one render later. Both
 *  are right to ask; a second PUT and a second library GET are not. The
 *  in-flight promise is handed to the second caller so it can still await the
 *  same close. */
let closing: Promise<void> | null = null;

/** Leave the editor the way the legacy header did: SAVE, then clear, then
 *  reload the library.
 *
 *  A no-op when there is nothing open, so every exit can call it without first
 *  working out whether it is the one that has to.
 *
 *  `flowsSave` itself declines a read-only or unchanged flow, so this issues a
 *  PUT only when there is something to store. */
export function leaveFlowEditor(): Promise<void> {
  const s = useStore.getState();
  if (!s.flows.record && s.flows.ui.screen !== "editor") return Promise.resolve();
  if (closing) return closing;
  closing = s.flowsCloseEditor().finally(() => { closing = null; });
  return closing;
}

/** Test hatch: drop the in-flight guard so one test's unresolved close cannot
 *  swallow the next test's. Production never needs it - the promise clears
 *  itself. */
export function resetFlowEditorDoorForTests(): void {
  closing = null;
}
