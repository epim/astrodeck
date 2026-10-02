// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// noSiteParamsInQueryStrings.test.ts - no URL the UI builds carries a place or
// a pointing (#520).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/noSiteParamsInQueryStrings.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY. `getCloudmapAt` built `/api/cloudmap/at?alt=..&az=..` from the mount's
// alt/az, several times a minute, and the remote relay's access log wrote
// every one down. A pointing at a known time is a function of the site, and at
// park its altitude IS the latitude (#140). The site picker did the same with
// `/api/site/sky?lat=..&lon=..`. A URL is recorded by every hop it crosses -
// the relay, any proxy, the browser's history - so a place or a pointing may
// travel only in a request body, and the telescope's pointing not at all.
//
// HOW. Every source file under ui/src, tests excluded, is parsed with the
// TypeScript compiler and three shapes are looked for:
//
//   * a string or template literal containing `?name=` or `&name=`, or
//     opening with `name=` (a piece later joined with "&");
//   * `new URLSearchParams({ name: ... })` or `new URLSearchParams([["name",
//     ...]])`;
//   * `.set("name", ...)` / `.append("name", ...)` on a variable made by
//     `new URLSearchParams(...)` in the same file, or on any `.searchParams`.
//
// where `name` is EXACTLY alt, az, lat, lon or site. Exactly, because
// `alt_step=` and `az_step=` are the dome's grid and carry no place, and a
// scan that flagged them would be switched off within a week. Comments are
// not literals, so a comment recording the old URL does not trip it.
//
// MUTATION RECORD, 2026-09-29, each mutant in a private scratch copy of ui/
// (scratchpad/H4-PRIV-mut in the session scratchpad, never the shared tree,
// #254), from a byte backup restored with its sha256 checked. Output verbatim.
//
//   MUTANT "restore the cloudmap/at template" (api/cloudmap.ts's
//   getCloudmapAt back to `(alt, az, aheadS)` and the alt/az template).
//   Observed ("noSiteParamsInQueryStrings.test: 2/4 passed"):
//     x no URL built anywhere in ui/src carries alt, az, lat, lon or site: [...]
//     expected []
//     got      ["api/cloudmap.ts line 151: \"alt=\" in a URL literal","api/cloudmap.ts line 151: \"az=\" in a URL literal"]
//     x the literal reader sees the real tree's URLs: the dome's grid template is read and NOT flagged: api/cloudmap.ts
//
//   MUTANT "restore SitePanel's ?lat=&lon=" (components/settings/SitePanel.tsx
//   back to `.get(`/api/site/sky?lat=${latSigned}&lon=${lonSigned}`)`).
//   Observed ("3/4 passed"):
//     x no URL built anywhere in ui/src carries alt, az, lat, lon or site: [...]
//     got      ["components/settings/SitePanel.tsx line 224: \"lat=\" in a URL literal","components/settings/SitePanel.tsx line 224: \"lon=\" in a URL literal"]
//
//   MUTANT 'params.set("lat", ...)' (lib/settingsNavigation.ts given
//   `params.set("lat", "0");` beside its `params.set("panel", panel)`).
//   Observed ("3/4 passed"):
//     x no URL built anywhere in ui/src carries alt, az, lat, lon or site: [...]
//     got      ["lib/settingsNavigation.ts line 20: .set(\"lat\", ...)"]
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

import ts from "typescript";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ================================================================= the rule

/** The names, restated here rather than imported: a scan that read the list
 *  from the code it grades would pass the day a name came off it. */
const NAMES = ["alt", "az", "lat", "lon", "site"] as const;
const NAME_SET = new Set<string>(NAMES);

/** `?alt=`, `&az=`, or a piece that opens with `lat=`. The name is bounded on
 *  both sides, so `alt_step=`, `min_alt=` and `latitude=` do not match. */
const IN_LITERAL = new RegExp(`(?:^|[?&])(${NAMES.join("|")})=`, "g");

