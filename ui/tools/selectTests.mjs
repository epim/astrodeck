// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Pick the UI test files a change can affect (#1258).
//
//   node --import tsx run-tests.mjs --changed <base>
//
// The runner calls this module; nothing here runs on import.
//
// THE RULE. Build the import graph of ui/src from relative imports (the only
// style the tree uses). A changed file selects every test file whose import
// closure contains it, and every changed test file. Two things an import graph
// cannot see are handled by running more, never less:
//
//   * A test file that reads another file from disk (a source file, a
//     stylesheet, a document) depends on data, not on an import. Such a file is
//     found MECHANICALLY (readsFromDisk below: an fs call, or a path built from
//     import.meta.url / __dirname / process.cwd()) and it always runs, until it
//     declares what it reads with
//
//         // disk-reads: ui/src/App.tsx, ui/src/components/
//
//     (repo-root-relative paths, comma separated, a trailing "/" meaning every
//     file under that directory; the line may repeat). A declaring file runs
//     when a changed or deleted file is one of the paths it names, or by import
//     like any other. A declaration is a promise the test file's author keeps; the
//     ratchet in selectTests.test.ts fails when a new undeclared reader
//     appears, and when a declared path does not exist.
//   * Anything the graph cannot account for runs everything: see failOpen.
//
// The tool uses the Node standard library only.

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, posix } from "node:path";

const UI = "ui";
const SRC = `${UI}/src`;
const CODE_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs"];
const RESOLVE_SUFFIXES = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".css"];
const INDEX_FILES = ["index.ts", "index.tsx", "index.js"];

/** True for a path the runner would run as a test (see findTests in run-tests.mjs). */
export function isTestFile(path) {
  return path.endsWith(".test.ts") || path.endsWith(".test.tsx");
}

// Words after which a "/" starts a regular expression and not a division.
const REGEX_KEYWORDS = new Set([
  "return", "typeof", "case", "delete", "void", "throw", "in", "of", "new",
  "else", "do", "instanceof", "await", "yield",
]);
// Characters after which a "/" starts a regular expression. "<" and ">" are
// left out on purpose: in a .tsx file "</div>" would otherwise open one.
const REGEX_AFTER = new Set([
  "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", ";", "+", "-", "*", "%", "~", "^",
]);
const WORD_CHAR = /[A-Za-z0-9_$]/;

/** The source with every comment replaced by spaces (newlines kept), so a word
 *  that appears only in a comment is not read as code. Strings, template
 *  literals and regular expression literals are skipped over, not stripped. A
 *  string or regex literal is closed at the end of its line, which bounds the
 *  damage when JSX text holds an apostrophe. */
export function stripComments(src) {
  const out = src.split("");
  const n = src.length;
  const blank = (from, to) => {
    for (let k = from; k < to; k++) if (out[k] !== "\n" && out[k] !== "\r") out[k] = " ";
  };
  const templateBraces = [];
  let braces = 0;
  let inTemplate = false;
  let prevSig = "";
  let prevWord = "";
  let i = 0;
  while (i < n) {
    const c = src[i];
    if (inTemplate) {
      if (c === "\\") { i += 2; continue; }
      if (c === "`") { inTemplate = false; prevSig = "`"; i++; continue; }
      if (c === "$" && src[i + 1] === "{") {
        templateBraces.push(braces);
        braces++;
        inTemplate = false;
        prevSig = "{";
        i += 2;
        continue;
      }
      i++;
      continue;
    }
    if (c === " " || c === "\t" || c === "\n" || c === "\r") { i++; continue; }
    if (c === "/" && src[i + 1] === "/") {
      let end = i;
      while (end < n && src[end] !== "\n") end++;
      blank(i, end);
      i = end;
      continue;
    }
    if (c === "/" && src[i + 1] === "*") {
      const close = src.indexOf("*/", i + 2);
      const end = close < 0 ? n : close + 2;
      blank(i, end);
      i = end;
      continue;
    }
    if (c === "\"" || c === "'") {
      i++;
      while (i < n && src[i] !== c && src[i] !== "\n") i += src[i] === "\\" ? 2 : 1;
      i++;
      prevSig = c;
      prevWord = "";
      continue;
    }
    if (c === "`") { inTemplate = true; i++; continue; }
    if (c === "/" && (prevSig === "" || REGEX_AFTER.has(prevSig)
        || (WORD_CHAR.test(prevSig) && REGEX_KEYWORDS.has(prevWord)))) {
      let inClass = false;
      i++;
      while (i < n && src[i] !== "\n" && (inClass || src[i] !== "/")) {
        if (src[i] === "\\") i++;
        else if (src[i] === "[") inClass = true;
        else if (src[i] === "]") inClass = false;
        i++;
      }
      i++;
      prevSig = "/";
      prevWord = "";
      continue;
    }
    if (c === "{") { braces++; prevSig = c; prevWord = ""; i++; continue; }
    if (c === "}") {
      braces--;
      if (templateBraces.length > 0 && templateBraces[templateBraces.length - 1] === braces) {
        templateBraces.pop();
        inTemplate = true;
        i++;
        continue;
      }
      prevSig = c;
      prevWord = "";
      i++;
      continue;
    }
    if (WORD_CHAR.test(c)) {
      let end = i;
      while (end < n && WORD_CHAR.test(src[end])) end++;
      prevWord = src.slice(i, end);
      prevSig = src[end - 1];
      i = end;
      continue;
    }
    prevSig = c;
    prevWord = "";
    i++;
  }
  return out.join("");
}

