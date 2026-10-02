// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
// `PlanLibrary.list()` returns `{id, name, frames, integration_min,
// shutter_min, targets, mtime}` for a plan (`server/astrodeck/plans.py`,
// `_summarize`), and since #378 `{id, name?, status: "unreadable", unreadable,
// mtime}` for a file that does not read as one; `PlanRow` declares both, and
// `lib/planLibrary.ts` `planUnreadableReason` tells them apart. The T-U7a-G
// brief spelled the integration field `integration_s`; the wire says MINUTES
// and the file the brief cites as the authority (`PlanLibraryPanel.tsx`) types
// it as `integration_min`, so the wire wins and the brief's spelling is
// recorded as a deviation rather than fixed by inventing a field the server
// never sends.
//
// `GET /api/plans/{id}` returns the WHOLE `SequencePlan`, not a row: it is the
// document the plan editor loads and the document `POST /api/sequence/start`
// takes in its body (`app.py:4223`, `:6551-6563`). For an unreadable file it
// answers 422 `unreadable` with the row's own reason.
import { api } from "../api";
import type { PlanRow, SequencePlan } from "../types";

export type { PlanRow, SequencePlan };

/** A row as `PlanRow` promises it: with a name.
 *
 *  The server leaves `name` OUT of an unreadable row whose file gives none a
 *  person could read (plans.py `_raw_name`), and a list line with no name is
 *  a blank line beside a DELETE. The id is the file's stem and the thing
 *  DELETE removes, so it names the row, as the session store names its own
 *  unreadable row (`UnreadableSessionRow.name`). Only an ABSENT name is
 *  filled: a name the file does give, even a blank one, is what the file says
 *  and is shown as sent. Anything that is not an object passes untouched, so
 *  the readers' own guards still see it. */
function named(row: PlanRow): PlanRow {
  if (row === null || typeof row !== "object") return row;
  return typeof (row as { name?: unknown }).name === "string" ? row : { ...row, name: row.id };
}

/** Rows only, newest first (the server sorts by mtime), unreadable files
 *  included (#378): ask `planUnreadableReason` before reading a row's
 *  numbers. `view.status`, so a viewer sees the library and is refused only at
 *  RUN. */
export const listPlans = (): Promise<PlanRow[]> =>
  api.get<PlanRow[]>("/api/plans").then((rows) => (Array.isArray(rows) ? rows.map(named) : rows));

/** The full plan document. 404s (ApiError) when the id is unknown - that is the
 *  answer, not a fault: a plan can be deleted between the list and the press.
 *  422s (ApiError, code "unreadable") for a file that does not read as a plan
 *  (#378); the list already says so, and offers no verb that reads it. */
export const getPlan = (id: string): Promise<SequencePlan> =>
  api.get<SequencePlan>(`/api/plans/${encodeURIComponent(id)}`);
