// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w8_9RecordReads.test.ts - the run-time read map of the UI suite (#1268, WP 8.9).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/__tests__/w8_9RecordReads.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. `node run-tests.mjs --record-reads <out.json>` runs every
// test file with tools/recordReads.mjs preloaded, which wraps node:fs and notes
// each path a test (or a helper it imports) opens. The read map says which test
// reads which file outside its own imports, and which of those the test did not
// declare in its `// disk-reads:` line. Checked here:
//
//   * the recorder sees readFileSync, readdirSync, statSync, existsSync, the
//     promise API and a file: URL, and it does nothing at all when the two
//     environment variables are not set (the default: no cost when off);
//   * what the map keeps: reads in the repository, outside the test's import
//     closure, outside node_modules, outside ui/ beyond ui/src (a change there
//     already runs everything);
//   * what counts as declared: an exact path, or a directory with a trailing "/";
//   * the runner's `--record-reads` option parses, and a real test file run
//     under the recorder reads nothing it did not declare.
//
// NAMED MUTANT (applied to ui/tools/recordReads.mjs from a byte backup and
// restored): `undeclaredReads` made to treat every read as covered. Observed
// red: "a read outside the declaration is named".
//
// disk-reads: ui/src/, ui/tools/recordReads.mjs, ui/tools/selectTests.mjs, ui/run-tests.mjs

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