const STATIC_IMPORT = /\b(?:import|export)\b[^"'`;]*?\bfrom\s*["']([^"']+)["']/g;
const SIDE_EFFECT_IMPORT = /\bimport\s*["']([^"']+)["']/g;
const CALLED_IMPORT = /\b(?:import|require)\s*\(\s*["']([^"']+)["']\s*\)/g;

/** Every relative module specifier a file names, comments excluded. */
export function relativeSpecifiers(stripped) {
  const found = new Set();
  for (const re of [STATIC_IMPORT, SIDE_EFFECT_IMPORT, CALLED_IMPORT]) {
    for (const m of stripped.matchAll(re)) {
      if (m[1] === "." || m[1] === ".." || m[1].startsWith("./") || m[1].startsWith("../")) {
        found.add(m[1]);
      }
    }
  }
  return [...found];
}

const FS_CALL = new RegExp(
  "\\b(?:readFileSync|readdirSync|readFile|readdir|statSync|lstatSync|existsSync|"
  + "openSync|opendirSync|realpathSync|createReadStream)\\b",
);
const FS_IMPORT = /(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*)["'](?:node:)?fs(?:\/promises)?["']/;
const BUILT_PATH = /\bimport\.meta\.(?:url|dirname|filename)\b|\b__dirname\b|\b__filename\b|\bprocess\.cwd\s*\(/;

/** The rule that finds a disk reader: the file (comments excluded) calls an
 *  fs function or imports fs, or it builds a path from where it lives or where
 *  the process runs, which can only be to open something outside its imports. */
export function readsFromDisk(stripped) {
  return FS_CALL.test(stripped) || FS_IMPORT.test(stripped) || BUILT_PATH.test(stripped);
}

const DECLARATION = /^[ \t]*\/\/[ \t]*disk-reads:[ \t]*(.+?)[ \t]*\r?$/gm;

/** The paths a file declares it reads (`// disk-reads: a, b/`), or [] when it
 *  declares nothing. Read from the raw text: the declaration IS a comment. */
export function declaredReads(raw) {
  const paths = [];
  for (const m of raw.matchAll(DECLARATION)) {
    for (const p of m[1].split(",")) if (p.trim() !== "") paths.push(p.trim());
  }
  return paths;
}

function listFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "node_modules") listFiles(p, out);
    } else {
      out.push(p);
    }
  }
  return out;
}

const toPosix = (p) => p.split("\\").join("/");

function resolveSpecifier(files, fromFile, spec) {
  const bare = spec.split("?")[0];
  const target = posix.normalize(posix.join(posix.dirname(fromFile), bare));
  const stems = /\.(?:js|jsx|mjs)$/.test(target)
    ? [target, target.replace(/\.(?:js|jsx|mjs)$/, ".ts"), target.replace(/\.(?:js|jsx|mjs)$/, ".tsx")]
    : [target];
  for (const stem of stems) {
    for (const suffix of RESOLVE_SUFFIXES) if (files.has(stem + suffix)) return stem + suffix;
  }
  for (const index of INDEX_FILES) if (files.has(`${target}/${index}`)) return `${target}/${index}`;
  return null;
}

/** The import graph of <root>/ui/src.
 *
 *  Keys are repo-root-relative paths with forward slashes. Returns
 *    files:        every file under ui/src
 *    reverse:      file -> the files that import it
 *    tests:        the test files, sorted
 *    unresolved:   non-test source file -> the specifiers that resolved to no file
 *    diskReaders:  test files that read from disk, themselves or through an import
 *    declared:     test file -> the paths it declares it reads (only those that declare) */
export function buildGraph(root) {
  const srcDir = join(root, SRC);
  const files = new Set(listFiles(srcDir).map((p) => toPosix(p).slice(toPosix(root).length + 1)));
  const reverse = new Map();
  const unresolved = new Map();
  const readers = new Set();
  const declared = new Map();
  for (const file of files) {
    if (!CODE_EXTENSIONS.some((ext) => file.endsWith(ext))) continue;
    const raw = readFileSync(join(root, file), "utf8");
    const stripped = stripComments(raw);
    for (const spec of relativeSpecifiers(stripped)) {
      const target = resolveSpecifier(files, file, spec);
      if (target === null) {
        if (!isTestFile(file)) unresolved.set(file, [...(unresolved.get(file) ?? []), spec]);
      } else {
        if (!reverse.has(target)) reverse.set(target, new Set());
        reverse.get(target).add(file);
      }
    }
    if (readsFromDisk(stripped)) readers.add(file);
    const reads = declaredReads(raw);
    if (reads.length > 0 && isTestFile(file)) declared.set(file, reads);
  }
  const tests = [...files].filter(isTestFile).sort();
  const readerReach = reachedFrom(reverse, readers);
  const diskReaders = new Set(tests.filter((t) => readerReach.has(t)));
  return { files, reverse, tests, unresolved, diskReaders, declared };
}