function namesInText(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(IN_LITERAL)) out.push(m[1]);
  return out;
}

/** The literal text a node carries, or null when it is not a literal. A
 *  template is read piece by piece: `?lat=${a}&lon=${b}` is the pieces
 *  `?lat=` and `&lon=`, and each is a piece of a URL on its own. */
function literalPieces(node: ts.Node): string[] | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return [node.text];
  if (ts.isTemplateExpression(node)) {
    return [node.head.text, ...node.templateSpans.map((s) => s.literal.text)];
  }
  return null;
}

function keyName(name: ts.PropertyName): string | null {
  if (ts.isIdentifier(name) || ts.isStringLiteral(name)) return name.text;
  return null;
}

const isNewSearchParams = (n: ts.Node): n is ts.NewExpression =>
  ts.isNewExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === "URLSearchParams";

/** Every place a URL in ``source`` would carry one of NAMES, as
 *  "line N: <how>" strings. */
function scanSource(source: string, fileName = "x.ts"): string[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const hits: string[] = [];
  const line = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;

  // Variables this file makes with `new URLSearchParams(...)`, by name. A
  // `.set("site", ...)` on anything else - a Map, a store - is not a URL.
  const searchParamVars = new Set<string>();
  const collect = (n: ts.Node): void => {
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer
        && isNewSearchParams(n.initializer)) {
      searchParamVars.add(n.name.text);
    }
    ts.forEachChild(n, collect);
  };
  collect(sf);

  const visit = (n: ts.Node): void => {
    const pieces = literalPieces(n);
    if (pieces) {
      for (const p of pieces) for (const name of namesInText(p)) {
        hits.push(`line ${line(n)}: "${name}=" in a URL literal`);
      }
    }
    if (isNewSearchParams(n) && n.arguments && n.arguments.length > 0) {
      const arg = n.arguments[0];
      if (ts.isObjectLiteralExpression(arg)) {
        for (const prop of arg.properties) {
          const key = (ts.isPropertyAssignment(prop) || ts.isShorthandPropertyAssignment(prop))
            ? keyName(prop.name) : null;
          if (key && NAME_SET.has(key)) hits.push(`line ${line(prop)}: URLSearchParams key "${key}"`);
        }
      } else if (ts.isArrayLiteralExpression(arg)) {
        for (const el of arg.elements) {
          if (ts.isArrayLiteralExpression(el) && el.elements[0] && ts.isStringLiteral(el.elements[0])
              && NAME_SET.has(el.elements[0].text)) {
            hits.push(`line ${line(el)}: URLSearchParams pair "${el.elements[0].text}"`);
          }
        }
      }
    }
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)
        && (n.expression.name.text === "set" || n.expression.name.text === "append")
        && n.arguments[0] && ts.isStringLiteral(n.arguments[0])
        && NAME_SET.has(n.arguments[0].text)) {
      const recv = n.expression.expression;
      const isParams = (ts.isIdentifier(recv) && searchParamVars.has(recv.text))
        || (ts.isPropertyAccessExpression(recv) && recv.name.text === "searchParams");
      if (isParams) {
        hits.push(`line ${line(n)}: .${n.expression.name.text}("${n.arguments[0].text}", ...)`);
      }
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return hits;
}

// ================================================================= the walk

const SRC = fileURLToPath(new URL("../", import.meta.url)).replace(/[\\/]+$/, "");
const CODE_EXT = /\.(ts|tsx)$/;
const isTest = (rel: string): boolean =>
  /(^|\/)(__tests__|__fixtures__)(\/|$)/.test(rel) || /\.test\.(ts|tsx)$/.test(rel);

function walk(dir: string, rel: string, out: string[]): void {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const abs = `${dir}/${name}`;
    const r = rel ? `${rel}/${name}` : name;
    if (statSync(abs).isDirectory()) walk(abs, r, out);
    else if (CODE_EXT.test(name)) out.push(r);
  }
}
const ALL: string[] = [];
walk(SRC, "", ALL);
const FILES = ALL.filter((r) => !isTest(r));

