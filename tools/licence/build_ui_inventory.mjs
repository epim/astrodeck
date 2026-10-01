// Build the normal UI and record the inputs behind every shipped output.
// The artifact audit consumes this local evidence; it is not a runtime asset.
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "../../ui/node_modules/vite/dist/node/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ui = path.join(root, "ui");
const out = path.join(root, ".probe/licence/ui-build-inventory.json");
const chunks = new Map();
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");

function inputName(raw) {
  const name = raw.replaceAll("\\", "/").replaceAll("\0", "");
  const marker = "/node_modules/";
  if (name.includes(marker)) return "npm:" + name.slice(name.lastIndexOf(marker) + marker.length);
  if (name.startsWith("vite/")) return "npm:" + name;
  const base = root.replaceAll("\\", "/") + "/";
  if (name.startsWith(base)) return "repo:" + name.slice(base.length);
  // Unknown paths must be classified before a bundle is cleared.
  if (name === "commonjsHelpers.js") return "build-helper:" + name;
  throw new Error("Unclassified module input; inspect the private build locally");
}

await build({
  root: ui,
  configFile: path.join(ui, "vite.config.ts"),
  configLoader: "runner",
  logLevel: "warn",
  plugins: [{
    name: "astrodeck-licence-inventory",
    generateBundle(_options, bundle) {
      for (const [file, item] of Object.entries(bundle)) {
        if (item.type === "chunk") {
          chunks.set(file, [...new Set(Object.keys(item.modules).map(inputName))].sort());
        } else {
          chunks.set(file, (item.originalFileNames || []).map((name) =>
            name.startsWith("/") || /^[A-Za-z]:/.test(name)
              ? inputName(name) : "repo:ui/" + name).sort());
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
        inputs: chunks.get(relative) || ["repo:ui/public/" + relative] });
    } else throw new Error("Unexpected link in UI output");
  }
  return found.sort((a, b) => a.path.localeCompare(b.path));
}
await mkdir(path.dirname(out), { recursive: true });
const credits = await readFile(path.join(ui, "src/credits.generated.json"));
const files = await walk(path.join(ui, "dist"));
await writeFile(out, JSON.stringify({
  generator: "tools/licence/build_ui_inventory.mjs",
  credits_sha256: digest(credits),
  files,
}, null, 2) + "\n", "utf8");
console.log("Recorded " + files.length + " UI outputs and their build inputs.");
