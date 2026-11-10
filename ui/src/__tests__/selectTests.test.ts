// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// selectTests.test.ts - the UI test selector (#1258): which test files a change
// reaches, and when it must give up and run everything.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/__tests__/selectTests.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. `run-tests.mjs --changed <base>` runs the test files whose
// import closure contains a changed file. A selector that picks too little lets
// a broken change merge green, so every way it can pick too little is pinned
// here on a made-up tree (a temporary directory laid out like ui/src):
//
//   * a changed file reaches the tests that import it directly, through other
//     files, through a directory index and through a ".js" specifier that names
//     a ".ts" file; a changed test file runs;
//   * a test that reads from disk is found mechanically and always runs, unless
//     it declares what it reads, in which case it runs when one of those paths
//     changes; a word in a comment does not make a reader;
//   * anything the graph cannot account for runs everything, and says why:
//     a file under ui/ outside ui/src (package.json, a tsconfig, the runner,
//     this tool), a deleted source file, a non-test source file that imports
//     something that does not resolve;
//   * the scanner reads what TypeScript reads: no import in a comment, strings
//     that hold "//", template literals, regular expressions with quotes;
//   * the changed files come from `base...HEAD`, the uncommitted files and the
//     untracked ones, with a rename read as a delete and an add.
//
// NAMED MUTANT (applied to ui/tools/selectTests.mjs from a byte backup and
// restored): "the fail-open list is emptied" (`failOpen`'s first loop made to
// skip every path). Observed red: "a change to ui/package.json runs everything".
//
// disk-reads: ui/tools/selectTests.mjs

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

