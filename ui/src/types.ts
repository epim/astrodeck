export interface DeviceInfo {
  name: string;
  kind: string;
  connected: boolean;
}

// ---------------------------------------------------------------- navigation
// ViewName is the canonical session/view union. Lives here (not store.ts) so
// types that reference a view (e.g. CheckItem.fix.view) don't create an import
// cycle with the store. store.ts re-exports this for backward compatibility.
export type ViewName =
  | "connect"
  | "capture"
  | "focus"
  | "mount"
  | "polar"
  | "guide"
  | "sequence"
  | "power"
  | "settings"
  | "monitor"
  | "atlas"
  // APPENDED (Risk-10 "land the order once, append thereafter"): the "what can I
  // image tonight?" destination. Desktop rail + mobile More sheet; never a
  // reorder/eviction of the landed primary order.
  | "tonight"
  | "report"
  | "help"
  // APPENDED (gallery design 2026-08-03 §Nav — the same Risk-10 precedent as
  // Monitor / Tonight / Reports / Help above: the landed order is never
  // rewritten, new destinations go on the end). Browsing what the rig has
  // already written to disk is not an equipment-dependent task, so this one is
  // deliberately absent from App's GATED table.
  | "gallery";

// NOV-9: the failure→topic / browsable-guide anchor set shared by
// lib/troubleshoot.ts (diagnoseFailure + TROUBLESHOOTING), Toast.action's
// "openHelp" variant, and the store's helpTopic deep-link slot. Lives here
// (not lib/) so Toast.action can reference it without an import cycle into
// lib/ — mirrors why ViewName itself lives here.
export type TroubleshootTopic =
  | "black-frame" | "star-trails" | "elongated-stars" | "wont-solve"
  | "camera-offline" | "guiding-lost" | "cooler-stuck" | "mount-move-failed"
  | "autofocus-failed" | "nina-error";

export interface MountStatus {
  ra_hours: number;
  dec_deg: number;
  ra_str: string;
  dec_str: string;
  alt: number;
  az: number;
  tracking: boolean;
  parked: boolean;
  slewing: boolean;
  // Multi-rate tracking (2026-07-21): the active drive rate + whether this mount
  // supports changing it. Absent/false on backends that don't (control hidden).
  tracking_rate?: "sidereal" | "lunar" | "solar";
  can_set_tracking_rate?: boolean;
  /** Mount advertises a home position (Home control, 2026-07-30). Absent on
   *  older servers, so the control is simply not offered there. */
  can_find_home?: boolean;
}

export interface RigStatus {
  connected: Record<string, DeviceInfo>;
  looping: boolean;
  live_stack_active?: boolean; // NOV-1: Live View armed
  bahtinov_active?: boolean; // NOV-12: Bahtinov focus aid armed
  mode?: "none" | "sim" | "alpaca" | "nina";
  mount?: MountStatus;
  focuser?: {
    position: number;
    max: number;
    temperature: number | null;
    /** Device's own in-motion report. OPTIONAL on purpose: absent means the
     *  backend cannot say (Focuser.is_moving defaults False), which is not the
     *  same claim as `false`. lib/focusMove.ts treats the two differently. */
    moving?: boolean;
    /** Can the position count be re-anchored without moving the drawtube
     *  (EAFResetPostion)? Static capability, not a reading. */
    can_set_position?: boolean;
  };
  filterwheel?: {
    position: number;
    names: string[];
    offsets?: number[];
    /** resolved name of the slot the wheel is on ("" = unknown/unnamed) */
    current?: string;
    /** per-slot blackout flags (no glass, blocks the light path) */
    opaque?: boolean[];
    /** per-slot narrowband flags — a focus sweep gives these slots their own
     *  exposure and gain (a 3-7 nm passband is a star tens of times fainter) */
    narrowband?: boolean[];
    /** Per-slot capture settings, parallel to `names`. `null` in a slot means
     *  NOT PINNED — which cannot be 0, because 0 is a real gain. They are
     *  DEFAULTS: picking the filter seeds the camera dial and fills a new plan
     *  step, and nothing rewrites a running sequence from them. The single
     *  authoritative reader is an offsets sweep, which has no plan to consult. */
    exposures?: (number | null)[];
    gains?: (number | null)[];
    /** first blackout slot, or null when the wheel has none */
    dark_slot?: number | null;
    /** Is the carousel turning right now? ABSENT means the backend cannot say
     *  (hub.poll_status publishes it in its own try, and FilterWheel.is_moving
     *  defaults False rather than guessing) — so undefined must be read as
     *  "watch the position instead", never as "not moving". */
    moving?: boolean;
  };
  rotator?: RotatorStatus;
  camera?: {
    temperature: number | null;
    can_cool: boolean;
    has_dew_heater?: boolean;
    width: number;
    height: number;
    max_gain: number;
    max_bin?: number; // UX-27: bin ceiling; UI offers 1..max_bin (default 4)
    cooler?: CoolerInfo; // monitor (Batch-2) — null/absent when no cooler
    // Warm-down ramp progress (2026-08-04). Absent when no warm has run recently:
    // the server keeps a FINISHED warm on status for ~3 min so the panel can say
    // "warm complete" instead of snapping back to a bare "Off", which is what the
    // old cut-the-TEC-dead bug also looked like.
    warm?: WarmInfo;
    // photometry/SNR design Task 7: e-/ADU at the current gain (native adapters
    // only; 0/absent = unknown). Optional — older servers omit the field.
    egain?: number;
    // Tech-debt hardening (c1): MEASURED e-/ADU per gain setting from the
    // auto-learn loop. Advanced-UI only — the driver value above always wins.
    egain_learned?: Record<string, number>;
  };
  guider?: GuideStats & { name: string };
  // --- guide-frame preview (SHARED lane; additive). Present only when the backend
  // can surface a guide-camera frame (GET /api/guide/frame.png). In NINA mode the
  // guider is PHD2 (status.guider) with no image; this optional field lets the UI
  // know a live guide-cam frame is fetchable without reading the preview itself.
  // The backend lane sets this (or extends status.guider) — coordinate the name. ---
  guide_camera?: {
    name: string; connected: boolean;
    // Why the preview has no picture right now, in the server's own words
    // ("… is also the imaging camera", "… could not deliver a frame: <driver
    // error>"). The preview panel renders an <img>, which can only observe THAT
    // the load failed, so every named refusal the route produces would otherwise
    // arrive as one generic sentence. Absent = no recent refusal; it expires
    // server-side, so it never describes a camera that has since been fixed.
    preview_reason?: string;
    // Whether the server VOUCHES for the picture the panel is currently showing.
    // Three values, and all three are load-bearing:
    //   true    a frame the server decoded and found to vary — so a dark preview
    //           is a dark sky, not an empty buffer.
    //   false   bytes it forwarded without being able to inspect them (a guider
    //           PNG this server's Pillow would not open). The browser may render
    //           them fine; the server simply has no opinion on what is in them.
    //   absent  nobody has asked this camera in the last few seconds.
    // Declared because the hub has published it since the #115 fix while the UI
    // read only preview_reason — the third state existed solely inside the
    // server, which is the same "claim outrunning its evidence" the fix was for.
    // Mutually exclusive with preview_reason: a refusal has no picture to vouch
    // for. Expires server-side on the same TTL as the reason.
    preview_ok?: boolean;
    // Which device actually produced that frame — NOT necessarily `name` above.
    // The hub picks `name` guide-camera-device-first and picks the preview
    // source connected-guider-first, so on a rig running PHD2 with a guide
    // camera also assigned (every sim rig) the two are different instruments.
    // Rides with preview_ok only. Absent = the server has no recent delivery to
    // attribute; say "the guide camera" rather than guessing from `name`, which
    // is how a warning about PHD2's bytes ended up naming the ZWO.
    preview_source?: string;
  };
  // --- monitor (Batch-2; server-computed from HA for sim/Alpaca, device value for NINA) ---
  meridian?: MeridianInfo;
  // --- reliability (additive; old clients ignore) ---
  busy?: "slewing" | "solving" | "focusing" | "capturing" | null;
  //: Every long operation in flight right now, by LANE name — "goto", "solve",
  //: "autofocus", "polar", "capture", "looping", "system.update"... `busy` above
  //: is the same set collapsed to one word for the stale-telemetry banner, which
  //: is all it was built for; it cannot tell one control's operation from
  //: another's. Use `useBusy(lane)` (lib/useBusy.ts) rather than reading this
  //: directly. Absent on a server older than 2026-08-05 — treat as unknown, not
  //: as idle.
  busy_lanes?: string[];
  nina_link?: {
    active: boolean;
    last_ok_age_s: number | null;   // seconds since last successful NINA HTTP call
    last_error: string | null;
    healthy: boolean;               // backend verdict (warming_up or age<=threshold)
    warming_up: boolean;            // true until first successful poll after bridge
  };
  // --- pluggable backends (W1.6; additive — old clients ignore). Present on
  //     every poll AND `hello`. `backend_links` is [] until a RigSpec/profile
  //     connect happened (the legacy connect_* paths leave it empty). ---
  backend_links?: BackendLink[];
  boot_connect_failed?: boolean;
  // --- onboarding (additive) ---
  disk?: DiskInfo;
  // --- automation (Batch-4b; additive). The own-cadence SafetyMonitor poller's
  //     latest cached reading, forwarded on EVERY 2s poll (the flat SafetyReading
  //     dict). `null` when no monitor is connected OR it dropped mid-session (the
  //     poller clears its cache to None) — this null is what lets the store's
  //     safety.connected self-heal on disconnect. The store normalizes it via
  //     normalizeSafety(); do NOT read the flat dict's `connected` (it has none). ---
  safety?: SafetyReading | null;
  // --- settings (additive; survives the 2s wholesale status replace) ---
  // Full poll_status site shape — superset of SiteInfo. name/lat/lon/elevation
  // are stripped over the wire for non-holders of view.site_precise (spec §2),
  // so they are optional; is_default/horizon_min_deg are always present.
  site?: {
    name?: string;
    latitude?: number;
    longitude?: number;
    elevation_m?: number;
    is_default: boolean;
    horizon_min_deg: number;
  };
  optics?: OpticsComputed;
}

// Rotator live state (CAA spec §3.2/§5.2). `sky_deg`/`mech_deg` are the sky
// position-angle and the raw mechanical reading; the offset between them
// (mechanical − sky) is NOT on the wire — lib/rotation.ts derives it from
// both live values (see adjustedPa).
export interface RotatorStatus {
  name: string;
  sky_deg: number;
  mech_deg: number;
  moving: boolean;
  synced: boolean;
  can_reverse: boolean;
  reverse: boolean;
}

export interface DiskInfo {
  free_gb: number;
  low: boolean;
  critical: boolean;
}

// UX-23: guider calibration report — pass/fail + the geometry that exposes a
// bad/flipped calibration, plus the engine's human-readable advisories.
export interface CalibrationReport {
  is_valid: boolean;
  ortho_error_deg: number;
  declination_deg: number | null;
  pier_side: string | null;
  binning: number;
  advisories: string[];
  source: string;
}

