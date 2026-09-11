// api/planning.ts: the planning surface's persisted state (D-FU-1). Server:
// server/astrodeck/planning.py over config.py:1094-1189.
//
// WHY THIS IS A SERVER BLOCK AND NOT A localStorage KEY. The quick-plan
// defaults and the target pool are properties of THE RIG - which filters this
// wheel has, how long this f/5 refractor needs per sub, which targets this
// operator is working through. Held per browser they were per phone: the same
// operator opening the same rig from a tablet got a blank shortlist and the
// shipped exposures back, and the rig itself could not tell a client what it
// had learned. They are also the only planning state a second client would have
// to be told about, which a browser-local key cannot do at all.
//
// THE CAPABILITY IS `control.capture`, NOT a config capability, and that is
// deliberate: setting up tonight's shortlist is the same class of act as
// starting a capture, and gating it on admin would put the person actually at
// the telescope behind the person who set the rig up. It is also NOT relay
// fenced - planning from the sofa is the product.

import { api } from "../api";
import type { PlanningConfig, PlanningPatch } from "../types";

/** `GET /api/planning` (`view.status`) - the whole block.
 *
 *  It also rides on `GET /api/config` as `config.planning`, but that copy is a
 *  bootstrap: read it from here when the planning surface opens, so a second
 *  client's write is picked up. */
export const getPlanning = (): Promise<PlanningConfig> =>
  api.get<PlanningConfig>("/api/planning");

/** `PUT /api/planning` (`control.capture`) - a PARTIAL update, answered with the
 *  WHOLE block.
 *
 *  THE TWO BLOCKS MERGE BY DIFFERENT RULES (`planning.py:122-153
 *  merge_planning`), and getting them the wrong way round silently destroys
 *  data:
 *
 *  - `quick` IS A NESTED PARTIAL. Only the fields actually present move, so
 *    `{quick: {hours: 3}}` sets the hours and leaves every learned per-filter
 *    exposure exactly where it was. A wholesale replace looks identical in every
 *    round-trip test and erases everything the rig learned the first time a
 *    client with a narrower idea of the block touches an unrelated toggle.
 *  - `pool` IS REPLACED WHOLE when present. A list has no field names to merge
 *    by, the order IS the shortlist's running order, and the only sane meaning
 *    of "here is my pool" is "this is my pool". Send `[]` to empty it; omit it
 *    to leave it alone. Those are different requests.
 *
 *  `learned` IS THE CLIENT'S FLAG and the route never sets it implicitly. An
 *  implicit "any quick write means something was learned" would make the field
 *  un-clearable through the only route that writes it - the exact shape that
 *  made `cooling.setpoint_c` unwritable and cost 19 warm frames. The sheet that
 *  learns the defaults sends the flag with them.
 *
 *  PRESENCE IS THE KEY BEING THERE, read off `model_fields_set` server-side, and
 *  an explicit `null` for either block is read as ABSENT rather than as "reset
 *  it" - so `JSON.stringify` dropping an `undefined` is safe, and a hand-built
 *  `{pool: null}` is a no-op rather than a wipe. This wrapper still sends only
 *  the keys the caller passed, so nothing depends on that leniency.
 *
 *  422 on a pool over 200 entries, an empty or over-64-character id, an exposure
 *  that is not a positive finite number, or any key the model does not know
 *  (`extra="forbid"` at both levels, so a typo is refused at binding and NOTHING
 *  is half-applied). The body is FastAPI's usual validation-error array, which
 *  `parseApiError` already turns into "field: message". */
export function putPlanning(patch: PlanningPatch): Promise<PlanningConfig> {
  // Built key by key rather than spread, for the same reason
  // `putSwitchPortSettings` is: a spread over a defaults object would forge a
  // `pool` the caller never sent, and on this route sending a pool REPLACES it.
  const body: PlanningPatch = {};
  if (patch.quick !== undefined) body.quick = patch.quick;
  if (patch.pool !== undefined) body.pool = patch.pool;
  return api.put<PlanningConfig>("/api/planning", body);
}
