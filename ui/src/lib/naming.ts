// PRO-11 client mirror of server/astrodeck/naming.py. Advisory PREVIEW only —
// the server render is authoritative for the real path. Kept in lock-step with
// the Python engine by the shared golden vectors in naming.test.ts.
export const CAPTURE_EXT = ".fits";
export const DEFAULT_TEMPLATE =
  "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$";
export const NAMING_TOKENS = [
  "TARGET", "FRAMETYPE", "FILTER", "DATE", "TIME", "DATETIME", "NIGHT", "FRAMENR",
] as const;

// token name -> sanitize mode. Mirrors astrodeck.naming.KNOWN_TOKENS.
const MODE: Record<string, "loose" | "strict"> = {
  TARGET: "loose", FRAMETYPE: "loose", FILTER: "strict", DATE: "loose",
  TIME: "loose", DATETIME: "loose", NIGHT: "loose", FRAMENR: "loose",
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