export interface GuideStats {
  guiding: boolean;
  rms_ra: number;
  rms_dec: number;
  rms_total: number;
  snr: number;
  recent: { t: number; ra: number; dec: number }[];
  // UX-15: whether rms_* / recent are true ARCSEC (guide-scope focal length
  // known) or guide-camera PIXELS (no FL → the native guider's 1:1 fallback).
  // Optional — absent on an older server payload, treated as arcsec (the prior
  // label) unless the server explicitly says false.
  is_arcsec?: boolean;
  image_scale?: number;
  // NOV-7: plain-language narration phase ("idle" | "finding" |
  // "calibrating" | "settling" | "guiding" | "lost"), or "" / absent when
  // unknown (the PHD2/NINA bridge guider leaves it unset — the narration
  // falls back to `guiding`). A bare string (not the GuidePhase union) so an
  // older/legacy payload still type-checks.
  phase?: string;
}

// SHARED lane (additive). Lightweight descriptor for the collapsible guide-cam
// preview (GuideFramePreview). The component fetches GET /api/guide/frame.png
// directly and self-manages its own loading/404 state, so this type is a small
// optional contract for any future store/aggregator that wants to surface guide
// frame availability without holding the image bytes. `ts` is server epoch seconds.
export interface GuideFrameInfo {
  available: boolean;
  ts?: number;
}

// ============================================================================
// LIVE PREVIEW (Batch-2, Pass-1) — owner: lane 2A (this lane).
// PreviewInfo is REWRITTEN: width/height → data_width/data_height plus the
// linear/full_well/display-dims/source/stretch fields. This is the program's
// only breaking rename (master §C-Risk-7). The single existing consumer
// (CaptureView header span) is rewritten by lane 2D; Monitor's PreviewTile (2E)
// uses /api/preview/{id}.png and the renamed fields. Be the contract SSOT.
// ============================================================================

export type PreviewSource = "sim" | "alpaca" | "nina";

export interface StarMark {
  x: number; // image-data pixel coords == frame.data space (NOT sensor space)
  y: number;
  hfr: number; // px
  ecc?: number; // 0..1, Pass 2 (optional; absent in Pass 1)
  theta?: number; // radians, Pass 2
}

// PRO-13 — sensor-tilt / corner-vs-center optical-aberration inspector.
// Purely additive: a zone-map aggregation of the SAME per-star ecc/theta/hfr
// PRO-7 already emits, plus a pattern classification. See docs/superpowers/
// specs/2026-07-23-tilt-inspector-design.md.
export interface TiltZone {
  hfr: number | null; // median HFR of zone stars (px); null when too few stars
  ecc: number | null; // mean elongation of trusted stars; null when none
  theta: number | null; // mean major-axis angle (radians); null when none
  n: number; // stars binned into this zone
}
export interface TiltInfo {
  cols: number;
  rows: number;
  zones: TiltZone[]; // row-major, len === rows*cols
  pattern: "uniform" | "tilt" | "coma" | "tracking";
  severity: number; // 0..1-ish relative HFR spread
  worst_zone: number | null; // index into zones, or null
}

// NOV-1 live-stacking readout — present only on raw/linear subs while Live View
// is armed (server-side accumulator). Old clients ignore it.
export interface LiveStackInfo {
  frames: number;        // subs in the current stack
  integrated_s: number;  // Σ accepted exposures
  rejected: number;      // subs skipped by drift-reject
  accepted: boolean;     // was THIS sub accepted (vs a drift skip)
  /** Why this sub landed where it did. "" is a clean constellation match;
   *  "weak_align" means it fell back to a single star, so the alignment is only
   *  as good as that star. The rest are rejections, plus "reseed" — the stack
   *  gave up on the old framing and started again on this sub. */
  reason?: "" | "weak_align" | "drift" | "no_match" | "no_stars" | "reseed" | "size";
  /** Star pairs backing the measured shift. >=3 is a real pattern; 1 is the
   *  single-star fallback. */
  support?: number;
  /** Pixels clipped out of THIS sub as bright outliers — a satellite trail,
   *  an aircraft, a cosmic ray hit. */
  clipped?: number;
  dx?: number;
  dy?: number;
}

// NOV-12 Bahtinov focus aid — present only on raw/linear subs while the aid is
// armed (server-side per-frame analysis). Old clients ignore it.
// One fitted spike line, in DATA pixel space: a point ON the line plus the
// direction to draw it in (degrees, [0,180), measured from +x). `central` marks
// the middle spike — the one that moves as you focus.
export interface BahtinovSpike {
  x: number;
  y: number;
  angle_deg: number;
  central: boolean;
}
// Drawable overlay geometry — present ONLY on a valid fit. Same pixel space as
// `star_list`, so the client scales it with the same displayScale.
export interface BahtinovGeom {
  center: [number, number]; // the star the fit centered on
  vertex: [number, number]; // crossing of the two outer spikes
  spikes: BahtinovSpike[]; // 3 lines; exactly one has central:true
}
export interface BahtinovInfo {
  valid: boolean;
  offset_px: number | null; // signed central-spike offset; null when invalid
  in_focus: boolean; // |offset_px| <= tol_px
  side: "left" | "right" | null; // geometric side of the crossing (invariant)
  direction: "in" | "out" | null; // rig-calibrated IN/OUT (side flipped by invert)
  angles_deg: number[]; // the 3 spike angles (empty when invalid)
  tol_px: number;
  reason: string; // plain-language status / why-invalid
  geom?: BahtinovGeom; // absent on an invalid fit (overlay abstains)
}

export interface PreviewInfo {
  id: number;
  stats: { min: number; max: number; mean: number; median: number; std: number };
  histogram: number[]; // DISPLAY-domain bins (see histogram_domain)
  histogram_linear?: number[]; // linear bins, present only when data_is_linear
  histogram_domain: "display" | "linear";
  exposure_s: number;
  gain: number;
  binning: number;
  data_width: number; // == frame.data.shape[1]  (was `width`)
  data_height: number; // == frame.data.shape[0]  (was `height`)
  display_width: number; // encoded preview px after ≤1400 downscale
  display_height: number;
  mime: string; // "image/jpeg" | "image/png"
  source: PreviewSource;
  is_stretched: boolean; // true => no client re-stretch (NINA)
  data_is_linear: boolean; // false for NINA (decoded-from-render)
  has_lossless: boolean; // a lossless base is cached (paused/zoom)
  full_well: number | null; // driver-derived; null => clip mask disabled
  pixel_scale_arcsec?: number; // for arcsec HFR + scale bar, when known
  bayer_pattern?: string | null; // non-null => OSC frame (mono preview note)
  auto_levels: { black: number; mid: number; white: number }; // 0..1 in display domain
  saved_path?: string;
  saved_local?: boolean; // true only if saved_path is under CAPTURE_DIR (FITS dl ok)
  hfr?: number;
  stars?: number;
  star_list?: StarMark[];
  // Median background-subtracted flux (ADU) over the trusted mid-bright stars of
  // THIS sub — the input to the per-sub SNR chip. Absent when no star qualified.
  star_flux_median?: number;
  tilt?: TiltInfo; // PRO-13: present only when enough zones are populated
  livestack?: LiveStackInfo; // NOV-1: present only while Live View is armed
  bahtinov?: BahtinovInfo; // NOV-12: present only while the Bahtinov aid is armed
  field?: PreviewField; // #182: what the rig is looking at. Absent = nothing knows.
  ts: number; // server epoch seconds (filmstrip age)
}

// ---------------------------------------------------------------- #182 field id
// "What is the rig looking at" — published from the server's own plate solve.
// ABSENT is a real and common state (nothing has solved since the last slew) and
// the UI must say WHICH thing is missing rather than printing "unknown".

/** The object that NAMES the field. Informational: it is never the plan's target
 *  name, never a folder, never the key of the frame counter. See
 *  `hub._object_cards` for the one narrow case where it may fill FITS OBJECT. */
export interface FieldIdentification {
  id: string; // "M 27"
  label: string; // "Dumbbell Nebula", else == id
  kind: "dso" | "star" | "solar_system";
  type: string; // "Planetary Nebula"
  describe: string; // one composed sentence — never prose from a model
  sep_arcmin: number; // how far off the field centre it sits
  /** The gate on everything that WRITES. False means two objects in this frame
   *  are comparable and the app is not going to pick one for you. */
  confident: boolean;
  runner_up: string | null; // named, not just counted
}

/** One catalogued object placed on THIS frame's pixels. */
export interface FieldObject extends FieldIdentification {
  x: number; // frame.data pixel space (column), 0-based
  y: number; // row
  size_px: number; // angular size through this plate; 0 for a point source
  inside: boolean; // its centre is within the frame rectangle
  mag: number | null;
  size_arcmin: number;
  constellation: string | null;
  alias: string | null;
  sep_deg?: number;
}

export interface PreviewField {
  /** `solve` = a plate solve, the only source anything may be recorded from.
   *  `pointing` = the MOUNT'S OWN CLAIM, offered to a human and never written:
   *  this rig's AM5 has no brake and has been found 50° from where it claimed. */
  source: "solve" | "pointing";
  solved_at: number; // unix seconds — the UI ages it
  id: FieldIdentification | null; // null = nothing catalogued names this field
  center?: { ra_hours: number; dec_deg: number };
  fov_w_deg?: number;
  fov_h_deg?: number;
  catalog_degraded?: boolean;
  /** Present ONLY when this preview IS the frame that was solved. A WCS from the
   *  previous frame drawn on this one is markers that look right and are not. */
  wcs?: Record<string, number | null>;
  objects: FieldObject[];
  data_width?: number;
  data_height?: number;
  /** Set when the mount's reported position and the plate disagree by more than
   *  a field. This is the condition that cost this rig a night. */
  pointing_disagrees_deg?: number;
  notes?: string[];
}

export interface Viewport {
  scale: number;
  x: number;
  y: number;
  fit: boolean;
}

export interface StretchParams {
  auto: boolean; // sticky; default true
  black: number; // display-domain 0..1, used in Manual
  mid: number;
  white: number;
  brightness: number; // simple primary slider, -1..1 (maps to mid)
  contrast: number; // display-only path (NINA), -1..1
  advancedOpen: boolean; // disclosure for B/M/W + curve, default false
}

export interface OverlayToggles {
  stars: boolean; // default false
  clip: boolean; // default false (only effective when linear+full_well)
  reticle: boolean; // default false (full reticle)
  centerMark: boolean; // default TRUE (subtle framing aid)
  tilt: boolean; // default false — tilt/aberration heatmap (PRO-13)
  // #182 catalogued-object markers on the frame. Default TRUE, and like
  // `bahtinov` that costs a novice nothing: the row only exists, and the layer
  // only draws, when the server sent objects placed on THIS frame's pixels.
  objects: boolean;
  // Bahtinov spike overlay: default TRUE. It only ever draws while the aid is
  // armed AND the fit is valid, so "on" costs a novice nothing — this flag is
  // purely the expert's opt-out for a clean canvas.
  bahtinov: boolean;
}

