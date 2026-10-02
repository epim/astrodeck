// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5NamingCaptureTokens.test.ts - WP-42 (#278): the client naming mirror
// (naming.ts) must offer every token the server's render_relative_path
// actually substitutes, or the settings preview lies about where a frame
// will land.
//
// #278's measured repro: the SAME template and fields rendered
//   server (render_relative_path):   M42/g100_300s/0001.fits
//   client (renderTemplatePreview):  M42/g_s/0001.fits
// because GAIN/EXPOSURE/BINNING/SENSORTEMP were in astrodeck.naming.
// KNOWN_TOKENS (since UX #48) but never added to naming.ts's NAMING_TOKENS/
// MODE, so the mirror treated them as unknown tokens and dropped them.
//
// This file does two things the comment-only "kept in lock-step" claim at the
// top of naming.ts never did (the issue's "Class" finding):
//   1. PARSES astrodeck/naming.py's KNOWN_TOKENS and checks naming.ts offers
//      the same token set with the same sanitize behaviour (loose/strict),
//      rather than restating the table a third time by hand.
//   2. Reproduces #278's exact golden vector, so a preview that silently
//      drops a known-but-sample-less token cannot pass by merely being
//      "recognised" (see the two mutants in NamingPanel.tsx/naming.ts's
//      commit message for why recognised alone is not enough).
//
// Run directly:  npx tsx src/lib/__tests__/w5NamingCaptureTokens.test.ts
import { readFileSync } from "node:fs";
import {
  CAPTURE_EXT, NAMING_TOKENS, PREVIEW_SAMPLE, renderTemplatePreview, sanitizeComponent,
} from "../naming";

// ---------------------------------------------------------------- harness
// Same tiny inline-assert harness as naming.test.ts / standardsPanel.test.ts
// (no vitest/jest wired into this UI - see naming.test.ts's header).
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ------------------------------------------------------- astrodeck.naming.py
const NAMING_PY_REL = "../../../../server/astrodeck/naming.py";
const pySrc: string = (() => {
  try {
    return readFileSync(new URL(NAMING_PY_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    // A missing naming.py must FAIL, never skip - a skipped cross-check reads
    // as a green run while the only thing pinning the mirror honest is gone.
    throw new Error(`cannot read naming.py at ${NAMING_PY_REL}: ${String(e)}`);
  }
})();

/** KNOWN_TOKENS's `"TOKEN": "mode",` entries, token -> loose|strict, parsed
 *  out of the dict literal rather than hand-copied (the exact thing #278
 *  found missing: nothing compared the two tables). */
function knownTokensFromPython(): Record<string, "loose" | "strict"> {
  const start = pySrc.indexOf("KNOWN_TOKENS: dict[str, str] = {");
  if (start < 0) throw new Error("KNOWN_TOKENS dict not found in naming.py");
  const end = pySrc.indexOf("\n}", start);
  if (end < 0) throw new Error("KNOWN_TOKENS dict has no closing brace");
  const body = pySrc.slice(start, end);
  const out: Record<string, "loose" | "strict"> = {};
  const re = /"([A-Z0-9_]+)":\s*"(loose|strict)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) out[m[1]] = m[2] as "loose" | "strict";
  return out;
}

const SERVER_MODES = knownTokensFromPython();

test("naming.py parse found the block", () => {
  assert(Object.keys(SERVER_MODES).length > 0,
    "no tokens parsed - the parser or KNOWN_TOKENS moved");
});

// ----------------------------------------------------- token-set parity (1)
test("NAMING_TOKENS offers every token the server knows, and no others", () => {
  const client = new Set<string>(NAMING_TOKENS as readonly string[]);
  const server = new Set(Object.keys(SERVER_MODES));
  const missing = [...server].filter((t) => !client.has(t)).sort();
  const extra = [...client].filter((t) => !server.has(t)).sort();
  assert(missing.length === 0,
    `naming.ts NAMING_TOKENS is missing server token(s): ${missing.join(", ")}`);
  assert(extra.length === 0,
    `naming.ts NAMING_TOKENS offers token(s) the server does not know: ${extra.join(", ")}`);
});

// ------------------------------------------------- sanitize-mode parity (2)
// Behavioural, not structural: render each token ALONE with a value that
// loose and strict sanitize differently ("_a b_" keeps its space only under
// loose), so a MODE entry that drifted from the server is caught by what the
// preview actually PRINTS, not by reading naming.ts's private MODE table.
for (const [token, mode] of Object.entries(SERVER_MODES)) {
  test(`$$${token}$$ sanitizes ${mode}, like the server`, () => {
    const rendered = renderTemplatePreview(`$$${token}$$`, { [token]: "_a b_" });
    const expect = sanitizeComponent("_a b_", mode) + CAPTURE_EXT;
    eq(rendered, expect,
      `$$${token}$$ rendered with the wrong sanitize mode (expected ${mode})`);
  });
}

// ------------------------------------------------ PREVIEW_SAMPLE completeness
// The exact other half of #278: a token can be fully "known" (offered as a
// chip, sanitized correctly) and STILL preview empty if the shared sample has
// no value for it, because an empty known-token value and an unknown token
// both contribute nothing to the rendered piece. PANEL is deliberately
// excluded - it has no value in the default-template dry render on the
// server either (naming.py's own _SAMPLE omits it; a panel label only exists
// mid-mosaic).
test("PREVIEW_SAMPLE has a value for every non-PANEL token", () => {
  const missing = (NAMING_TOKENS as readonly string[])
    .filter((t) => t !== "PANEL" && !PREVIEW_SAMPLE[t]);
  assert(missing.length === 0,
    `PREVIEW_SAMPLE has no value for: ${missing.join(", ")} - the preview would `
    + "silently drop that token, exactly #278's bug");
});

// ---------------------------------------------------- golden vector (#278)
// The issue's own measured repro, byte-for-byte: same template, same
// semantics as the server's _SAMPLE (gain=100, exposure=300.0 whole seconds,
// binning=1, sensor_temp=-10.0).
test("GAIN+EXPOSURE golden vector matches the server's measured output (#278)", () => {
  eq(renderTemplatePreview("$$TARGET$$/g$$GAIN$$_$$EXPOSURE$$s/$$FRAMENR$$", PREVIEW_SAMPLE),
     "M42/g100_300s/0001.fits");
});
test("BINNING+SENSORTEMP golden vector", () => {
  eq(renderTemplatePreview("$$TARGET$$_bin$$BINNING$$_$$SENSORTEMP$$/$$FRAMENR$$", PREVIEW_SAMPLE),
     "M42_bin1_-10C/0001.fits");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw5NamingCaptureTokens.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
