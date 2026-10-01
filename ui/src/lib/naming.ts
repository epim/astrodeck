// PRO-11 client mirror of server/astrodeck/naming.py. Advisory PREVIEW only —
// the server render is authoritative for the real path. NAMING_TOKENS/MODE
// are pinned against astrodeck.naming.KNOWN_TOKENS by
// ui/src/lib/__tests__/w5NamingCaptureTokens.test.ts, which parses naming.py
// rather than trusting the two tables to stay in lock-step by comment alone
// (#278: GAIN/EXPOSURE/BINNING/SENSORTEMP existed server-side for a full
// release before anything noticed they were missing here).
export const CAPTURE_EXT = ".fits";
export const DEFAULT_TEMPLATE =
  "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$";
// PANEL is a mosaic panel's 1-based "row-col" label (#189 U-08). It is empty on
// every frame that is not a panel, so it drops out of the path like any unset
// token.
export const NAMING_TOKENS = [
  "TARGET", "FRAMETYPE", "FILTER", "DATE", "TIME", "DATETIME", "NIGHT", "FRAMENR",
  // Capture-settings tokens (#278). Same order as astrodeck.naming.
  // KNOWN_TOKENS. All opt-in, like every other token: a template that doesn't
  // mention one is unchanged, and no value renders empty so it drops out.
  "GAIN", "EXPOSURE", "BINNING", "SENSORTEMP",
  "PANEL",
] as const;

// token name -> sanitize mode. Mirrors astrodeck.naming.KNOWN_TOKENS.
const MODE: Record<string, "loose" | "strict"> = {
  TARGET: "loose", FRAMETYPE: "loose", FILTER: "strict", DATE: "loose",
  TIME: "loose", DATETIME: "loose", NIGHT: "loose", FRAMENR: "loose",
  GAIN: "loose", EXPOSURE: "loose", BINNING: "loose", SENSORTEMP: "loose",
  PANEL: "strict",
};

// Matches $$TOKEN$$ — kept in lock-step with astrodeck.naming._TOKEN_RE.
const TOKEN_RE = /\$\$([A-Z0-9_]+)\$\$/g;

/**
 * One path component, sanitized to match the Python engine byte-for-byte.
 * strict: alnum + -_ , strip underscores (legacy filter). loose: alnum + -_ +
 * space, strip whitespace (legacy target).
 */
export function sanitizeComponent(v: string, mode: "loose" | "strict" = "loose"): string {
  const ok = (c: string) =>
    /[A-Za-z0-9]/.test(c) || (mode === "strict" ? c === "-" || c === "_"
                                                 : c === "-" || c === "_" || c === " ");
  const mapped = [...(v ?? "")].map((c) => (ok(c) ? c : "_")).join("");
  return mode === "strict" ? mapped.replace(/^_+|_+$/g, "") : mapped.trim();
}

/**
 * Substitute $$TOKEN$$ in one underscore-delimited piece. Literal runs are
 * loose-sanitized (neutralizes `..`/`:` injected as literals); each token
 * value is sanitized by its own mode. Unknown tokens render empty. Mirrors
 * astrodeck.naming._sub_piece exactly (walk, not a blind regex replace).
 */
function subPiece(piece: string, fields: Record<string, string>): string {
  const out: string[] = [];
  let pos = 0;
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(piece)) !== null) {
    const lit = piece.slice(pos, m.index);
    if (lit) out.push(sanitizeComponent(lit, "loose"));
    const mode = MODE[m[1]];
    if (mode !== undefined) {
      out.push(sanitizeComponent(fields[m[1]] ?? "", mode));
    }
    pos = m.index + m[0].length;
  }
  const tail = piece.slice(pos);
  if (tail) out.push(sanitizeComponent(tail, "loose"));
  return out.join("");
}

// Sample field values for a naming preview's dry render (never a real
// capture). Mirrors astrodeck.naming._SAMPLE's values exactly (gain=100,
// exposure=300.0 whole seconds, binning=1, sensor_temp=-10.0), and is the
// SINGLE copy both naming editors preview from — two hand-maintained samples
// drifting apart is exactly how #278 happened: GAIN/EXPOSURE/BINNING/
// SENSORTEMP sat in MODE above with no sample value, so even once the token
// is "known" its piece still renders empty (an empty known value and an
// unknown token both contribute nothing — see w5NamingCaptureTokens.test.ts).
// PANEL is deliberately absent: the server's own _SAMPLE has no panel either,
// since a panel label only exists mid-mosaic.
export const PREVIEW_SAMPLE: Record<string, string> = {
  TARGET: "M42", FRAMETYPE: "Light", FILTER: "Ha", DATE: "2026-07-23",
  TIME: "213045", DATETIME: "2026-07-23_213045", NIGHT: "2026-07-23",
  FRAMENR: "0001",
  GAIN: "100", EXPOSURE: "300", BINNING: "1", SENSORTEMP: "-10C",
};

/**
 * Render a template to a preview path string (folders via '/', '.fits'
 * appended). Split -> substitute -> drop-empty -> rejoin — mirrors
 * astrodeck.naming.render_relative_path.
 */
export function renderTemplatePreview(template: string, fields: Record<string, string>): string {
  const segs: string[] = [];
  for (const seg of template.split("/")) {
    const pieces = seg.split("_").map((p) => subPiece(p, fields));
    const joined = pieces.filter((p) => p !== "").join("_");
    if (joined) segs.push(joined);
  }
  if (segs.length === 0) segs.push("capture");
  segs[segs.length - 1] += CAPTURE_EXT;
  return segs.join("/");
}