/** Every file in `starts` and every file that imports one, directly or not. */
function reachedFrom(reverse, starts) {
  const seen = new Set(starts);
  const stack = [...starts];
  while (stack.length > 0) {
    for (const importer of reverse.get(stack.pop()) ?? []) {
      if (!seen.has(importer)) {
        seen.add(importer);
        stack.push(importer);
      }
    }
  }
  return seen;
}

/** Test files that read from disk and declare nothing: the always-run set. */
export function undeclaredReaders(graph) {
  return [...graph.diskReaders].filter((t) => !graph.declared.has(t)).sort();
}

/** Declared paths that name no file or directory in the tree. */
export function missingDeclaredPaths(graph, root) {
  const missing = [];
  for (const [test, paths] of graph.declared) {
    for (const p of paths) if (!existsSync(join(root, p))) missing.push(`${test}: ${p}`);
  }
  return missing.sort();
}

const declares = (paths, changedPath) =>
  paths.some((p) => (p.endsWith("/") ? changedPath.startsWith(p) : changedPath === p));

/** Why the whole suite must run, or null when a selection is safe.
 *
 *  Everything runs when a changed file is under ui/ but not under ui/src (the
 *  package manifest and lock file, every tsconfig, the runner, this tool, the
 *  build config: anything that changes how tests are run or built), when a
 *  source file was deleted (its importers are no longer in the graph to be
 *  found), or when a non-test source file names an import that resolves to no
 *  file (the graph is then missing an edge it cannot name). */
export function failOpen(graph, { changed, deleted }) {
  for (const path of [...changed, ...deleted]) {
    if (path.startsWith(`${UI}/`) && !path.startsWith(`${SRC}/`)) {
      return `${path} changed outside ${SRC}`;
    }
  }
  for (const path of deleted) {
    if (path.startsWith(`${SRC}/`) && !isTestFile(path)) return `${path} was deleted`;
  }
  const first = graph.unresolved.entries().next();
  if (!first.done) return `${first.value[0]} imports ${first.value[1][0]}, which resolves to no file`;
  return null;
}

/** Select the tests a change can affect.
 *
 *  `changed` and `deleted` are repo-root-relative paths. Returns
 *    tests:       the selected test files, sorted
 *    everything:  the reason the whole suite runs, or null
 *    byImport:    selected because a changed file is in the test's closure, or
 *                 because the test file itself changed
 *    byDeclared:  selected because a changed file is one the test declares it reads
 *    byReader:    selected only because it reads from disk and declares nothing */
export function selectTests(graph, { changed, deleted = [] }) {
  const reason = failOpen(graph, { changed, deleted });
  if (reason !== null) {
    return { tests: [...graph.tests], everything: reason, byImport: [], byDeclared: [], byReader: [] };
  }
  const reached = reachedFrom(graph.reverse, changed.filter((p) => graph.files.has(p)));
  const byImport = graph.tests.filter((t) => reached.has(t));
  const taken = new Set(byImport);
  const touched = [...changed, ...deleted];
  const byDeclared = graph.tests.filter((t) => !taken.has(t) && graph.declared.has(t)
    && touched.some((c) => declares(graph.declared.get(t), c)));
  byDeclared.forEach((t) => taken.add(t));
  const byReader = undeclaredReaders(graph).filter((t) => !taken.has(t));
  const tests = [...byImport, ...byDeclared, ...byReader].sort();
  return { tests, everything: null, byImport, byDeclared, byReader };
}

/** The one line the runner prints before it runs a selection. */
export function describeSelection(selection, total) {
  const head = `selected ${selection.tests.length} of ${total} test files`;
  if (selection.everything !== null) return `${head} (everything: ${selection.everything})`;
  return `${head} (${selection.byImport.length} by import, ${selection.byDeclared.length} by declared`
    + ` disk read, ${selection.byReader.length} always-run disk readers)`;
}

function git(root, args) {
  return execFileSync("git", ["-C", root, ...args], {
    encoding: "utf8", maxBuffer: 64 << 20, stdio: ["ignore", "pipe", "pipe"],
  });
}

/** The files that differ from `base`: the commits since the merge base
 *  (`base...HEAD`), then the uncommitted and untracked ones. Renames are
 *  reported as a delete plus an add. Throws when git cannot say. */
export function changedFiles(root, base) {
  const names = (out) => out.split("\0").filter((p) => p !== "");
  const listed = new Set([
    ...names(git(root, ["diff", "--name-only", "--no-renames", "-z", `${base}...HEAD`])),
    ...names(git(root, ["diff", "--name-only", "--no-renames", "-z", "HEAD"])),
    ...names(git(root, ["ls-files", "--others", "--exclude-standard", "-z"])),
  ]);
  const changed = [];
  const deleted = [];
  for (const p of [...listed].sort()) (existsSync(join(root, p)) ? changed : deleted).push(p);
  return { changed, deleted };
}