interface Graph {
  files: Set<string>;
  tests: string[];
  unresolved: Map<string, string[]>;
  diskReaders: Set<string>;
  declared: Map<string, string[]>;
}
interface Selection {
  tests: string[];
  everything: string | null;
  byImport: string[];
  byDeclared: string[];
  byReader: string[];
}
interface Change { changed: string[]; deleted?: string[] }
interface SelectModule {
  stripComments(src: string): string;
  relativeSpecifiers(stripped: string): string[];
  readsFromDisk(stripped: string): boolean;
  declaredReads(raw: string): string[];
  buildGraph(root: string): Graph;
  undeclaredReaders(graph: Graph): string[];
  missingDeclaredPaths(graph: Graph, root: string): string[];
  selectTests(graph: Graph, change: Change): Selection;
  describeSelection(selection: Selection, total: number): string;
  changedFiles(root: string, base: string): { changed: string[]; deleted: string[] };
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const TOOL_URL = new URL("../../tools/selectTests.mjs", import.meta.url).href;
const {
  stripComments, relativeSpecifiers, readsFromDisk, declaredReads, buildGraph, undeclaredReaders,
  missingDeclaredPaths, selectTests, describeSelection, changedFiles,
} = (await nodeImport(TOOL_URL)) as SelectModule;

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const same = (got: string[], want: string[], what: string): void =>
  assert(JSON.stringify([...got].sort()) === JSON.stringify([...want].sort()),
    `${what}: wanted ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);

// ------------------------------------------------------------ the made-up tree
const T = (name: string): string => `ui/src/__tests__/${name}.test.ts`;
const TREE: Record<string, string> = {
  "ui/src/lib/a.ts": "export const a = 1;\n",
  "ui/src/lib/b.ts": "import { a } from \"./a\";\nexport const b = a + 1;\n",
  "ui/src/lib/z.ts": "export const z = 0;\n",
  "ui/src/components/C.tsx": "import { b } from \"../lib/b\";\nexport const C = b;\n",
  "ui/src/widgets/index.ts": "export { w } from \"./w\";\n",
  "ui/src/widgets/w.ts": "export const w = 3;\n",
  "ui/src/helpers/read.ts": "import { readFileSync } from \"node:fs\";\nexport const read = (p: string) => readFileSync(p, \"utf8\");\n",
  "ui/src/data/table.json": "{}\n",
  "ui/src/data/other.json": "{}\n",
  [T("viaChain")]: "import { C } from \"../components/C\";\nexport const r = C;\n",
  [T("direct")]: "import { a } from \"../lib/a.js\";\nexport const r = a;\n",
  [T("unrelated")]: "import { z } from \"../lib/z\";\nexport const r = z;\n",
  [T("viaIndex")]: "import { w } from \"../widgets\";\nexport const r = w;\n",
  [T("reader")]: "import { readFileSync } from \"node:fs\";\nexport const r = readFileSync;\n",
  [T("viaHelper")]: "import { read } from \"../helpers/read\";\nexport const r = read;\n",
  [T("commentOnly")]: "// this used to call readFileSync and readdirSync\nexport const r = 1;\n",
  [T("declaresFile")]: [
    "// disk-reads: ui/src/data/table.json",
    "import { readFileSync } from \"node:fs\";",
    "export const r = readFileSync;",
    "",
  ].join("\n"),
  [T("declaresDir")]: [
    "// disk-reads: ui/src/lib/, server/notes.md",
    "export const r = process.cwd();",
    "",
  ].join("\n"),
};

const made: string[] = [];
function makeTree(extra: Record<string, string> = {}, skip: string[] = []): string {
  const root = mkdtempSync(join(tmpdir(), "astrodeck-select-")).split("\\").join("/");
  made.push(root);
  for (const [rel, text] of Object.entries({ ...TREE, ...extra })) {
    if (skip.includes(rel)) continue;
    mkdirSync(dirname(join(root, rel)), { recursive: true });
    writeFileSync(join(root, rel), text, "utf8");
  }
  return root;
}

try {
  const root = makeTree();
  const graph = buildGraph(root);
  const READERS = [T("reader"), T("viaHelper"), T("declaresFile"), T("declaresDir")];
  const ALWAYS = [T("reader"), T("viaHelper")];

  test("the tree is read as laid out: every test file found, readers found mechanically", () => {
    assert(graph.tests.length === 9, `want 9 test files, got ${graph.tests.length}`);
    same([...graph.diskReaders], READERS, "disk readers");
    same(undeclaredReaders(graph), ALWAYS, "undeclared readers");
    assert(graph.unresolved.size === 0, `nothing should be unresolved: ${[...graph.unresolved.keys()]}`);
  });

  test("a changed source file reaches the tests that import it directly, through a chain, and by a .js specifier", () => {
    const s = selectTests(graph, { changed: ["ui/src/lib/a.ts"] });
    assert(s.everything === null, `a source change must not run everything: ${s.everything}`);
    same(s.byImport, [T("viaChain"), T("direct")], "selected by import");
    assert(!s.tests.includes(T("unrelated")), "a test that does not reach the file must not run");
  });

  test("a directory index is followed", () => {
    same(selectTests(graph, { changed: ["ui/src/widgets/w.ts"] }).byImport, [T("viaIndex")], "selected by import");
  });

  test("a changed test file runs, and nothing else beyond the always-run readers", () => {
    const s = selectTests(graph, { changed: [T("unrelated")] });
    same(s.tests, [T("unrelated"), ...ALWAYS], "selected");
  });

  test("a reader with no declaration always runs, even when only a file outside ui changed", () => {
    const s = selectTests(graph, { changed: ["server/astrodeck/hub.py"] });
    same(s.tests, ALWAYS, "selected");
    same(s.byReader, ALWAYS, "always-run readers");
  });

  test("a declared reader runs when a path it declares changes, and not otherwise", () => {
    const hit = selectTests(graph, { changed: ["ui/src/data/table.json"] });
    same(hit.byDeclared, [T("declaresFile")], "declared file changed");
    const miss = selectTests(graph, { changed: ["ui/src/data/other.json"] });
    assert(!miss.tests.includes(T("declaresFile")), "an undeclared file must not select the declaring test");
  });

  test("a declared directory (trailing slash) matches any file under it, and a path outside ui counts", () => {
    same(selectTests(graph, { changed: ["ui/src/lib/z.ts"] }).byDeclared, [T("declaresDir")], "directory");
    same(selectTests(graph, { changed: ["server/notes.md"] }).byDeclared, [T("declaresDir")], "outside ui");
    assert(!selectTests(graph, { changed: ["ui/src/libx/z.ts"] }).tests.includes(T("declaresDir")),
      "a directory declaration must not match a longer sibling name");
  });

  test("a word in a comment is not a disk read", () => {
    assert(!graph.diskReaders.has(T("commentOnly")), "commentOnly only mentions readFileSync in a comment");
  });

  test("the declaration is read from the file: comma separated, repeatable", () => {
    same(declaredReads("// disk-reads: a, b/\n// disk-reads: c\nconst x = 1;\n"), ["a", "b/", "c"], "paths");
    same(declaredReads("const s = 1; // disk-reads: not-a-declaration\n"), [], "a trailing comment is not one");
    same(declaredReads("x\r\n// disk-reads: a\r\n"), ["a"], "CRLF");
  });

  test("every file that changes the way tests run or are built runs everything, and says which file", () => {
    for (const path of [
      "ui/package.json", "ui/package-lock.json", "ui/tsconfig.json", "ui/tsconfig.lib.json",
      "ui/run-tests.mjs", "ui/tools/selectTests.mjs", "ui/vite.config.ts", "ui/public/x.svg",
    ]) {
      const s = selectTests(graph, { changed: [path] });
      assert(s.everything !== null && s.everything.includes(path),
        `a change to ${path} must run everything and name it, got ${JSON.stringify(s.everything)}`);
      same(s.tests, graph.tests, `${path} selection`);
    }
  });

  test("a change to ui/package.json runs everything", () => {
    const s = selectTests(graph, { changed: ["ui/package.json"] });
    assert(s.everything !== null, "ui/package.json changed and nothing was selected as a reason to run everything");
    assert(s.tests.length === graph.tests.length,
      `ui/package.json must select all ${graph.tests.length} files, got ${s.tests.length}`);
  });

  test("a deleted source file runs everything; a deleted test file does not", () => {
    const gone = selectTests(graph, { changed: [], deleted: ["ui/src/lib/old.ts"] });
    assert(gone.everything !== null && gone.everything.includes("ui/src/lib/old.ts"),
      `a deleted source file must run everything, got ${JSON.stringify(gone.everything)}`);
    const goneTest = selectTests(graph, { changed: [], deleted: [T("old")] });
    assert(goneTest.everything === null, "a deleted test file reaches nothing and must not run everything");
  });

  test("a non-test source file that imports something unresolvable runs everything; a test file's string does not", () => {
    const broken = buildGraph(makeTree({ "ui/src/lib/c.ts": "import { q } from \"./nowhere\";\nexport const c = q;\n" }));
    const s = selectTests(broken, { changed: ["ui/src/lib/a.ts"] });
    assert(s.everything !== null && s.everything.includes("ui/src/lib/c.ts") && s.everything.includes("./nowhere"),
      `an unresolved import must run everything and name it, got ${JSON.stringify(s.everything)}`);
    const quoted = buildGraph(makeTree({
      [T("fixtureString")]: "export const text = 'import { q } from \"./nowhere\"';\n",
    }));
    assert(quoted.unresolved.size === 0, "an import that appears in a string inside a test file is not a source import");
    assert(selectTests(quoted, { changed: ["ui/src/lib/a.ts"] }).everything === null,
      "a test file's unresolved string must not run everything");
  });

  test("the line that explains a selection carries the counts and the reason", () => {
    const s = selectTests(graph, { changed: ["ui/src/lib/a.ts", "ui/src/data/table.json"] });
    assert(describeSelection(s, 10) === "selected 6 of 10 test files (2 by import, 2 by declared disk read, 2 always-run disk readers)",
      `got ${describeSelection(s, 10)}`);
    const all = selectTests(graph, { changed: ["ui/package.json"] });
    assert(describeSelection(all, 9) === "selected 9 of 9 test files (everything: ui/package.json changed outside ui/src)",
      `got ${describeSelection(all, 9)}`);
  });

  // ------------------------------------------------------- the scanner
  test("an import named only in a comment is not an import", () => {
    const src = [
      "// import { x } from \"./line\";",
      "/* import { y } from \"./block\"; */",
      "/** a doc: from \"./doc\" */",
      "import { real } from \"./real\";",
    ].join("\n");
    same(relativeSpecifiers(stripComments(src)), ["./real"], "specifiers");
  });

  test("every import form is found: type, side effect, re-export, multi-line, dynamic, require", () => {
    const src = [
      "import type { T } from \"./type\";",
      "import \"./effect.css\";",
      "export * from \"./star\";",
      "export { a } from \"./named\";",
      "import {",
      "  one,",
      "  two,",
      "} from \"../multi\";",
      "const m = await import(\"./dynamic\");",
      "const r = require(\"./required\");",
      "import react from \"react\";",
    ].join("\n");
    same(relativeSpecifiers(stripComments(src)),
      ["./type", "./effect.css", "./star", "./named", "../multi", "./dynamic", "./required"], "specifiers");
  });

  test("strings that hold slashes, template literals and regular expressions do not hide the imports after them", () => {
    const src = [
      "const url = \"http://local/\";",
      "const t = `a ${ \"//\" + `${1}` } // not a comment`;",
      "const re = /[\"'] \\/ \\/\\//g;",
      "const half = 4 / 2 / 1;",
      "import { after } from \"./after\";",
      "const tail = () => import(\"./tail\");",
    ].join("\n");
    same(relativeSpecifiers(stripComments(src)), ["./after", "./tail"], "specifiers");
  });

  test("an apostrophe in JSX text does not swallow the next lines", () => {
    const src = "const x = <p>don't</p>;\nimport { later } from \"./later\";\n";
    same(relativeSpecifiers(stripComments(src)), ["./later"], "specifiers");
  });

  test("a disk read is an fs call, an fs import, or a path built from where the file lives", () => {
    for (const reads of [
      "readFileSync(p)", "readdirSync(d)", "await readFile(p)", "existsSync(p)",
      "import fs from \"node:fs\";", "import { x } from \"fs/promises\";",
      "new URL(\"../x\", import.meta.url)", "join(__dirname, \"x\")", "join(process.cwd(), \"x\")",
    ]) assert(readsFromDisk(stripComments(reads)), `${reads} is a disk read`);
    for (const quiet of [
      "const readers = [];", "// readFileSync", "import { x } from \"../fsUtil\";", "process.exit(0)",
    ]) assert(!readsFromDisk(stripComments(quiet)), `${quiet} is not a disk read`);
  });

  test("a declared path that does not exist is reported", () => {
    const bad = buildGraph(makeTree({ [T("ghost")]: "// disk-reads: ui/src/no/such/file.ts\nexport const r = 1;\n" }));
    const missing = missingDeclaredPaths(bad, made[made.length - 1]);
    same(missing, [`${T("ghost")}: ui/src/no/such/file.ts`, `${T("declaresDir")}: server/notes.md`], "missing");
  });

  // ------------------------------------------------------- the git side
  const git = (cwd: string, ...args: string[]): void => {
    execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
      "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", ...args], { cwd, stdio: "pipe" });
  };
  const put = (repo: string, rel: string, text: string): void => {
    mkdirSync(dirname(join(repo, rel)), { recursive: true });
    writeFileSync(join(repo, rel), text, "utf8");
  };

  test("changedFiles reads base...HEAD, the uncommitted files and the untracked ones; a rename is a delete and an add", () => {
    const repo = mkdtempSync(join(tmpdir(), "astrodeck-select-git-")).split("\\").join("/");
    made.push(repo);
    git(repo, "init", "-q", "-b", "main");
    put(repo, "ui/src/keep.ts", "export const keep = 1;\n");
    put(repo, "ui/src/old.ts", "export const old = 1;\n");
    put(repo, "ui/src/edit.ts", "export const edit = 1;\n");
    git(repo, "add", "-A");
    git(repo, "commit", "-q", "-m", "base");
    git(repo, "checkout", "-q", "-b", "feature");
    renameSync(join(repo, "ui/src/old.ts"), join(repo, "ui/src/new.ts"));
    put(repo, "ui/src/edit.ts", "export const edit = 2;\n");
    git(repo, "add", "-A");
    git(repo, "commit", "-q", "-m", "feature");
    git(repo, "checkout", "-q", "main");
    put(repo, "ui/src/mainOnly.ts", "export const mainOnly = 1;\n");
    git(repo, "add", "-A");
    git(repo, "commit", "-q", "-m", "main moved on");
    git(repo, "checkout", "-q", "feature");
    put(repo, "ui/src/keep.ts", "export const keep = 2;\n");
    put(repo, "ui/src/untracked.ts", "export const untracked = 1;\n");
    const got = changedFiles(repo, "main");
    same(got.changed, ["ui/src/edit.ts", "ui/src/keep.ts", "ui/src/new.ts", "ui/src/untracked.ts"], "changed");
    same(got.deleted, ["ui/src/old.ts"], "deleted");
  });

  test("changedFiles throws for a base git does not know, so the runner can fail open", () => {
    const repo = mkdtempSync(join(tmpdir(), "astrodeck-select-git-")).split("\\").join("/");
    made.push(repo);
    git(repo, "init", "-q", "-b", "main");
    put(repo, "a.txt", "a\n");
    git(repo, "add", "-A");
    git(repo, "commit", "-q", "-m", "base");
    let threw = false;
    try { changedFiles(repo, "no-such-base"); } catch { threw = true; }
    assert(threw, "an unknown base must throw, not return an empty change");
  });
} finally {
  for (const dir of made) rmSync(dir, { recursive: true, force: true });
}

console.log(`selectTests.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