function scanTree(): string[] {
  const out: string[] = [];
  for (const r of FILES) {
    for (const h of scanSource(readFileSync(`${SRC}/${r}`, "utf8"), r)) out.push(`${r} ${h}`);
  }
  return out;
}

// ==================================================================== cases

test("no URL built anywhere in ui/src carries alt, az, lat, lon or site", () => {
  eq(scanTree(), [],
    "these build a URL carrying a place or a pointing, which every log on the way writes down "
    + "(#520): read the pointing server-side, or send the value in a POST body");
});

test("the walk read ui/src's sources, none of its tests, and the files the leak lived in", () => {
  assert(FILES.length > 300, `the walk read ${FILES.length} source files under ${SRC}: it is not reading ui/src`);
  for (const f of ["api/cloudmap.ts", "components/settings/SitePanel.tsx",
                   "next/hubs/sky/sheets/sites.tsx", "lib/settingsNavigation.ts"]) {
    assert(FILES.includes(f), `the walk did not read ${f}`);
  }
  assert(ALL.includes("__tests__/noSiteParamsInQueryStrings.test.ts")
         && !FILES.includes("__tests__/noSiteParamsInQueryStrings.test.ts"),
    "this file, which quotes every forbidden URL, was not walked and then excluded");
});

test("the literal reader sees the real tree's URLs: the dome's grid template is read and NOT flagged", () => {
  // The control on the real tree. getCloudmapDome's URL carries alt_step and
  // az_step; if the scan could not see template pieces at all, the first case
  // would pass on nothing.
  const src = readFileSync(`${SRC}/api/cloudmap.ts`, "utf8");
  assert(/alt_step=\$\{/.test(src), "api/cloudmap.ts no longer builds the dome URL this control reads");
  eq(scanSource(src, "api/cloudmap.ts"), [], "api/cloudmap.ts");
  eq(scanSource("const u = `/api/cloudmap/dome?alt_step=${a}&az_step=${b}`;"), [],
    "alt_step= and az_step= are not alt= and az=");
});

test("each shape is caught, and each near-miss is left alone", () => {
  const caught: Array<[string, number]> = [
    ["api.get(`/api/cloudmap/at?alt=${a}&az=${b}&ahead_s=0`);", 2],
    ['api.get("/api/site/sky?lat=" + a + "&lon=" + b);', 2],
    ['const parts = ["site=" + s];', 1],
    ['const params = new URLSearchParams(); params.set("lat", "1");', 1],
    ['const p = new URLSearchParams(); p.append("az", String(z));', 1],
    ["const q = new URLSearchParams({ site: s, fov: f });", 1],
    ["const q = new URLSearchParams({ lon });", 1],
    ['const q = new URLSearchParams([["alt", "1"]]);', 1],
    ['url.searchParams.set("site", s);', 1],
  ];
  for (const [src, n] of caught) eq(scanSource(src).length, n, `not caught: ${src}`);
  const leftAlone = [
    "const u = `/api/cloudmap/at?ahead_s=${s}`;",
    'const u = "/api/catalog/tonight?alt_limit=20&min_alt_deg=10";',
    'const u = "/x?latitude=1&longitude=2&sitemap=3&altitude=4";',
    "// the old form was /api/cloudmap/at?alt=1&az=2",
    "/* GET /api/site/sky?lat=1&lon=2 */",
    'const m = new Map(); m.set("site", 1);',
    'api.post("/api/site/sky", { lat: 1, lon: 2 });',
    'api.post("/api/cloudmap/at", { alt, az, ahead_s: 0 });',
  ];
  for (const src of leftAlone) eq(scanSource(src), [], `flagged a near-miss: ${src}`);
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`noSiteParamsInQueryStrings.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
