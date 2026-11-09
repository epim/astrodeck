// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// useCoverage.ts - when the Target modal asks the coverage route (#177
// package 2, WP 6.5; spec 2026-09-23 flows mosaic, 2.6).
//
// ASKED ONLY WHILE THE SHEET IS OPEN IN RUN MODE: the hook is given `active`
// (run mode) and the sheet unmounts with the modal, so a closed modal or an
// edit-mode sheet makes no request, and the answer is dropped with the sheet.
//
// THE CADENCE IS THE PROGRESS READ'S, NOT A TIMER OF ITS OWN. The slice
// re-reads the flow's progress when a frame lands on the open flow's run (at
// most once per LIVE_PROGRESS_MIN_MS) and once more when the run ends
// (flowsSlice `onSequence`), and a header is only worth reading once the
// ledger holds the frame: the coverage route is asked each time that answer
// is replaced, which is also how the two can never describe different
// moments of the ledger. The first answer is asked for on opening.
//
// NO COVERAGE TO SHOW IS NOT AN ERROR. The route is advisory: a 404 (the
// flow has no record) and a 403 (a principal without the capability) clear
// what was on screen and say nothing; any other failure (a dropped
// connection) leaves the last answer, which is a frame or two behind and
// still true of the lights it counted.

import { useEffect, useState } from "react";
import type { CoverageAnswer } from "../../../types";
import { framingApi } from "./framingApi";

/** The coverage answer for `flowId`, or null while there is none to show.
 *  `progress` is the open flow's progress answer from the store: null until
 *  it lands, and a new object each time the run's progress is re-read. */
export function useCoverage(
  flowId: string | null, active: boolean, progress: object | null,
): CoverageAnswer | null {
  const [answer, setAnswer] = useState<CoverageAnswer | null>(null);
  useEffect(() => {
    if (!active || !flowId || progress === null) return;
    let live = true;
    framingApi.coverage(flowId).then(
      (a) => { if (live) setAnswer(a); },
      (e: { status?: number }) => {
        if (live && (e?.status === 403 || e?.status === 404)) setAnswer(null);
      },
    );
    return () => { live = false; };
  }, [flowId, active, progress]);
  return active ? answer : null;
}
