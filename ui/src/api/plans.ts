// api/plans.ts - typed wrappers for the saved-plan library.
//
// WHY THIS FILE EXISTS NOW. `components/sequence/PlanLibraryPanel.tsx` reached
// the two read routes with bare `api.get<PlanRow[]>("/api/plans")` calls
// (`:72`, `:162`), which was fine while the plan library had exactly one
// consumer. Session - Now's TONIGHT'S LIST is the second, and D-FU-3's whole
// point is that a phone can start a saved plan without the plan editor - so the
// two paths have to agree on the shape and on the encoding of the id.
//
// THE ROW IS `types.ts`'s `PlanRow`, NOT A SECOND COPY. The server's
// `PlanLibrary.list()` returns `{id, name, frames, integration_min, targets,
// mtime}` (`server/astrodeck/plans.py:154-171`, `_summarize` at `:49-58`), which
// is exactly what `types.ts:2116` already declares. The T-U7a-G brief spelled
// the integration field `integration_s`; the wire says MINUTES and the file the
// brief cites as the authority (`PlanLibraryPanel.tsx:72`) types it as
// `integration_min`, so the wire wins and the brief's spelling is recorded as a
// deviation rather than fixed by inventing a field the server never sends.
//
// `GET /api/plans/{id}` returns the WHOLE `SequencePlan`, not a row: it is the
// document the plan editor loads and the document `POST /api/sequence/start`
// takes in its body (`app.py:4223`, `:6551-6563`).
import { api } from "../api";
import type { PlanRow, SequencePlan } from "../types";

export type { PlanRow, SequencePlan };

/** Rows only, newest first (the server sorts by mtime). `view.status`, so a
 *  viewer sees the library and is refused only at RUN. */
export const listPlans = (): Promise<PlanRow[]> => api.get<PlanRow[]>("/api/plans");

/** The full plan document. 404s (ApiError) when the id is unknown - that is the
 *  answer, not a fault: a plan can be deleted between the list and the press. */
export const getPlan = (id: string): Promise<SequencePlan> =>
  api.get<SequencePlan>(`/api/plans/${encodeURIComponent(id)}`);
