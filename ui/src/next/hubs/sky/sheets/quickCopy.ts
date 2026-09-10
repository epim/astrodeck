// quickCopy.ts - every sentence the quick-session sheet and the flow card say
// out loud, in one place (hub-sky plan D.1-D.5, T-SKY-3).
//
// WHY A COPY MODULE. Two of these strings are the whole feature. The filter
// footer states what the engine will actually do with the window, and the
// AUTOMATION explanations are the only place a first-timer learns what "dither"
// is. Both are quoted verbatim in tests, so a well-meaning edit to a sentence
// that changes what it CLAIMS fails a test instead of shipping a promise the
// engine does not keep.
//
// The `INFO` table is transcribed from the design prototype's own
// (`<scratch>/seams/proto/logic.js:37-60`), unchanged apart from the house rule
// that UI strings use hyphens, never em-dashes. The prototype already does.

export interface InfoTopic {
  /** The brief's title, shown in accent caps. */
  t: string;
  /** One paragraph of prose, saying something the screen does not. */
  b: string;
}

/** Hold-to-learn topics. Keyed exactly as the chips and labels name them, so a
 *  chip cannot ask for an explanation that does not exist. */
export const INFO: Record<string, InfoTopic> = {
  setup: {
    t: "WHAT THESE MEAN",
    b: "Night arc: how long to image. Filter cycle: which filters, how long each sub. "
      + "Automation: focus, guiding, dither, cloud hold, HFR watchdog, live stack. "
      + "Hold any label for its own explanation.",
  },
  arc: {
    t: "NIGHT ARC",
    b: "Your target's height in the sky from now to dawn. Drag the handle to choose how "
      + "long to image. Amber = forecast cloud (the flow pauses and resumes), red = behind "
      + "your horizon, dashed line = your site's horizon limit. The number is under the "
      + "chart, because it is your site's and not a constant.",
  },
  wheel: {
    t: "FILTER CYCLE",
    b: "One sub per filter per pass, round and round, so every channel grows together. "
      + "If clouds end the night early you still have a balanced set. Untick a filter to "
      + "skip it; tap the exposure to change it.",
  },
  af: {
    t: "AUTOFOCUS",
    b: "Steps the focuser through a V-curve and picks the sharpest point before the first "
      + "light frame, then again whenever the HFR watchdog asks.",
  },
  guide: {
    t: "GUIDING",
    b: "PHD2 watches a star and nudges the mount so long exposures stay pin-sharp. Turn "
      + "off for very short subs.",
  },
  dither: {
    t: "DITHER",
    b: "Shifts the frame a few pixels between subs so hot pixels and pattern noise average "
      + "out when you stack. Every 3 frames is a good default - hold the chip and slide to "
      + "change it.",
  },
  cloud: {
    t: "CLOUD HOLD",
    b: "When the forecast or the frames say cloud, the flow pauses at a frame boundary, "
      + "keeps guiding if it can, and resumes when it clears. Off means it keeps shooting "
      + "regardless.",
  },
  hfr: {
    t: "HFR WATCHDOG",
    b: "Half-flux radius measures star size. If it drifts past 3.2″ the flow refocuses "
      + "instead of banking soft frames.",
  },
  stack: {
    t: "LIVE STACK",
    b: "The rig aligns and stacks subs as they land so you can watch the image build here. "
      + "A preview only; your FITS are untouched.",
  },
  flats: {
    t: "DUSK FLATS",
    b: "Holds the flow until the twilight window, then shoots a flat set per filter before "
      + "darkness is spent on it. Flats divide out dust and vignetting; without them a "
      + "stack keeps every shadow the optics put there.",
  },
  darks: {
    t: "DARKS AFTER",
    b: "Queues darks and bias at the end of the night, with the wheel rotated to a blackout "
      + "slot. They are shot at the same temperature and exposure as tonight's lights, which "
      + "is the only way the library matches them later.",
  },
  floor: {
    t: "HORIZON LIMIT",
    b: "The altitude your site refuses to shoot below - trees, a roofline, or simply the "
      + "air. Low down the light crosses far more atmosphere: worse seeing, more "
      + "extinction, more light pollution. Flows treat it as a suspend boundary, the "
      + "ranking treats it as out of reach, and it is set per site in SITES > HORIZON.",
  },
};

/**
 * The footer under the filter rows.
 *
 * CONTROLLER DECISION, and a deliberate deviation from the design README's
 * "Time splits evenly across the checked filters". The number the server takes
 * is `subs` PER FILTER (`FlowQuickBody.subs`, `wizard.quick(subs_per_filter=)`)
 * and there is no way to express a different count per slot - `cycle.params`
 * has one `cycles` number and a `plan` string of names and exposures. So the
 * sheet asks for the number of full PASSES that fit the window, every checked
 * filter gets one sub per pass, and the rows all show the same count. An even
 * split would have promised each channel its own share and then sent a single
 * number the engine applies to all of them.
 */