export interface FocusPoint {
  position: number;
  hfr: number;
}

export interface FocusEvent {
  state: "running" | "done" | "failed";
  points: FocusPoint[];
  best: { position: number; hfr: number | null } | null;
  /** What the run measured against what a fit needs — the verdict chip. */
  message?: string;
  /**
   * The run's OWN account of what to change, composed by the code that watched
   * the sweep. Declared here rather than left to the cast below it, because
   * this is the field that fixed #114: the panel used to print a generic
   * sentence keyed off the failure enum and told a user under a clear sky to go
   * check the sky, while the server had already worked out "try a longer
   * exposure than 2s, or a richer field" and thrown it away.
   *
   * store.ts reads the focus event through `as unknown as FocusEvent`, so an
   * undeclared field compiles and then silently reads `undefined` if either end
   * renames it. For a string whose entire job is to be the one true thing on
   * screen when everything else has failed, that is not a risk worth carrying.
   */
  advice?: string | null;
}

// ----------------------------------------------------------------- monitor (Batch-2)
// SequenceProgress is the named progress shape — server-computed magnitudes; the
// client derives wall-clock finish from its own clock (monitor spec §5). All ETA
// fields optional: absent => client renders "—"/low-confidence, elapsed only.
export interface SequenceProgress {
  frames_done: number;
  frames_total: number;
  percent: number; // 0..100
  elapsed_s: number; // server-computed, EXCLUDES paused time (§5)
  rejected: number; // engine already tracks _rejected

  eta_s?: number; // total predicted seconds to finish (authoritative magnitude)
  eta_confident?: boolean; // false until >= ETA_MIN_FRAMES real frames measured
  server_now_ms?: number; // server epoch at emit; client uses to offset-correct
  current_exposure_s?: number; // exposure of the step in flight (for sub-frame bar)
  frame_started_at_ms?: number; // server epoch when the in-flight exposure began
  // event-cost breakdown (transparency/debugging; not required by the UI):
  remaining_capture_s?: number;
  events_cost_s?: number; // sum of remaining dither/AF/flip costs
}

export interface SequenceState {
  // "nina_native" lets the Monitor show the honest "NINA is driving" state.
  //
  // "aborting" IS STILL A LIVE RUN. POST /api/sequence/abort awaits the whole
  // wind-down — abort the exposure, stop the guider, panel/cover off, finalize
  // the report, drain the thumbnails — which is ~210 s worst case against a 15 s
  // request cap, so the engine publishes it the moment the teardown starts and
  // only says "aborted" once the rig has actually stopped (sequence/engine.py,
  // same two-step as polar's pausing/paused). Every is-live predicate must
  // include it: a client that treats it as terminal blanks the run panel and
  // the Abort control over a rig that is still moving.
  state: "idle" | "running" | "paused" | "aborting" | "complete" | "aborted"
    | "error" | "nina_native";
  detail?: string;
  target?: string;
  target_index?: number;
  plan_name?: string;
  progress?: SequenceProgress;
  // --- automation (Batch-4b; additive — old clients ignore) ---
  // Autorun scheduling state attached while the engine is waiting for / running a
  // windowed target (schedule spec §1.9-C). `progress.rejected` already exists above.
  schedule?: {
    state: "waiting" | "ready" | "window_closed" | "never_rises";
    reason: string;
    eta_s: number;
    start_ts?: number; // unix seconds (resolved window open)
    stop_ts?: number;  // unix seconds (resolved window close)
  };
  // Live ETA chips (engine §1.9-E): meridian-flip ETA is SECONDS (ttf*3600); the
  // sensor temp / guide RMS are echoed for the run-time chips without a status poll.
  live?: { meridian_eta_s?: number; sensor_temp_c?: number; guide_rms?: number };
  // Multi-night session sub-state (sessions spec §6): present while a session
  // is running; cleared with explicit-None semantics like `schedule`. `target`
  // is a raw dict key server-side — it serializes as an explicit null (key
  // PRESENT) until the first target starts, so it must admit null, not just
  // undefined. The other fields are pydantic str/int-typed: never null.
  session?: { id: string; name: string; count_mode: string; accepted: number; target?: string | null };
  // Terminal reason — drives the run-complete Badge + Report end-reason icon.
  end_reason?: "complete" | "aborted" | "error" | "unsafe" | "dawn_cutoff" | "cooling_skip" | "quality";
}

export interface CoolerInfo {
  // under RigStatus.camera.cooler
  on: boolean;
  power: number | null; // 0..100 %, null if camera can't report power
  target_c: number | null;
  at_target: boolean; // |temp - target| <= COOLER_AT_TARGET_C (shared 1.0)
  can_report_power: boolean; // false => ThermometerBar degrades to on/off + target
}

export interface WarmInfo {
  // under RigStatus.camera.warm — server-side shape: Hub.warm_state()
  /** True while the ramp is still stepping. False = finished/stopped/never ran. */
  active: boolean;
  /** Who asked: "user" (the Warm button) or "wind-down" (the unattended
   *  abort_park_warm safety path). Worth showing: a warm nobody pressed is news. */
  source: string;
  /** FALSE means the cooler was switched off with NO ramp — no temperature
   *  readout, the ramp disabled in config, or already at ambient. The reason is
   *  in `note`. This flag exists so the UI can never present the fallback and a
   *  real ramp identically; a silent fallback to the old behaviour is worse than
   *  the old behaviour, because the product goes on promising a safe ramp. */
  ramped: boolean;
  /** TRUE when the camera backend owns the ramp (NINA warms on a duration). The
   *  setpoint is then not ours to report — progress is the clock. */
  delegated: boolean;
  start_c: number | null;
  /** Where the ramp is climbing to. */
  ambient_c: number | null;
  /** "configured" | "measured" | "assumed" — how ambient_c was arrived at. */
  ambient_from: string | null;
  setpoint_c: number | null;
  temp_c: number | null;
  rate_c_per_min: number | null;
  elapsed_s: number;
  eta_s: number | null;
  /** Human sentence: why it ended, or why no ramp ran. */
  note: string;
}

export type MeridianStatus =
  | "n_a_fork" // mount reports no flip needed (fork/non-GEM): informational
  | "flip_disabled" // plan.meridian_flip === false on a GEM: WARNING (pier risk)
  | "counting" // hours_to_flip is a real positive number
  | "due" // hours_to_flip <= 0
  | "unknown"; // can't determine (no mount / no data)

export interface MeridianInfo {
  // top-level on RigStatus
  status: MeridianStatus;
  hours_to_flip: number | null; // null unless status === "counting" | "due"
  flip_enabled: boolean; // plan.meridian_flip AND mount is GEM
  pier_side: "east" | "west" | "unknown";
}

// Cold-load aggregator response (monitor §8) for GET /api/monitor/snapshot.
export interface MonitorSnapshot {
  sequence: SequenceState;
  status: RigStatus;
  preview_id: number | null;
  guide_recent: { t: number; ra: number; dec: number }[];
  /** Long-running operations in flight on the SERVER right now ("autofocus",
   *  "goto", "polar", ...). The client's own view of what is running comes from
   *  live events, which publish their terminal tick exactly once — so this is
   *  how a client that was disconnected across that tick finds out. */
  busy?: string[];
  /** The aligner's current state, for the same reason `sequence` is here: its
   *  terminal states — a refusal, an error, a completed measurement — publish
   *  once on the bus and are never replayed, so a reload showed an idle aligner
   *  with no trace of the refusal that had just happened. Optional: an older
   *  server does not send it. */
  polar?: PolarState;
}

export interface LogLine {
  type: string;
  data: { level: string; message: string; source: string };
  ts: number;
}

/* --------------------------------------- frame settings, by PURPOSE (#176)
   Mirrors server/astrodeck/config.py FrameSettings / FRAME_SCOPES.

   ONE home per (camera, purpose), not per screen. On 2026-08-08 the operator
   set FILT=R on the Align screen and Capture said Oiii: every camera setting
   behind those screens was a private useState seeded from a constant, so no two
   surfaces could agree and a reload recovered none of them.

   The scopes stay SEPARATE on purpose — a guide camera's exposure is not the
   imaging camera's, and a 0.3 s solve frame is not a light frame. What was
   missing was a name for which is which. */
export type FrameScope = "capture" | "focus" | "solve" | "guide";

export interface FrameSettings {
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
  /** A filter NAME, or null for "leave the wheel where it is". An INTENT to
   *  move the wheel, never a claim about where the wheel is — which is why the
   *  UI renders it as a transition ("Oiii -> R") and never on its own. */
  filter: string | null;
}

export interface PolarState {
  state: "idle" | "running" | "paused" | "done" | "error";
  az_error: number;   // signed arcmin
  alt_error: number;  // signed arcmin
  total_error: number;
  progress: number;
  message: string;
  source: "nina" | "sim" | null;
}

// NOV-3: server-derived beginner difficulty tag (catalog/difficulty.py). The
// canonical union — lib/difficulty.ts re-exports it for display-helper callers.
export type DifficultyTier = "easy" | "moderate" | "hard";

export interface CatalogEntry {
  id: string;
  name: string;
  type: string;
  ra_hours: number;
  dec_deg: number;
  mag: number;
  size_arcmin: number;
  alt: number;
  az: number;
  // NOV-3 (additive): server-derived beginner difficulty. Optional so payloads
  // that predate it (older /api/catalog, test doubles) still type-check.
  difficulty?: DifficultyTier;
  surface_brightness?: number;              // mag/arcmin^2
  difficulty_source?: "heuristic" | "curated";
}

export interface SwitchPort {
  id: number;
  name: string;
  can_write: boolean;
  is_boolean: boolean;
  value: number;
  min: number;
  max: number;
  unit: string;
}

export interface AlpacaServer {
  address: string;
  port: number;
  devices: { DeviceName: string; DeviceType: string; DeviceNumber: number }[];
}

export interface NinaInstance {
  host: string;
  hostname: string | null;
  port: number;
  url: string;
  api_version: string;
  nina_version: string | null;
  devices: Record<string, string>;
}

export interface ExposureStep {
  // stable identity for multi-night session ledgers (sessions spec §1).
  // Optional: legacy localStorage plans lack it; store backfills via ensurePlanIds.
  id?: string;
  filter: string | null;
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
  count: number;
  frame_type: string;
  // PRO-5 flat auto-exposure (additive; 0/undefined = off => back-compat).
  adu_target?: number;      // >0 + Flat ⇒ solve exposure to this ADU
  panel_brightness?: number; // flat-panel level while shooting; undefined = don't touch
}

export interface Target {
  // stable identity for multi-night session ledgers (sessions spec §1).
  id?: string;
  name: string;
  ra_hours: number;
  dec_deg: number;
  center: boolean;
  autofocus_first: boolean;
  calibration: boolean;
  steps: ExposureStep[];
  // --- atlas (additive; nullable so existing plans deserialize unchanged) ---
  rotation_deg?: number;    // target camera angle (PA) — guidance only, no rotator in rig
  mosaic_group?: string;    // e.g. "M31" to group panels in the Plan UI
  // --- automation (Batch-4b; additive — backfilled with defaultSchedule() so old
  //     plans deserialize unchanged, see store.defaultSchedule / C1-27). ---
  schedule?: Schedule;
}

