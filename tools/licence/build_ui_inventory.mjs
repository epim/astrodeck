// Build the UI against exact per-environment credits without editing ui/src.
// All optional outputs stay in this checkout's private .probe directory.
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "../../ui/node_modules/vite/dist/node/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ui = path.join(root, "ui");
const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i];
  if (!["--credits", "--out-dir", "--report"].includes(key) || !process.argv[i + 1] || args.has(key)) {
    throw new Error("Usage: build_ui_inventory.mjs [--credits PATH] [--out-dir PATH] [--report PATH]");
  }
  args.set(key, path.resolve(process.argv[i + 1]));
}
function privatePath(value) {
  const relative = path.relative(path.join(root, ".probe"), value);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Output must be within this checkout's .probe directory");
  return value;
}
const defaultCredits = path.join(ui, "src/credits.generated.json");
const creditsPath = args.get("--credits") || defaultCredits;
const outDir = args.has("--out-dir") ? privatePath(args.get("--out-dir")) : path.join(ui, "dist");
const out = privatePath(args.get("--report") || path.join(root, ".probe/licence/ui-build-inventory.json"));
if (args.has("--credits")) privatePath(creditsPath);
const chunks = new Map();
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const normalized = (bytes) => Buffer.from(bytes.toString("utf8").replaceAll("\r\n", "\n"), "utf8");
const slash = (value) => value.replaceAll("\\", "/");
function inputName(raw) {
  const name = slash(raw).replaceAll("\0", "");
  if (name === slash(creditsPath)) return "repo:ui/src/credits.generated.json";
  const marker = "/node_modules/";
  if (name.includes(marker)) return "npm:" + name.slice(name.lastIndexOf(marker) + marker.length);
  if (name.startsWith("vite/")) return "npm:" + name;
  const base = slash(root) + "/";
  if (name.startsWith(base)) return "repo:" + name.slice(base.length);
  if (name === "commonjsHelpers.js") return "build-helper:" + name;
  throw new Error("Unclassified module input; inspect the private build locally");
}
await build({
  root: ui,
  configFile: path.join(ui, "vite.config.ts"),
  configLoader: "runner",
  logLevel: "warn",
  build: { outDir, emptyOutDir: true },
  plugins: [{
    name: "astrodeck-release-credits-overlay",
    enforce: "pre",
    resolveId(source, importer) {
      if (importer && path.resolve(path.dirname(importer), source) === defaultCredits) return creditsPath;
      if (path.isAbsolute(source) && path.resolve(source) === defaultCredits) return creditsPath;
      return null;
    },
  }, {
    name: "astrodeck-licence-inventory",
    generateBundle(_options, bundle) {
      for (const [file, item] of Object.entries(bundle)) {
        if (item.type === "chunk") {
          chunks.set(file, [...new Set(Object.keys(item.modules).map(inputName))].sort());
        } else {
          chunks.set(file, (item.originalFileNames || []).map((name) =>
            inputName(path.resolve(ui, name))).sort());
        }
      }
    },
  }],
});
async function walk(directory, prefix = "") {
  const found = [];
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix + item.name;
    if (item.isDirectory()) found.push(...await walk(path.join(directory, item.name), relative + "/"));
    else if (item.isFile()) {
      const bytes = await readFile(path.join(directory, item.name));
      found.push({ path: "ui/dist/" + relative, bytes: bytes.length, sha256: digest(bytes),
        inputs: chunks.get(relative) || [relative === "index.html" ? "repo:ui/index.html" : "repo:ui/public/" + relative] });
    } else throw new Error("Unexpected link in UI output");
  }
  return found.sort((a, b) => a.path.localeCompare(b.path));
}
const files = await walk(outDir);
const sources = new Set(files.flatMap((item) => item.inputs).filter((name) => name.startsWith("repo:ui/")));
const sourceInputs = [];
for (const name of [...sources].sort()) {
  const relative = name.slice(5);
  const file = relative === "ui/src/credits.generated.json" ? creditsPath : path.join(root, relative);
  const bytes = await readFile(file);
  const text = /\.(?:tsx?|jsx?|css|html|json|svg)$/.test(relative);
  sourceInputs.push({ path: relative, sha256: digest(text ? normalized(bytes) : bytes), encoding: text ? "normalized-utf8" : "bytes" });
}
await mkdir(path.dirname(out), { recursive: true });
const credits = await readFile(creditsPath);
await writeFile(out, JSON.stringify({
  schema_version: 2,
  generator: "tools/licence/build_ui_inventory.mjs",
  credits_sha256: digest(credits),
  npm_lock_sha256: digest(await readFile(path.join(ui, "package-lock.json"))),
  source_inputs: sourceInputs,
  files,
}, null, 2) + "\n", "utf8");
console.log("Recorded " + files.length + " UI outputs and " + sourceInputs.length + " source inputs; tracked credits unchanged.");