export const FILTER_FOOTER =
  "Each pass shoots one sub per checked filter and passes repeat until the window ends, "
  + "so a clouded-out half night still stacks in every channel. Focus offsets apply per filter.";

/**
 * The legend under the night arc, which is where the horizon NUMBER belongs.
 *
 * It used to be baked into the `arc` brief as "the 25° floor" while the chart
 * drew 25 and the fetch used the site's own limit (review #34). A site with a
 * 30 degree limit therefore read a sentence, a dashed line and a red curve that
 * disagreed with each other and with the engine. The sentence is now generic
 * and the number is rendered from the same value the fetch used.
 *
 * Zero is not a limit, it is the absence of one, and it says so rather than
 * drawing a "0° limit" on the horizon line.
 */
export function floorLegend(horizonMinDeg: number): string {
  return horizonMinDeg > 0
    ? `Dashed line - your ${Math.round(horizonMinDeg)}° horizon limit. Red is below it.`
    : "No horizon limit set for this site, so nothing is out of reach - set one in SITES.";
}

/** The lock on GENERATE FLOW when nothing is ticked. */
export const NO_FILTER_REASON =
  "Tick at least one filter - the night has nothing to shoot otherwise.";

/** What the FILTER CYCLE header says when the wheel is not this rig's. */
export const ASSUMED_WHEEL_NOTE = "no wheel connected - showing the assumed seven";

/** The flow card's closing sentence. `issues` is the compiler's own count and
 *  is never a hardcoded number - the prototype's "the doctor passed all 13
 *  checks" is a fixture value and would be a lie on every real graph. */
export function flowFooterLine(issuesLine: string): string {
  return `Compiles to a SequencePlan plus when/then instructions. ${issuesLine} `
    + "Every stage stays editable in Flows.";
}

/** OPEN IN FLOWS on a phone. The reason names the BREAKPOINT, not the role -
 *  an operator who reads "needs operator access" here would go looking for a
 *  permission they already hold. */
export const FLOWS_NEEDS_WIDTH =
  "The flows canvas needs a tablet or a desktop - this card has every stage it holds.";

/** The 409 `unmapped` question. Not an error: the server is asking whether the
 *  operator accepts running a graph part of which will not be honoured. */
export const UNMAPPED_TITLE = "Parts of this flow do not survive the compile";
export const UNMAPPED_CONFIRM = "RUN ANYWAY";
export const UNMAPPED_CANCEL = "CANCEL";

/** H.6: `nodeDefs` has no `mosaic` node, and the engine's mosaic mechanism is N
 *  plan targets sharing a group. The lane card is honest about being drawn, not
 *  compiled. */
export const MOSAIC_FOOTNOTE = "the panels are plan targets, not a flow stage";

/**
 * What GENERATE FLOW will do with a framing that has more than one panel, said
 * before it is pressed rather than in a toast afterwards.
 *
 * It names the SPLIT because the split is surprising: the flow is saved for the
 * framing centre, and the panels are queued as plan targets sharing a mosaic
 * group. That is the engine's own mosaic mechanism (there is no mosaic node),
 * and it is the difference between "my mosaic is in the flow" and finding six
 * targets in the plan.
 */
export function mosaicPlanNote(panels: number, cols: number, rows: number): string {
  return `Framed as a ${cols}×${rows} mosaic. GENERATE FLOW saves the flow for the framing `
    + `centre and queues all ${panels} panels as plan targets in one mosaic group, each `
    + "carrying the camera angle above. The engine shoots a pass at each panel in turn, so "
    + "a clouded-out night still leaves every panel with data. Re-framing replaces them "
    + "rather than adding a second set.";
}

/** A.10's footer, verbatim from the design prototype. */
export const TARGETS_FOOTER =
  "Tap a row to swing the finder to it and draw its path; ⓘ for what it is and how to "
  + "shoot it. Kinds hidden by the lens are hidden here too.";

/** A.10's empty state. */
export const TARGETS_EMPTY = "Nothing matches the lens - tap the lens to show more kinds.";

/** A.11's honesty footer for a deep-sky object, from `ObjectCard.tsx:164-170`.
 *
 *  One character differs from that source: its em-dash is a hyphen here,
 *  because ARCHITECTURE non-negotiable 5 is "hyphens, never em-dashes, in UI
 *  strings" and this is a new surface rather than the old card being edited.
 *  Nothing the sentence CLAIMS has changed. */
export const DSO_HONESTY =
  "The offline catalogue carries positions, sizes and brightnesses - no descriptions or "
  + "history for any object. Everything above is measured, not written.";