export interface SequencePlan {
  name: string;
  targets: Target[];
  guide: boolean;
  dither_every: number;
  dither_pixels: number;
  autofocus_every: number;
  cool_to: number | null;
  cool_timeout_s: number;
  apply_filter_offsets: boolean;
  refocus_on_temp_delta_c: number;
  meridian_flip: boolean;
  recover_guiding: boolean;
  hfr_reject_factor: number;
  park_when_done: boolean;
  warm_cooler_when_done: boolean;
  // --- automation (Batch-4b; additive — safety/escalation are GLOBAL in config,
  //     the plan carries only the master toggle + a meridian-flip warning lead). ---
  safety_check?: boolean;          // honor the configured SafetyMonitor + floor
  meridian_flip_warn_min?: number; // lead time for the live meridian-flip ETA chip
  // --- multi-night quota (sessions spec §3; additive — server defaults apply) ---
  count_mode?: "attempts" | "accepted";
  min_stars?: number;                     // star floor (0 = off)
  max_guide_rms?: number;                 // guide-RMS ceiling, arcsec (0 = off)
  max_consecutive_rejects?: number;       // per-step guard (0 = off)
  max_consecutive_rejects_night?: number; // per-night guard (0 = off)
  max_eccentricity?: number;              // per-frame median-ecc ceiling, 0..1 (0 = off)
  // --- conditional sequencer (PRO-3; additive/optional — [] / absent === today) ---
  instructions?: Instruction[];
}

// ============================================================================
// BATCH-1 ADDITIVE CONTRACTS (master-plan §A.1) — the one true shape.
// Other lanes compile against these. All additive; no existing field changes.
// ============================================================================

// ---------------------------------------------------------------- reliability
export type ToastLevel = "error" | "warning" | "info" | "success";

export interface Toast {
  id: number;          // monotonic, also React key
  level: ToastLevel;
  title: string;       // human, short, sentence-case (e.g. "Sequence failed")
  detail?: string;     // optional human second line (e.g. a suggested action)
  kind: "generic" | "sequence";  // "sequence" toasts are the de-duped focal one
  createdAt: number;   // Date.now() — NEVER mutated on coalesce
  ttl: number;         // ms before auto-dismiss; 0 = sticky (sequence-fatal only)
  count: number;       // coalesce counter for identical generic toasts
  // optional inline action — NOV-9 adds the "openHelp" deep-link variant
  action?:
    | { label: string; kind: "openLog" }
    | { label: string; kind: "openHelp"; topic: TroubleshootTopic };
  source?: string;     // originating log source (for dedupe / debugging)
}

export type WsPhase = "connecting" | "up" | "down" | "reconnecting";

export type NinaState = "ok" | "stale" | "error" | "down" | "warming" | "na";

export interface NinaHealth {
  active: boolean;          // mode === "nina" && nina_link.active
  ageMs: number | null;     // ms since backend's last successful NINA call
  state: NinaState;
  lastError?: string | null;
}

// ---------------------------------------------------- settings: site + optics
export interface Site {
  // Strippable over the wire for principals lacking view.site_precise (spec §2):
  // absent, not nulled. is_default + horizon_min_deg are always present.
  name?: string;
  latitude?: number;   // +N (stored signed)
  longitude?: number;  // +E (East-positive; matches coords.lst_hours)
  elevation_m?: number;
  is_default: boolean;
  horizon_min_deg: number;
}

export interface Optics {
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
  auto_from_camera: boolean;
  guide_focal_length_mm?: number | null; // A4: optional guide-scope focal length (mm)
  // PRO-2 F-B: the OPTICAL TUBE's name, written to the FITS TELESCOP card when
  // set and omitted when blank. Not the mount device name — stackers group on
  // this, so a wrong string splits one target across two groups.
  telescope_name: string;
}

export interface OpticsComputed {
  have_optics: boolean;
  source: "config" | "camera" | "mixed" | "none";
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
  image_scale_arcsec_px: number | null;
  fov_w_deg: number | null;
  fov_h_deg: number | null;
  fov_diag_deg: number | null;
}

// ---------------------------------------------------------------- provenance
// #129. `AppConfig` above is the GLOBAL block — and the global block is the
// LOSING layer for every key an ACTIVE PROFILE overrides. Binding a form field
// to it renders a value that is not what the rig is running, with no tell of
// any kind; that is how a profile-pinned simulator drove the polar aligner for
// twelve days while the console displayed "AstroDeck native".
//
// The server answers that with `config.effective` (server/astrodeck/provenance.py):
// per overridable key, the value in force AND the identity of the layer that
// supplied it, plus what each losing layer holds. Read the winner from here;
// read `config.providers` / `config.optics` only when you specifically mean
// "the global setting this form writes to".
export type EffectiveLayer =
  // the ACTIVE PROFILE supplied it — the override that has no other tell
  | "profile"
  // global AppConfig supplied it (a value that differs from the field default)
  | "config"
  // the CONNECTED CAMERA filled a zeroed optics field. A real winning layer:
  // reporting `pixel_size_um: 0` while the rig runs 3.76 is the same lie moved.
  | "camera"
  // nobody pinned anything. Note a persisted value equal to the model default
  // is indistinguishable from never having been set, so the server reports
  // `default` there — it under-claims rather than over-claims.
  | "default";

/** One key's provenance. `value` is what the rig runs; every other field is a
 *  layer's holding, `null` when that layer has nothing (never absent, so the
 *  block renders generically). */
export interface EffectiveEntry<T = unknown> {
  value: T;
  layer: EffectiveLayer;
  /** what the ACTIVE profile holds (raw — a pin the server discarded as invalid
   *  still shows up here, which is the only way to tell a dropped override from
   *  one that was never written). */
  profile: T | null;
  /** what global AppConfig holds — i.e. what would run without the profile. */
  config: T | null;
  /** the model's built-in default. */
  default: T | null;
  profile_id: string | null;
  profile_name: string | null;
  /** a sentence naming the deciding branch. Reuses the `"override: "` prefix
   *  convention from ProviderChoice.reason — that prefix is what let the user
   *  find the polar bug. */
  reason: string | null;
}

/** Dotted-key map: `providers.autofocus`, `optics.focal_length_mm`, … Exactly
 *  eleven keys today, but typed open so a server-side addition needs no UI
 *  change to reach the generic renderers. */
export type EffectiveConfig = Record<string, EffectiveEntry>;

export interface AppConfig {
  version: number;
  site: Site;
  optics: Optics;
  // Present on the REST GET/POST payload; OMITTED from the WS `hello` bootstrap
  // (hub.summary() seeds config without it, refreshed by the first `config`
  // event), so it must be optional to match the bootstrap reality.
  optics_computed?: OpticsComputed;
  // #129: which LAYER won, per overridable key. Optional for exactly the same
  // reason optics_computed is (the WS bootstrap omits it) — so every consumer
  // must degrade to the raw global value rather than blanking the panel.
  effective?: EffectiveConfig;
  active_profile_id: string | null;
  // --- automation (Batch-4b; additive — appended to the EXISTING config-backed
  //     AppConfig. Global safety/escalation/alerts live here, NOT on the plan;
  //     `deadman_url` is the external healthcheck ping target. Tokens are blanked
  //     server-side via ConfigStore.redacted() before they reach the client. ---
  safety: SafetyConfig;
  // Optional for the WS-bootstrap reason above — never assume it is present.
  cooling?: CoolingConfig;
  escalation: EscalationConfig;
  alerts: AlertSink[];
  // Master-library matching + stacking tolerances (PRO-1). Optional for the same
  // reason optics_computed is: the WS `hello` bootstrap omits it.
  calibration?: CalibrationConfig;
  // REDACTED outbound (P2-12): the deadman url can carry a per-ping secret in its
  // path/query, so the server blanks it and exposes only `deadman_configured`.
  // POST an empty `deadman_url` to leave the stored value unchanged (mirrors the
  // alert-token "empty means unchanged" contract); POST a non-empty url to set it.
  deadman_url: string;
  deadman_configured?: boolean;
  // --- weather (sub-project C; additive). The Astrospheric key is blanked
  //     server-side via redacted(); astrospheric_configured is the marker the
  //     write-only Settings field reads ("set" / "not set"). ---
  weather?: {
    enabled: boolean;
    cloud_threshold_pct: number;
    sustain_minutes: number;
    astrospheric_api_key: string | null;
    astrospheric_configured?: boolean;
  };
  // --- RBAC (W2.5; additive). The redacted auth state block — non-secret
  //     booleans + role allowlist. Optional: the WS `hello` bootstrap config may
  //     omit it; the first `config` event / REST GET carries it. ---
  auth?: AuthState;
  // --- self-update (Phase 3; additive). Non-secret (signing_pubkey is PUBLIC). ---
  update?: UpdateConfig;
  // --- capability providers (native parity; additive). Per-capability routing
  //     override persisted server-side (config.py ProvidersConfig); "auto" lets
  //     providers.resolve() pick, "backend"/"astrodeck" pin a family. Optional:
  //     an old WS `hello` bootstrap predates the field. ---
  providers?: ProvidersConfig;
  // --- backend drivers (equipment-drivers spec; global, never per-profile) ---
  drivers?: DriverEntry[];
  // --- rotator range-of-motion + tolerance (CAA spec §3.2; global config,
  //     mirrors server config.py RotatorConfig). Optional: an old WS `hello`
  //     bootstrap predates the field. ---
  rotator?: RotatorConfig;
  // --- offline survey pack (spec 2026-07-13). Optional: an old WS `hello`
  //     bootstrap predates the field. ---
  survey?: SurveyConfig;
  // --- file-naming template (PRO-11; appended). Optional: an old WS `hello`
  //     bootstrap predates the field. ---
  naming?: NamingConfig;
  // --- per-frame WCS stamping (per-frame-wcs; appended). `solve_saved_lights`
  //     is the MASTER enable (shipped with PRO-2 F-B); `wcs_stamp` is the
  //     additive advanced block. Both optional: an old WS `hello` bootstrap
  //     predates them. ---
  solve_saved_lights?: boolean;
  wcs_stamp?: WcsStampConfig;
  // --- file-sync push destination (Phase 2; appended). Optional: an old WS
  //     `hello` bootstrap predates the field. ---
  sync_push?: SyncPushConfig;
}

// ------------------------------------------------------ file-sync push (Phase 2)
// Mirrors server/astrodeck/config.py SyncPushConfig. `path` is a plain
// filesystem path this rig can write to — on Windows that means a mapped drive
// or a UNC share (\\nas\astro). It is NOT redacted server-side: a local path
// carries no credential, and a panel that may not say where frames are going
// cannot honestly claim they are going anywhere.
export interface SyncPushConfig {
  enabled: boolean;
  kind: "local_dir";
  path: string;
  label: string;
  limit_per_pass: number;                  // 0 = "no config bound" (server caps)
}

