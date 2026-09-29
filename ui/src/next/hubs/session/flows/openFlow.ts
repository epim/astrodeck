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
 *  `flowsOpen` writes on a failed read and on that refusal, and it is also
 *  written by a failed library load and a failed save, so a stale one is
 *  possible - hence the comparison against what was there before the call
 *  rather than a bare read. */
export function flowOpenFailure(previousError: string | null): string {
  const now = useStore.getState().flows.libraryError;
  return now && now !== previousError ? now : FLOW_OPEN_MISMATCH;
}

/** The error that was showing before an open, for {@link flowOpenFailure}. */
export function libraryErrorNow(): string | null {
  return useStore.getState().flows.libraryError;
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