interface Graph {
  files: Set<string>;
  tests: string[];
  declared: Map<string, string[]>;
  reverse: Map<string, Set<string>>;
}
interface TestReads { reads: string[]; inRepo: string[]; declared: string[]; undeclared: string[] }
interface RecordModule {
  RECORD_DIR_ENV: string;
  RECORD_TEST_ENV: string;
  recordFilePath(dir: string, testFile: string): string;
  importClosure(graph: Graph, test: string): Set<string>;
  readsOutsideImports(raw: string[], opts: { repoRoot: string; closure: Set<string> }): string[];
  repoReads(raw: string[], opts: { repoRoot: string; test: string }): string[];
  undeclaredReads(reads: string[], declared: string[]): string[];
  readMap(dir: string, graph: Graph, repoRoot: string): Record<string, TestReads>;
}
interface SelectModule { buildGraph(root: string): Graph }
interface RunModule {
  parseArgs(argv: string[]): { changed: string | null; filters: string[]; recordReads: string | null };
  runOne(file: string, timeoutMs?: number, opts?: { exitGraceMs?: number; readsDir?: string | null }): Promise<{ ok: boolean }>;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const UI_DIR = dirname(dirname(dirname(fileURLToPath(import.meta.url)))).split("\\").join("/");
const REPO_ROOT = dirname(UI_DIR);
const RECORDER_URL = new URL("../../tools/recordReads.mjs", import.meta.url).href;
const rec = (await nodeImport(RECORDER_URL)) as RecordModule;
const { buildGraph } = (await nodeImport(new URL("../../tools/selectTests.mjs", import.meta.url).href)) as SelectModule;
const { parseArgs, runOne } = (await nodeImport(new URL("../../run-tests.mjs", import.meta.url).href)) as RunModule;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const same = (got: string[], want: string[], what: string): void =>
  assert(JSON.stringify([...got].sort()) === JSON.stringify([...want].sort()),
    `${what}: wanted ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);

const made: string[] = [];
function scratch(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix)).split("\\").join("/");
  made.push(dir);
  return dir;
}

// A child that opens one file each way, under the recorder or not.
function runChild(dir: string | null, testName: string, targets: Record<string, string>, extra: string[] = []): void {
  const script = join(scratch("astrodeck-readmap-script-"), "child.mjs");
  writeFileSync(script, [
    ...extra,
    "import fs, { readFileSync as named } from \"node:fs\";",
    `const t = ${JSON.stringify(targets)};`,
    "named(t.read);",
    "fs.readdirSync(t.dir);",
    "fs.statSync(t.stat);",
    "fs.existsSync(t.exists);",
    "await fs.promises.readFile(t.promise);",
    "fs.readFileSync(new URL(t.url));",
    "",
  ].join("\n"), "utf8");
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env[rec.RECORD_DIR_ENV];
  delete env[rec.RECORD_TEST_ENV];
  if (dir !== null) { env[rec.RECORD_DIR_ENV] = dir; env[rec.RECORD_TEST_ENV] = testName; }
  execFileSync(process.execPath, ["--import", RECORDER_URL, script], { env, stdio: "pipe" });
}

try {
  const area = scratch("astrodeck-readmap-area-");
  const targets: Record<string, string> = {
    read: join(area, "read.txt"), dir: join(area, "listed"), stat: join(area, "stat.txt"),
    exists: join(area, "missing.txt"), promise: join(area, "promise.txt"), url: join(area, "url.txt"),
  };
  mkdirSync(targets.dir);
  for (const k of ["read", "stat", "promise", "url"]) writeFileSync(targets[k], "x", "utf8");

  test("the recorder sees every way a test can open a path", () => {
    const dir = scratch("astrodeck-readmap-rec-");
    runChild(dir, "ui/src/a.test.ts", { ...targets, url: pathToFileURL(targets.url).href });
    const file = rec.recordFilePath(dir, "ui/src/a.test.ts");
    const seen = new Set((JSON.parse(readFileSync(file, "utf8")) as string[]).map((p) => p.split("\\").join("/")));
    for (const [how, path] of Object.entries(targets)) {
      assert(seen.has(path.split("\\").join("/")), `${how} was not recorded: ${path} not in ${JSON.stringify([...seen].slice(0, 6))}`);
    }
  });

  test("a read the loader makes for a module in node_modules is not the test's read", () => {
    const dir = scratch("astrodeck-readmap-loader-");
    const lib = scratch("astrodeck-readmap-lib-");
    mkdirSync(join(lib, "node_modules", "pkg"), { recursive: true });
    const helper = join(lib, "node_modules", "pkg", "read.mjs");
    writeFileSync(helper, [
      "import { readFileSync } from \"node:fs\";",
      "export const go = (p) => readFileSync(p);",
      "",
    ].join("\n"), "utf8");
    const theirs = join(area, "theirs.txt");
    writeFileSync(theirs, "x", "utf8");
    runChild(dir, "ui/src/b.test.ts", { ...targets, url: pathToFileURL(targets.url).href }, [
      `import { go } from ${JSON.stringify(pathToFileURL(helper).href)};`,
      `go(${JSON.stringify(theirs)});`,
    ]);
    const seen = (JSON.parse(readFileSync(rec.recordFilePath(dir, "ui/src/b.test.ts"), "utf8")) as string[])
      .map((p) => p.split("\\").join("/"));
    assert(seen.includes(targets.read.split("\\").join("/")), "the test's own read is missing");
    assert(!seen.includes(theirs.split("\\").join("/")), "a read made through a node_modules helper was recorded");
  });

  test("with the environment variables unset the recorder writes nothing and wraps nothing", () => {
    const dir = scratch("astrodeck-readmap-off-");
    runChild(null, "ui/src/a.test.ts", { ...targets, url: pathToFileURL(targets.url).href });
    same(readdirSync(dir), [], "files written");
  });

  test("a record file is named for its test and two tests never share one", () => {
    const a = rec.recordFilePath("d", "ui/src/lib/__tests__/x.test.ts");
    const b = rec.recordFilePath("d", "ui/src/components/__tests__/x.test.ts");
    assert(a !== b, "same record file for two tests");
    assert(a.startsWith("d/") && a.endsWith(".json"), `unexpected record file ${a}`);
  });

  // ------------------------------------------------------ what the map keeps
  const tree = scratch("astrodeck-readmap-tree-");
  const put = (rel: string, text: string): void => {
    mkdirSync(dirname(join(tree, rel)), { recursive: true });
    writeFileSync(join(tree, rel), text, "utf8");
  };
  put("ui/src/lib/x.ts", "export const x = 1;\n");
  put("ui/src/lib/y.ts", "export const y = 1;\n");
  put("ui/src/__tests__/a.test.ts", "import { x } from \"../lib/x\";\nexport const r = x;\n");
  const graph = buildGraph(tree);
  const T = "ui/src/__tests__/a.test.ts";
  const abs = (rel: string): string => join(tree, rel);
  const closure = rec.importClosure(graph, T);

  test("the import closure holds the test and everything it imports, and no more", () => {
    same([...closure], [T, "ui/src/lib/x.ts"], "closure");
  });

  test("the map keeps repository reads outside the closure, and drops the rest", () => {
    const raw = [
      abs("ui/src/lib/x.ts"), abs(T), abs("ui/src/lib/y.ts"), abs("server/astrodeck/z.py"),
      abs("server/tests/fixtures"), abs("ui/package.json"), abs("ui/tsconfig.json"),
      abs("ui/node_modules/tsx/dist/a.cjs"), abs("node_modules/q/index.js"),
      join(scratch("astrodeck-readmap-elsewhere-"), "outside.txt"),
      abs("ui/src/lib/y.ts"),
    ];
    same(rec.readsOutsideImports(raw, { repoRoot: tree, closure }),
      ["server/astrodeck/z.py", "server/tests/fixtures", "ui/src/lib/y.ts"], "kept reads");
  });

  test("a read written with backslashes or as a file: URL is the same read", () => {
    // A backslash separates path parts on Windows only; elsewhere it is an
    // ordinary character in a file name, so that spelling is tried there alone.
    const a = process.platform === "win32" ? abs("server/a.py").split("/").join("\\") : abs("server/a.py");
    const raw = [a, pathToFileURL(abs("server/b.py")).href];
    same(rec.readsOutsideImports(raw, { repoRoot: tree, closure }), ["server/a.py", "server/b.py"], "kept reads");
  });

  test("repoReads is everything the test opened in the repository but itself and node_modules", () => {
    const raw = [abs(T), abs("ui/src/lib/x.ts"), abs("ui/package.json"), abs("docs/n.md"),
      abs("ui/node_modules/tsx/a.cjs"), join(scratch("astrodeck-readmap-elsewhere-"), "o.txt")];
    same(rec.repoReads(raw, { repoRoot: tree, test: T }), ["docs/n.md", "ui/package.json", "ui/src/lib/x.ts"], "repo reads");
  });

  // ------------------------------------------------ what counts as declared
  test("a read outside the declaration is named", () => {
    same(rec.undeclaredReads(["server/a.py", "server/b.py"], ["server/a.py"]), ["server/b.py"], "undeclared");
  });

  test("a directory declaration (trailing slash) covers what is under it, and the directory itself", () => {
    same(rec.undeclaredReads(["server/tests/f.json", "server/tests"], ["server/tests/"]), [], "undeclared");
    same(rec.undeclaredReads(["server/tests2/f.json"], ["server/tests/"]), ["server/tests2/f.json"],
      "a longer sibling name is not covered");
  });

  test("an exact declaration does not cover a file beside it", () => {
    same(rec.undeclaredReads(["docs/a.md"], ["docs/b.md"]), ["docs/a.md"], "undeclared");
  });

  // ---------------------------------------------- the map over a record dir
  test("readMap reads each record file and grades it against the test's declaration", () => {
    const dir = scratch("astrodeck-readmap-map-");
    put("ui/src/__tests__/b.test.ts", "// disk-reads: server/\nexport const r = 1;\n");
    const g = buildGraph(tree);
    writeFileSync(rec.recordFilePath(dir, T), JSON.stringify([abs("ui/src/lib/x.ts"), abs("docs/n.md")]), "utf8");
    writeFileSync(rec.recordFilePath(dir, "ui/src/__tests__/b.test.ts"),
      JSON.stringify([abs("server/a.py"), abs("docs/n.md")]), "utf8");
    const map = rec.readMap(dir, g, tree);
    same(map[T].reads, ["docs/n.md"], "a.test reads");
    same(map[T].inRepo, ["docs/n.md", "ui/src/lib/x.ts"], "a.test in-repo reads");
    same(map[T].undeclared, ["docs/n.md"], "a.test undeclared");
    same(map["ui/src/__tests__/b.test.ts"].undeclared, ["docs/n.md"], "b.test undeclared");
    same(map["ui/src/__tests__/b.test.ts"].declared, ["server/"], "b.test declared");
  });

  // ------------------------------------------------------------ the runner
  test("the runner takes --record-reads <out> and refuses it without a value", () => {
    const a = parseArgs(["--record-reads", "reads.json", "nav"]);
    assert(a.recordReads === "reads.json" && a.filters[0] === "nav", "option and filter not both read");
    assert(parseArgs([]).recordReads === null, "recording must be off by default");
    let message = "";
    try { parseArgs(["--record-reads"]); } catch (e) { message = (e as Error).message; }
    assert(message.includes("--record-reads needs"), `no usage message: ${message}`);
  });

  await testAsync("a real test file run under the recorder reads nothing it did not declare", async () => {
    const dir = scratch("astrodeck-readmap-real-");
    const file = join(UI_DIR, "src/__tests__/nav.test.ts");
    const r = await runOne(file, 60_000, { readsDir: dir });
    assert(r.ok, "nav.test.ts did not pass under the recorder");
    const map = rec.readMap(dir, buildGraph(REPO_ROOT), REPO_ROOT);
    const mine = map["ui/src/__tests__/nav.test.ts"];
    assert(mine !== undefined && mine.reads.length > 0, "nav.test.ts reads source files and none was recorded");
    same(mine.undeclared, [], "undeclared reads of nav.test.ts");
  });
} finally {
  for (const dir of made) rmSync(dir, { recursive: true, force: true });
}

console.log(`w8_9RecordReads.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