// GET /api/sync/push — the runner's own view of itself. Everything below
// `configured` is an OBSERVATION of passes that already ran; nothing here
// decides what gets sent, which is always a fresh diff (see sync/push.py).
export interface SyncPushStatus extends SyncPushConfig {
  configured: boolean;                     // enabled AND a usable destination
  running: boolean;                        // a pass is in flight right now
  debounce_s: number;
  sweep_interval_s: number;
  passes: number;
  total_sent: number;
  total_bytes: number;
  last_attempt_at: number | null;
  last_ok_at: number | null;
  consecutive_failures: number;
  alarm: boolean;                          // failing enough to be shouted about
  last: {
    sent: number;
    failed: number;
    bytes_sent: number;
    already_there: number;
    extra_at_destination: number;
    elapsed_s: number;
    error: string;
    summary: string;
  } | null;
}

// ---------------------------------------------------------- rotator config
// Mirrors server/astrodeck/config.py RotatorConfig (CAA spec §3.2) — the
// CONFIG (write) side of POST /api/config/rotator. range_type bounds the
// mechanical sweep (full 360° / half 180° / quarter 90°) starting at
// range_start_deg; tolerance_deg is the "close enough" band for rotate-to-PA.
export interface RotatorConfig {
  range_type: "full" | "half" | "quarter";
  range_start_deg: number;
  tolerance_deg: number;
}

// ------------------------------------------------------- capability providers
// Mirrors server/astrodeck/config.py ProvidersConfig — the CONFIG (write) side.
// Vocabulary (spec §3.4, registry-driven): "auto" | "backend" (LEGACY alias for
// the connected backend's own implementation — kept accepted forever) |
// "astrodeck" | "astap" | "sim" (implicit driver ids) | a configured driver id
// ("nina-a3f2"). Dynamic — a plain string; the server 422s unknown values.
export type ProviderKind = string;
export interface ProvidersConfig {
  autofocus: ProviderKind;
  polar_align: ProviderKind;
  solve: ProviderKind;
  // Who autoguides (P5-T1; mirrors server config.py ProvidersConfig.guide,
  // landed P2-T3). "auto" | "backend" (PHD2/NINA bridge) | "astrodeck"
  // (native engine) | "sim" (implicit driver id — see providers.py
  // valid_override_values; _resolve_guide has no dedicated "sim" branch so it
  // behaves like "auto" on a sim rig today, kept for vocabulary parity with
  // the other three capabilities).
  guide: ProviderKind;
}

// ------------------------------------------------------------- backend drivers
// Mirrors server/astrodeck/drivers.py describe_all() + config.py DriverEntry
// (equipment-drivers spec 2026-07-08 §3.1/§3.2). DriverEntry is the CONFIG
// (write) side; DriverInfo is the READ side (probe status + offers) the
// Equipment/Settings surfaces render from. The listed literals are the known
// built-ins + the native-hardware-onramp driver_types (2026-07-21: zwo-am5,
// wanderer-snowflake, zwo-usb, zwo-asi, player-one) + the implicit
// "ascom-local" row; `(string & {})` keeps it OPEN (mirrors ProfileDevice's
// free-string `backend`) since the registry lets a plugin backend declare a
// new driver_type with zero core/client edits (drivers.py driver_type_to_backend).
export type DriverType =
  | "nina" | "alpaca" | "phd2" | "sim" | "astrodeck" | "astap" | "ascom-local"
  | "zwo-am5" | "wanderer-snowflake" | "zwo-usb" | "zwo-asi" | "player-one"
  // ZWO ASIAIR network bridge — offered only when the server registered the
  // backend (it needs the optional `libasi` dependency).
  | "asiair"
  | (string & {});

export interface DriverEntry {
  id: string;            // server-minted "<type>-<4hex>", immutable
  type: DriverType;      // configured entries are only nina|alpaca|phd2
  host: string;
  port: number;
  enabled: boolean;
  label: string;
  extra: Record<string, unknown>;
}

export interface DriverDeviceOffer {
  role: string;
  name: string;
  dev_type?: string;     // Alpaca only — ALWAYS present there (ConnSpec addressing)
  dev_num?: number;      // Alpaca only
}

export interface DriverStatus {
  reachable: boolean;
  error: string | null;
  detail: string | null; // NINA API version / ASTAP path / native wheel version
  probed_at: number;     // unix seconds
}

export interface DriverInfo {
  id: string;
  type: DriverType;
  label: string;
  enabled: boolean;
  implicit: boolean;     // sim/astrodeck/astap = detected built-ins, not stored
  host?: string;
  port?: number;
  // --- native hardware on-ramp follow-ups (2026-07-21): per-unit addressing
  //     echoed on configured rows so the client can tell two identical units
  //     of the same driver_type apart. `transport` is "network"|"serial"|
  //     "local"; `port_path` (serial, e.g. "COM3") is REDACTED (absent) for
  //     callers without config.backend, mirroring `host`; `index` is the
  //     0-based SDK-enumerated unit (zwo-asi/player-one cameras only). ---
  transport?: string;
  port_path?: string;
  index?: number;
  status: DriverStatus;
  offers: { devices: DriverDeviceOffer[]; tasks: string[] };
}

export interface DriversResponse {
  roles: string[];       // fed from devices/backend.py ROLES — a new role appears free
  drivers: DriverInfo[];
}

// ------------------------------------------------ offline survey pack (spec 2026-07-13)
export interface SurveyConfig {
  /** True => hips2fits upstream is used for FOV < 4°; false (default) => pack only. */
  online_fetch: boolean;
}

// -------------------------------------------------------- file naming (PRO-11)
// Mirrors server/astrodeck/config.py NamingConfig — the CONFIG (write) side of
// POST /api/config/naming. The client PREVIEW render (ui/src/lib/naming.ts) is
// advisory only; the server render is authoritative for the real path.
export interface NamingConfig {
  template: string;
}

// --------------------------------------------------- per-frame WCS (per-frame-wcs)
// Mirrors server/astrodeck/config.py WcsStampConfig — the ADVANCED half of
// POST /api/config/wcs. The master enable is the sibling `solve_saved_lights`
// bool on AppConfig; every field here defaults to today's behaviour, so the
// bool alone fully drives the feature. `solver` is Auto/ASTAP only on purpose:
// there is deliberately no "force sim" (faking a solve on a real rig is the
// exact hazard the sim solver refuses).
export interface WcsStampConfig {
  solver: "auto" | "astap";
  downsample: number;   // 0 = automatic; else ASTAP -z (1/2/4)
  min_stars: number;    // 0 = gate off; else skip the solve below N detected stars
  queue_max: number;    // bounded backlog before the oldest pending frame is dropped
}
export interface PackFetchProgress { done: number; total: number; failed: number; }
export interface PackStatus {
  present: boolean;
  slug: string;
  survey: string;
  order: number | null;
  bytes: number | null;
  tile_count: number | null;
  fetched_at: number | null;
  fetching: PackFetchProgress | null;
}

// ---------------------------------------------------------------- self-update
// UpdateConfig is the persisted config block; UpdateStatus is the live snapshot
// the server pushes over the `update` WS event and returns from GET /api/version
// and /api/update/status. Holds no secret (the signing key here is public).
export interface UpdateConfig {
  enabled: boolean;
  auto_check: boolean;
  check_interval_hours: number;
  channel: "stable" | "prerelease";
  repo: string;
  signing_pubkey: string;
  health_timeout_s: number;
  last_check_ts?: number | null;
}

export interface UpdateStatus {
  current: string;
  latest: string | null;
  update_available: boolean;
  notes_md: string;
  channel: string;
  last_check_ts: number | null;
  phase: string; // idle | checking | downloading | verifying | staging | applying
  progress: number;
  error: string | null;
  last_result: { ok: boolean; version?: string; from?: string; reason?: string } | null;
  // only on /api/update/status (not the raw snapshot):
  supervised?: boolean;
  can_apply?: boolean;
  apply_blocked_reason?: string;
}

// ============================================================================
// AUTOMATION / SAFETY (Batch-4b; design spec §2.1). The one true shape —
// downstream lanes (Settings, Sequence schedule sub-panel, Report) compile
// against these. All additive; reuse the existing AppConfig/Target/SequenceState
// which were EXTENDED above. Tokens (telegram bot token) are never sent to the
// client — ConfigStore.redacted() blanks them, hence no `token` field on AlertSink.
// ============================================================================

// ---------------------------------------------------------------- safety device
export interface SafetyReading {
  is_safe: boolean;
  reason: string;                       // human string when unsafe (e.g. "cloud sensor")
  source: string;                       // device name
  detail?: Record<string, number>;      // optional sensor readouts
  stale: boolean;                       // true when the read timed out / device dropped
  ts: number;                           // server epoch seconds
}

export interface SafetyState {
  connected: boolean;
  reading: SafetyReading | null;
  streak: number;                       // consecutive unsafe reads (engine streak)
}

// ----------------------------------------------------------- per-target schedule
// Structured controls only — NO token mini-language (C1-24). Backfilled onto every
// Target with defaultSchedule() so old plans deserialize unchanged (C1-27).
export interface Schedule {
  start_mode: "now" | "dusk" | "dawn" | "time";
  start_offset_min: number;             // ± minutes relative to dusk/dawn
  start_time: string | null;            // "HH:MM" when start_mode === "time"
  min_altitude_deg: number;             // per-target START gate (target-alt). 0 = none
  // --- pro visibility constraints (PRO-14; additive, 0 = off) ---
  min_moon_sep_deg: number;             // ≥ this from the Moon while it's up (0 = off)
  max_moon_illum_pct: number;           // skip while Moon > this % illuminated (0 = off)
  max_hour_angle_h: number;             // image within ±this h of the meridian (0 = off)
  stop_mode: "none" | "dawn" | "time";
  stop_offset_min: number;
  stop_time: string | null;
  max_run_min: number;                  // 0 = no cap
  on_missed: "wait" | "skip";           // default "wait" (C1-25)
}

// ---------------------------------------------------- conditional sequencer (PRO-3)
// An additive when-trigger-do-action rule layer on SequencePlan. Flat closed
// enums mirror the pydantic Instruction 1:1 (not a discriminated union). A plan
// with no instructions runs byte-identical to today.
export type TriggerKind =
  | "on_hfr_above" | "on_guide_rms_above" | "on_frame_rejected"
  | "on_target_complete" | "at_time";
export type ActionKind =
  | "notify" | "pause" | "refocus" | "dither" | "abort"
  // control-flow expansion: target jumps. Both carry their destination in
  // `target_arg` (NOT `only_target`, which stays a gate).
  | "run_target" | "skip_target";
// Bounded 1-level compound condition (control-flow expansion). `terms` holds
// LEAVES only — no nesting — so the grammar stays a closed vocabulary.
export type PredicateKind =
  | "hfr_above" | "guide_rms_above" | "frame_rejected"
  | "target_complete" | "at_time";
export interface Predicate {
  kind: PredicateKind;
  threshold: number;
  at_time: string | null;
}
export interface Condition {
  op: "all" | "any";                    // all = AND, any = OR
  terms: Predicate[];                   // 2..8
}
export interface Instruction {
  id?: string;                          // uuid4 hex; generated client-side on create
  enabled: boolean;
  trigger: TriggerKind;
  threshold: number;                    // on_hfr_above / on_guide_rms_above value
  at_time: string | null;               // "HH:MM" 24h local, when trigger === at_time
  action: ActionKind;
  message: string;                      // notify text / log + abort reason
  level: "info" | "warning" | "error";  // notify severity
  once: boolean;                        // fire at most once per run
  cooldown_s: number;                   // min seconds between fires (0 = every boundary)
  only_target: string | null;           // gate: only while this target (by name) active
  target_arg?: string | null;           // jump DESTINATION for run_target / skip_target
  when?: Condition | null;              // compound condition; when set it OVERRIDES `trigger`
}

// ------------------------------------------------------------ alerting / config
// `token` is intentionally absent — the server blanks it; the client never holds it.
export interface AlertSink {
  id: string;
  kind: "ntfy" | "webhook" | "telegram" | "discord" | "slack" | "email";
  enabled: boolean;
  url: string;                          // ntfy topic url / webhook url
  chat_id?: string;                     // telegram chat id
  min_level: "warning" | "error";
  events: string[];                     // ["run_start","run_end","safety","error",...]
  verified: boolean;                    // true only after a successful round-trip test
  heartbeat_min: number;                // 0 = off; periodic progress ping cadence
  token_configured?: boolean;   // derived server marker: is the secret set?
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_from?: string;
  smtp_to?: string;
  smtp_starttls?: boolean;
}

// write body: adds the write-only secret
export interface AlertSinkInput extends AlertSink {
  token?: string;   // telegram bot token | discord/slack webhook url | smtp password
}

export interface AlertHealth {
  undelivered: number;
  undelivered_by_sink: Record<string, number>;
  deadman: { configured: boolean; healthy: boolean; last_ping_age_s: number | null };
}

/** One obstruction wedge. `alt_max` is the floor the mount must clear while its
 *  azimuth is inside the range (the server name is historical). The range wraps
 *  the 0↔360 seam: 350 → 10 is the 20° span through due north. */
export interface NoGoWedge {
  az_min: number;
  az_max: number;
  alt_max: number;
}

export interface SafetyConfig {
  enabled: boolean;
  preset: "backyard" | "remote" | "custom";
  poll_each_frame: boolean;             // read the monitor before every exposure
  min_alt_deg: number;                  // global pier-collision floor (mount-alt). 0 = off
  max_alt_deg?: number;                 // zenith keep-out CEILING. 90 = off (#101)
  horizon: [number, number][] | null;   // sorted (az,alt) control points
  // Hard-edged obstruction wedges: inside [az_min, az_max] the mount must stay
  // above alt_max. Distinct from `horizon`, whose points interpolate — a pier
  // declared at az 180 would otherwise slope a floor across the southern sky.
  nogo_box: NoGoWedge[] | null;
  enforce_pier_limits: boolean;         // only settable when mount reports pier side
  twilight_deg: number;                 // nautical −12 default (C1-26)
  // Sun-exclusion cone (W1.10). ON by default to protect deep-sky gear; a
  // deliberate solar-astronomy session disarms it via solar_avoidance=false
  // (route-gated behind config.solar_override). RA/Dec-based ⇒ site-independent.
  solar_avoidance: boolean;             // master enable; true = cone armed (deep-sky default)
  solar_exclusion_deg: number;          // cone half-angle (deg); 0..90, ≤0 = inert
  on_unsafe: "abort_park_warm" | "park" | "pause" | "warn";
  unsafe_consecutive: number;
  resume_when_safe: boolean;
  resume_safe_consecutive: number;
  max_pause_min: number;                // 0 = no cap; escalates to park on timeout
  // Roll-off roof / dome auto-close (PRO-4). All default false server-side.
  close_dome_on_unsafe: boolean;        // rain/cloud trip → park-and-close teardown
  close_dome_when_done: boolean;        // close the roof at a normal end-of-night
  // PRO-4 D3: opt-in advanced flag, ONLY meaningful when close_dome_on_unsafe is on.
  // On ⇒ instead of ending the run, close the roof, wait for safe-again, REOPEN and
  // resume. Off (default) ⇒ close_dome_on_unsafe still aborts (byte-identical).
  reopen_dome_when_safe: boolean;
}

/** Cooler warm-down policy (server: config.CoolingConfig). Lives beside the
 *  safety block and is gated on the SAME capability (config.safety), because
 *  the sentence it makes true — "park, then warm the camera at a safe ramp" —
 *  is a safety-panel promise. Optional on the wire: the WS `hello` bootstrap
 *  and any older server omit it, and every consumer must degrade to "the
 *  default 2 °C/min ramp is on" rather than blanking the control. */
export interface CoolingConfig {
  warm_ramp: boolean;            // false = cut the TEC dead (the pre-2026-08-04 bug, opt-in)
  warm_rate_c_per_min: number;   // ramp rate; server clamps to 0.1..20
  warm_ambient_c: number | null; // null = work it out (measured, else assumed 20 °C)
}

/** PRO-1 master-library matching + stacking tolerances. Mirrors backend
 *  config.CalibrationConfig; every bound is enforced server-side too. */
export interface CalibrationConfig {
  exposure_tol_pct: number;   // 0..100 — how far a master's exposure may differ
  temp_tol_c: number;         // 0..50  — how far its sensor temperature may differ
  temp_bin_c: number;         // 0..50  — stacking bucket width; must be >= temp_tol_c
  stack_sigma: number;        // >0..10 — sigma-clip threshold when combining
  max_stack_frames: number;   // 1..1000 — cap on frames per master
}

export interface EscalationConfig {
  require_cooling: boolean;
  cooling_action: "warn" | "abort" | "skip";
  require_guiding: boolean;
  guiding_action: "warn" | "abort" | "skip";
  af_failure_action: "warn" | "abort" | "skip";
  hfr_reject_action: "warn" | "discard" | "retake";  // retake = Advanced-only
  hfr_retake_limit_per_target: number;               // cap per target (C1-7)
  no_progress_watchdog_s: number;                     // 0 = off
  reconnect_resume: boolean;                          // Alpaca-only; off by default
  reconnect_retries: number;
}

// SiteConfig is the persisted lat/lon/elevation shape the automation config carries.
// (Distinct from the existing Site interface, which is the settings-panel view, and
// from SiteInfo, which is the live status view.) Mirrors backend config.SiteConfig.
export interface SiteConfig {
  latitude: number;
  longitude: number;
  elevation_m: number;
}

// ----------------------------------------------------------------- session report
export interface FilterBreakdown {
  filter: string;
  frames: number;
  rejected: number;
  integration_s: number;
  hfr_median: number | null;
}

export interface TargetBreakdown {
  name: string;
  frames: number;
  rejected: number;
  integration_s: number;
  by_filter: FilterBreakdown[];
}

// ----------------------------------------------------- PRO-10 stacking bundle
// Slim per-group preview (GET /api/reports/{id}/bundle) — no per-light rows, so
// a 2000-frame report stays a small JSON. The full manifest + weights CSV +
// build scripts ship only inside the .zip download.
export interface BundleGroupSummary {
  dir: string;
  target: string;
  filter: string | null;
  exposure_s: number;
  gain: number | null;
  binning: number | null;
  light_count: number;
  accepted_count: number;
  kept_count?: number;                  // subs with weight >= keep_threshold
  masters: Record<string, boolean>; // {dark:true, flat:true, bias:false}
}
export interface BundlePreview {
  report_id: string;
  plan_name: string;
  layout: string;
  weight_altitude: boolean;             // was the opt-in sin(alt) term applied
  keep_threshold?: number | null;       // normalized-weight tail cutoff, if any
  groups: BundleGroupSummary[];
  warnings: string[];
}
/** POST /api/reports/{id}/bundle/materialize — the server laid the actual FITS
 *  out under captures/exports/<id>/. No file body; counts + the export path. */
export interface BundleMaterializeResult {
  export_dir: string;
  layout: string;
  linked: number;                       // hardlinked (no extra bytes on disk)
  copied: number;                       // copy2 fallback (cross-device etc.)
  bytes_copied: number;
  failed: { src: string; reason: string }[];
  groups: { dir: string; linked: number; copied: number }[];
  hardlink_note: string;
}

// List-row summary (GET /api/reports) — header fields only, no per-frame payload.
export interface SessionReportSummary {
  id: string;                           // "<plan>-<YYYYMMDD-HHMMSS>"
  plan_name: string;
  started_at: number;                   // unix seconds
  ended_at: number | null;
  end_reason: string | null;            // complete|aborted|error|unsafe|dawn_cutoff
  frames_captured: number;
  frames_rejected: number;
  integration_s: number;
}

// Full detail (GET /api/reports/{id}) — trends DERIVED from frames at read time.
export interface SessionReport extends SessionReportSummary {
  by_filter: FilterBreakdown[];         // plan-wide per-filter totals (headline)
  targets: TargetBreakdown[];
  safety_events: { ts: number; reason: string; action: string }[];
  trends: {
    hfr: [number, number][];            // (ts, value) pairs
    temp: [number, number][];
    rms: [number, number][];
  };
}

export interface SkyInfo {
  sun_alt_deg: number;
  dark_window: { start_iso: string; end_iso: string } | null;
  place_hint: string;
  lst_str: string;
}

// ============================================================================
// ATLAS — Sky Atlas + Framing + Mosaic + Visibility (design spec §4.1).
// Owner A freezes these; lanes B–E compile against them. All additive.
// NOTE: `Optics` is the EXISTING px-suffixed interface above (sensor_width_px /
// sensor_height_px) — these types reference it, they do NOT redefine it.
// ============================================================================

export interface FovRect {                 // single sensor footprint
  ra_hours: number;
  dec_deg: number;
  fov_x_deg: number;
  fov_y_deg: number;
  rotation_deg: number;                    // position angle, N-up E-left
}

export interface MosaicSpec {
  ra_hours: number;
  dec_deg: number;
  rows: number;                            // 1..10
  cols: number;                            // 1..10
  overlap: number;                         // 0..0.5 (default 0.25)
  rotation_deg: number;                    // PA applied to whole mosaic
  fov_x_deg: number;                       // single-frame FOV at bin 1
  fov_y_deg: number;
}

export interface MosaicPanel {
  row: number;
  col: number;
  ra_hours: number;                        // server returns ra already %24-wrapped
  dec_deg: number;
  rotation_deg: number;
  transit_alt?: number;                    // peak alt tonight (NOT instantaneous "now" alt)
}

export interface MosaicResult {
  panels: MosaicPanel[];
  total_fov_x_deg: number;                 // tangent-plane extent
  total_fov_y_deg: number;
  frame_fov_x_deg: number;
  frame_fov_y_deg: number;
  pixel_scale_arcsec: number;
}

export interface FramingSession {
  target?: CatalogEntry;                   // origin object (undefined = free-roam)
  center: { ra_hours: number; dec_deg: number };
  rotation_deg: number;
  survey: string;                          // "CDS/P/DSS2/color" | "CDS/P/DSS2/red" | "CDS/P/2MASS/color"
  stretch: "linear" | "asinh";
  fovZoomDeg: number;                      // survey crop angular width
  prev_zoom_deg?: number;                  // zoom saved by the FOV-lock toggle (in-memory; not persisted)
  mosaic: { rows: number; cols: number; overlap: number };
  panels: MosaicPanel[];                   // generated; length 1 when 1x1
  freeroamId?: string;                     // stable mosaic-group id for free-roam (no target) sends
}

export interface MoonInfo {
  illumination: number;                    // 0..1
  phase_name: string;                      // "Waning Gibbous"
  alt: number;
  az: number;                              // at session time
  separation_deg: number;                  // from target
  rise_unix: number | null;                // tonight, null if always up/down
  set_unix: number | null;
}

export interface VisibilitySample {
  t_unix: number;
  alt: number;
  moon_alt: number;
  sun_alt: number;
}

export interface VisibilityNight {
  date: string;
  transit_unix: number;
  transit_alt: number;                                 // peak within dark window (see §9)
  transit_in_daylight: boolean;                        // true if geometric transit is in daylight
  dark_start_unix: number | null;                      // astro-dark start (null if none)
  dark_end_unix: number | null;
  darkness_kind: "astronomical" | "nautical" | "none"; // fallback ladder
  samples: VisibilitySample[];
  moon: MoonInfo;
  best_window: { start_unix: number; end_unix: number; mean_alt: number } | null;
  alt_limit_deg: number;                               // horizon limit (default 30)
  never_rises_above_limit: boolean;
}

export interface VisibilityTarget {
  name: string;
  ra_hours: number;
  dec_deg: number;
  transit_unix: number;
  max_alt: number;
  best_window: { start_unix: number; end_unix: number } | null;
  moon_sep_deg: number;
}

// ------------------------------------------------ tonight picker (NOV-3)
// GET /api/catalog/tonight — the ranked "what can I image tonight?" list. Each
// pick is a CatalogEntry-shaped object plus tonight's visibility summary and a
// difficulty tag; ordered best-window-first by the server (score desc).
export interface TonightPick {
  id: string;
  name: string;
  type: string;
  ra_hours: number;
  dec_deg: number;
  mag: number;
  size_arcmin: number;
  difficulty: DifficultyTier;
  surface_brightness: number;
  difficulty_source: "heuristic" | "curated";
  max_alt: number;
  transit_unix: number;
  best_window: { start_unix: number; end_unix: number } | null;
  moon_sep_deg: number;
  never_rises_above_limit: boolean;
  score: number;                            // server ranking key (visibility)
}

export interface TonightResponse {
  date: string;
  site_is_default: boolean;
  picks: TonightPick[];
}

// ---------------------------------------------------- onboarding: site + checks
// SiteInfo is the LIVE/status view of the site (distinct from the persisted Site).
export interface SiteInfo {
  name?: string;
  latitude?: number;
  longitude?: number;
  elevation_m?: number;
  is_default: boolean;
  horizon_min_deg: number;
}

// Saved observing location (server astrodeck/locations.py SavedLocation). Served
// ONLY by /api/locations — never embedded in config/status/WS payloads.
export interface SavedLocation {
  id: string;
  name: string;
  latitude: number;   // +N (signed)
  longitude: number;  // +E (East-positive)
  elevation_m: number;
  horizon_min_deg: number | null;
  created_ts: number;
  updated_ts: number;
}

export interface PreflightAlt {
  alt: number | null;
  az: number | null;
  verdict: "ok" | "low" | "below" | "unknown";
  horizon_min_deg: number;
  site_is_default: boolean;
  sets_in_min?: number | null;
}

export type CheckStatus = "ok" | "warn" | "blocked" | "checking" | "skipped" | "disabled";

export interface CheckItem {
  id: string;
  label: string;
  status: CheckStatus;
  word: string;               // redundant text token, always set
  detail?: { value: string; unit?: string };
  help?: string;              // HelpKey (the HELP map lives in the onboarding lane)
  fix?: { label: string; view?: ViewName; onClick?: () => void | Promise<void>; inPlace?: boolean };
}

// --------------------------------------------------- design-system display primitives
export type LedState = "off" | "on" | "warn" | "bad" | "busy";
export type Tone = "good" | "warn" | "bad";

// -------------------------------------------------------- profiles / plans (frozen)
// `backend` is a free string (registry name): the server allows "native", "sim",
// "nina", "alpaca" (legacy alias for "native"), etc. — NOT a closed union. `extra`
// carries backend-specific options (e.g. the managed-PHD2 toggle writes
// extra.managed) and is persisted verbatim; mirrors server profiles.ProfileDevice.
export interface ProfileDevice {
  role: string;
  backend: string;
  host: string;
  port: number;
  dev_type: string;
  dev_num: number;
  name: string;
  extra: Record<string, unknown>;
  // Reference to AppConfig.drivers[].id (spec §3.3/§4.4); server-persisted
  // (profiles.ProfileDevice.driver_id, default ""). Optional here so
  // pre-drivers profile payloads type-check unchanged.
  driver_id?: string;
}

export interface Profile {
  id: string;
  name: string;
  // The RigSpec primary (default backend for any role with no explicit per-device
  // override). Server defaults to "sim"; the picker writes the primary selection
  // here. Mirrors server profiles.Profile.primary_backend.
  primary_backend: string;
  devices: ProfileDevice[];
  nina_host: string | null;
  nina_port: number;
  phd2_host: string | null;
  phd2_port: number;
  optics: Optics | null;
  site_name: string | null;
  // Per-rig task overrides (spec §4.4) — wins over global config when this
  // profile is ACTIVE (server providers._override). Partial dict server-side;
  // absent on old profiles.
  providers?: Partial<ProvidersConfig> | null;
}

export interface ProfileRow {
  id: string;
  name: string;
  mode: "alpaca" | "nina" | "mixed" | "empty";
  devices_count: number;
  site_name: string | null;
  active: boolean;
  // #129: the two blocks that BEAT global config when this profile is active.
  // The row used to drop both, which made it structurally impossible for the
  // Profiles tab to show that a profile pins a capability or a focal length —
  // the list endpoint returns these rows and nothing on that screen fetches the
  // full model. `providers` keeps a literal "auto" (it still beats a global
  // "astap", so it IS an override); `optics` is the whole dump, because a
  // profile optics block is swapped WHOLE and overrides every optics field at
  // once, including the ones the profile left at its own defaults.
  providers?: Partial<ProvidersConfig> | null;
  optics?: Optics | null;
}

export interface ApplyResult {
  summary: RigStatus;
  results: { role: string; ok: boolean; error?: string }[];
  connected: number;
  total: number;
}

// ============================================================================
// PLUGGABLE BACKENDS + RBAC (W1.C / W1.6 / W2.x). The one true client shape,
// verified against server: devices/backend.list_backends (BackendInfo),
// devices/orchestrator.RoleResult (RoleResult), hub.backend_links (BackendLink),
// devices.backend.RigSpec/ConnSpec (RigSpec/ConnSpec), hub.connect_rigspec
// (ConnectRigResult), auth/principal.Principal.to_public (Principal), and
// auth/capabilities (Capability). All additive — old code ignores them.
// ============================================================================

// GET /api/backends row — devices.backend.list_backends().
export interface BackendInfo {
  name: string;          // registry key, stored in ConnSpec.backend ("sim"|"nina"|"native"|...)
  label: string;         // human name for the UI
  roles: string[];       // subset of ROLES this backend can fill
  discoverable: boolean; // true => discover() does real network work
  // Native-hardware on-ramp (2026-07-21): USB/serial backends (zwo-am5,
  // wanderer-snowflake, zwo-usb, zwo-asi, player-one) carry these so the
  // client can offer them in "Scan for USB/serial hardware" without a
  // per-backend switch statement. Optional — network backends (sim/nina/
  // native/phd2) omit `driver_type` (falsy => not a configurable driver_type).
  hardware?: boolean;    // real hardware (device-borne safety flag), not a network service
  driver_type?: string;  // the `type` to pass to addDriver() when configuring this backend
  transport?: string;    // "network" | "serial" | "local"
}

// One per-role connection override in a RigSpec (mirrors server ConnSpecBody /
// devices.backend.ConnSpec). Only `backend` is required; the rest carry the
// addressing a given backend needs (native: host/port/dev_*; nina: host/port;
// sim/phd2: nothing). `extra` carries backend options (e.g. {managed:true}).
export interface ConnSpec {
  backend: string;
  host?: string | null;
  port?: number | null;
  dev_type?: string | null;
  dev_num?: number | null;
  role?: string | null;
  driver_id?: string | null; // reference to AppConfig.drivers[].id (spec §3.3)
  extra?: Record<string, unknown>;
}

// POST /api/connect/rig body (mirrors server RigSpecBody / devices.backend.RigSpec):
// a `primary` backend that fills every ROLE it can + optional per-role overrides.
export interface RigSpec {
  primary: string;
  roles: Record<string, ConnSpec>;
}

// The tri-state outcome for ONE requested role, as returned in
// /api/connect/rig.results (devices.orchestrator.RoleResult — no live `connected`).
// attempted=false => never tried (unfillable / not requested) => NOT a red LED.
// attempted=true, ok=true => connected. attempted=true, ok=false => failed (error).
export interface RoleResult {
  role: string;
  ok: boolean;
  error: string | null;
  attempted: boolean;
}

// The per-role boot-LED surface (hub.backend_links): a retained RoleResult joined
// with the role's LIVE `connected` state. Present on every `status` poll AND
// `hello`, plus inside ConnectRigResult. `[]` until a RigSpec/profile connect
// happened (the legacy connect_* paths leave it empty).
export interface BackendLink {
  role: string;
  ok: boolean;
  error: string | null;
  attempted: boolean;
  connected: boolean;
}

// POST /api/connect/rig response (hub.connect_rigspec).
export interface ConnectRigResult {
  summary: RigStatus;
  results: RoleResult[];
  backend_links: BackendLink[];
}

// The 16 capability strings (auth/capabilities.py). view.* read; control.* device
// motion/imaging; config.* settings writes; admin.* auth/remote admin.
export type Capability =
  | "view.status"
  | "view.preview"
  | "view.media"
  | "view.site_precise"
  | "view.site_derived"
  | "view.weather"
  | "control.capture"
  | "control.mount"
  | "control.guide"
  | "control.power"
  | "config.safety"
  | "config.solar_override"
  | "config.backend"
  | "config.site_optics"
  | "config.alerts"
  | "admin.users"
  | "system.update";

export type PrincipalRole = "viewer" | "syncer" | "operator" | "admin";

// GET /api/me — auth/principal.Principal.to_public(). FAIL-CLOSED on the server:
// 401 on any resolution failure (never default-admin). Under provider="none"
// every caller resolves to admin + ALL caps, so the default LAN UI is unchanged.
export interface Principal {
  role: PrincipalRole;
  email: string | null;
  caps: Capability[];
}

// Non-secret auth state on the REDACTED GET /api/config (config.redacted()). Only
// present when the server has an `auth` block; *_configured booleans replace the
// scrubbed secrets. `auth` is optional on AppConfig because the WS `hello`
// bootstrap config may omit it.
//
// MULTI-METHOD (W2.3-bis/W2.6): `methods` is the source of truth — a subset of
// {"local","google"}. EMPTY ⇒ OPEN/admin-for-all (today's LAN default; NO login
// screen). `provider` is the LEGACY single-provider field, kept for read-time
// migration only. Both `local` and `google` can be enabled at once. The redacted
// block is a full model_dump of the server AuthConfig with secrets scrubbed, so it
// also carries google_client_id/redirect (non-secret), session_ttl_s, and the
// first-run flag.
export interface AuthState {
  // Source of truth for enabled login methods. [] ⇒ open/admin (no login).
  methods: string[];
  // LEGACY single-provider field (migrate-only; methods wins when non-empty).
  provider: "none" | "google";
  google_configured: boolean;
  admin_token_configured: boolean;
  session_signing_configured: boolean;
  role_allowlist: Record<string, string>; // email -> role
  default_role: string | null;
  // Session lifetime (seconds) for BOTH local + google logins.
  session_ttl_s?: number;
  // First-run create-admin path is allowed while the user store is empty.
  local_enabled_first_run?: boolean;
  // G4: False => a loopback (127.0.0.1) caller no longer auto-resolves to admin
  // under the open/no-method default — it must sign in like any other client.
  // Test-mode knob for verifying operator/viewer gating from this machine.
  // Default true; not a secret, passes through redaction unscrubbed.
  trust_loopback?: boolean;
  // Non-secret google fields pass through redaction (the client_secret is blanked).
  google_client_id?: string;
  google_redirect_uri?: string;
  google_hd?: string;
}

// GET /api/auth/methods — the tiny UNAUTHENTICATED "what login UI do I render?"
// signal (auth/local_routes.py). Read by the Login screen BEFORE any session
// exists. `methods == []` ⇒ no login at all (open LAN). `first_run` is true only
// while local is enabled, the first-run flag is on, AND the user store is empty.
export interface AuthMethods {
  methods: string[]; // subset of {"local","google"}
  google_configured: boolean;
  first_run: boolean;
  // A break-glass admin token is configured → the login screen offers the
  // "access token" field. Optional so older payloads/test doubles omit it.
  admin_token_configured?: boolean;
}

// A local user record as returned by the admin user-management routes
// (User.to_public() — auth/users.py). `password_hash` is structurally ABSENT.
export interface User {
  id: string;
  username: string;
  email: string | null;
  role: PrincipalRole;
  enabled: boolean;
  created: number; // epoch seconds
}

// -------------------------------------------------------- multi-night sessions
// Mirrors server sequence/session.py (sessions spec §2/§6).
export interface SessionFrame {
  id: string;
  ts: number;
  night: string;                       // report_id captured under
  target_id: string;
  step_id: string;
  path?: string;                       // ABSENT for principals w/o config.backend (§8)
  thumb: string | null;
  metrics: Record<string, number>;     // hfr, stars, guide_rms, sensor_temp_c, ...
  auto_accepted: boolean;
  override: "accept" | "reject" | null;
}

export interface Session {
  id: string;
  schema_version: number;
  name: string;
  created_ts: number;
  updated_ts: number;
  status: "active" | "dormant" | "complete" | "abandoned";
  plan: SequencePlan;                  // frozen snapshot WITH ids
  nights: string[];
  frames: SessionFrame[];
  auto_resume: boolean;
}

export interface SessionRow {
  id: string;
  name: string;
  status: "active" | "dormant" | "complete" | "abandoned";
  created_ts: number;
  updated_ts: number;
  nights: number;
  accepted: number;
  total: number;
  auto_resume: boolean;
}

export interface PlanRow {
  id: string;
  name: string;
  frames: number;
  integration_min: number;
  targets: number;
  mtime: number;
}

// ------------------------------------------------------------- touch ergonomics
export type SlewRateId = "pulse" | "fine" | "set";

export interface SlewRateOption {
  id: SlewRateId;
  label: string;
  rateDegS: number;
  pulseMs?: number;
}

export interface MountCaps {
  max_rate_deg_s?: number;
}

export interface TouchSettings {
  hapticsEnabled: boolean;
  touchSizing: "auto" | "on" | "off";
  reverseRa: boolean;
  reverseDec: boolean;
  autoLockMs: null | 180000 | 300000;
}

// ===================================================================== weather
// Sub-project C (weather spec §7): the GET /api/weather + WS `weather` event
// payload. ALL weather data is view.weather-gated server-side (2026-07-17
// decisions wave I2 -- split off view.site_precise so operators see it too;
// WS events are DROPPED for non-holders), so these only ever populate for
// holders (operator + admin). site_lat/site_lon are the one deliberate
// coordinate exception (the radar map needs them to center its tiles) --
// every OTHER coordinate-bearing surface stays view.site_precise-gated
// (admin only), unchanged.
export interface WeatherForecast {
  times: string[];        // ISO-8601 Z, 15-min grid, <= 192 samples (48 h)
  cloud: number[];        // TOTAL cloud cover % — the breach metric
  cloud_low: number[];
  cloud_mid: number[];
  cloud_high: number[];
}

export interface WeatherAstrospheric {
  times: string[];        // ISO-8601 Z, hourly (81 h horizon)
  seeing: (number | null)[];
  transparency: (number | null)[];
  fetched_ts: number | null;
  stale: boolean;         // > 12 h old (two 6-hourly model cycles)
  credits_used_today: number | null;  // 5 credits/call on a 100/day Pro budget
}

export interface WeatherAlert {
  kind: "high_cloud";
  start_iso: string;
  end_iso: string;
  peak_pct: number;
  dominant_layer: "low" | "mid" | "high";
}

export interface WeatherState {
  enabled: boolean;
  fetched_ts: number | null;   // Open-Meteo fetch time (unix s)
  stale: boolean;              // client re-derives FAIL-CLOSED (> 45 min)
  ignore_tonight: boolean;
  threshold_pct: number;
  sustain_minutes: number;
  // Site fix for the radar map (RadarMap.tsx) to center on -- null on the
  // default (0,0) site. The one deliberate exception to "coordinates are
  // view.site_precise-gated everywhere else" (see the module comment above).
  site_lat: number | null;
  site_lon: number | null;
  forecast: WeatherForecast | null;
  astrospheric: WeatherAstrospheric | null;
  alert: WeatherAlert | null;
}

// ============================================================================
// IMAGE GALLERY (gallery design 2026-08-03). Mirrors the ten /api/gallery/*
// routes 1:1. Read the route docstrings in server/astrodeck/api/app.py before
// changing a field name here — these are the wire shapes, not a view model.
// ============================================================================

/** One row of the capture library. `night` is the NOON-TO-NOON observing night
 *  the frame belongs to, derived server-side from `ts` (its DATE-OBS, else its
 *  mtime) — NOT parsed out of the filename. The default naming template writes
 *  the calendar date, so a 00:10 frame's name says "today" while it belongs to
 *  the night that opened yesterday afternoon; `ts` is returned precisely so the
 *  UI can show what decided that when the two disagree. */
export interface GalleryFrame {
  /** library-relative, forward-slashed: "M42/Light_M42_L_…_0001.fits". The
   *  identity every other gallery route takes. */
  path: string;
  name: string;
  /** target directory, "" for a frame sitting at the library root. */
  folder: string;
  night: string;                 // "YYYY-MM-DD" night key
  /** Capture instant, unix seconds — what the server sorted and derived the
   *  night from. NOT for display: rendering it here formats it in the VIEWER's
   *  timezone, which is the bug `local_date`/`local_clock` exist to close. */
  ts: number;
  /** `ts` in the OBSERVATORY's timezone — "YYYY-MM-DD" and "HH:MM".
   *
   *  Sent by the server rather than derived here because `night` was computed
   *  with the RIG's `localtime`, and a browser on the relay is in its own
   *  timezone. Formatting `ts` client-side would compare London's calendar date
   *  against Arizona's night and announce a rollover on every frame in the
   *  library, at a clock time the frame was never taken at. Both dates have to
   *  be the observatory's or the comparison means nothing. */
  local_date: string;
  local_clock: string;
  target: string;                // OBJECT header, falling back to the folder name
  filter: string;                // "" when the header was unreadable
  frame_type: string;            // "Light" | "Flat" | … ; "" when unknown
  exposure_s: number | null;     // null when the header did not say
  bytes: number;
  mtime: number;
}

export interface GalleryFramesPage {
  frames: GalleryFrame[];
  /** The WHOLE filtered set, not this page — so a download's size can be shown
   *  without a second round trip. Same resolver the summary route uses. */
  total: number;
  bytes: number;
  offset: number;
  limit: number;
  /** The walk hit its file ceiling: this is a PREFIX of the library, not the
   *  library. Must be said out loud rather than presented as the whole thing. */
  truncated: boolean;
  scan_ms: number;
}

export interface GalleryNight {
  night: string;
  frames: number;
  bytes: number;
}

export interface GalleryNightsResponse {
  /** Tonight's key by the same noon rollover — so "tonight" is highlightable at
   *  01:00, when the calendar date has already moved on and the night has not. */
  current: string;
  nights: GalleryNight[];
  truncated: boolean;
}

/** A per-path refusal. Never merged into the happy path: a selection that
 *  partly failed must not be able to look like a complete one. */
export interface GalleryFailure {
  path: string;
  reason: string;
}

/** `/api/gallery/summary`. Kept as the route's documented shape even though this
 *  UI never calls it: the frames listing already returns `total`/`bytes` for the
 *  whole filtered set from the same server-side resolver, so asking again would
 *  buy a second library walk for two numbers already on the button. The route is
 *  for callers with no listing — a script pricing a stream before committing to
 *  it. See the note in api/gallery.ts. */
export interface GallerySummary {
  count: number;
  bytes: number;
  failed: GalleryFailure[];
}

export interface TrashedFrame {
  /** TRASH-relative, and possibly suffixed "-1" on a name collision. This — not
   *  `original` — is what restore and purge take. */
  path: string;
  original: string;
  bytes: number;
  deleted_at: number;
}

export interface GalleryTrashResult {
  trashed: TrashedFrame[];
  failed: GalleryFailure[];
  bytes: number;
}

export interface TrashItem {
  path: string;                  // trash-relative (the id for restore/purge)
  original: string;              // where it will go back to
  name: string;
  deleted_at: number;
  expires_at: number;            // auto-purge instant (deleted_at + ttl)
  bytes: number;
  /** false when something already occupies `original` — restore would have to
   *  overwrite a live frame, and it never will. */
  restorable: boolean;
}

export interface GalleryTrashListing {
  items: TrashItem[];
  count: number;
  bytes: number;
  ttl_days: number;
}

export interface GalleryRestoreResult {
  restored: { path: string; restored_to: string }[];
  failed: GalleryFailure[];
}

export interface GalleryPurgeResult {
  purged: number;
  bytes: number;
  failed: GalleryFailure[];
}
