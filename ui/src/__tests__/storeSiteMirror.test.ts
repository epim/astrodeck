// storeSiteMirror.test.ts — the site slice is a MIRROR of config.site, and
// loadConfig() is the one place that keeps them in lock-step.
//
// The bug this pins (reported from a phone: "I just set my location manually
// annnnd... it doesn't show as having been set. However when I skipped ahead
// then it showed it"): SitePanel saved the site and called loadConfig(), which
// refreshed `config` only. `site` still held the WS `hello` bootstrap value, so
// FirstRunWizard's `site?.is_default ?? config?.site?.is_default` — and
// PreflightStrip / AtlasView / MonitorView / SequenceView / TonightPicker with
// it — kept reading the PRE-SAVE site until a `status` event happened to
// overwrite it, which needs a connected rig and never arrives on a cold first
// run.
//
// Same inline-assert harness as store.test.ts (no vitest/jest wired in yet).
// Run directly:  npx tsx src/__tests__/storeSiteMirror.test.ts

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  clear(): void {
    this.m.clear();
  }
}
const g = globalThis as unknown as {
  localStorage?: Storage;
  document?: unknown;
  window?: unknown;
  fetch?: unknown;
};
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}
if (typeof g.document === "undefined") {
  const classList = {
    toggle(_c: string, _on?: boolean): void {},
    add(_c: string): void {},
    remove(_c: string): void {},
    contains(_c: string): boolean {
      return false;
    },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string {
      return "";
    },
  };
  g.document = { documentElement: { classList, style } };
}

// GET /api/config is the only request these tests make.
let nextConfig: unknown = null;
g.fetch = async (): Promise<unknown> => ({
  ok: true,
  status: 200,
  json: async () => nextConfig,
});

const { useStore } = await import("../store");
import type { AppConfig, Site, SiteInfo } from "../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  return Promise.resolve()
    .then(fn)
    .then(
      () => {
        passed++;
      },
      (e: Error) => {
        failed++;
        failures.push(`✗ ${name}: ${e.message}`);
      },
    );
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const DEFAULT_SITE: Site = {
  name: "My Observatory",
  latitude: 0,
  longitude: 0,
  elevation_m: 0,
  is_default: true,
  horizon_min_deg: 15,
};

function configWith(site: Site): AppConfig {
  return { version: 2, site, optics: {} as AppConfig["optics"] } as AppConfig;
}

// ------------------------------------------------- the reported bug, pinned
await test("loadConfig mirrors the saved site into the `site` slice the wizard reads", async () => {
  // the WS `hello` bootstrap put the pre-save site here
  useStore.getState().setSite({ ...DEFAULT_SITE } as SiteInfo);
  const saved: Site = {
    name: "Ridge",
    latitude: 44.25,
    longitude: -3.5,
    elevation_m: 210,
    is_default: false,
    horizon_min_deg: 15,
  };
  nextConfig = configWith(saved);
  await useStore.getState().loadConfig();
  const s = useStore.getState().site;
  assert(!!s, "site set");
  eq(s!.is_default, false, "is_default follows the save");
  eq(s!.latitude, 44.25, "latitude");
  eq(useStore.getState().config?.site.is_default, false, "config.site too");
});

// A config bump that does NOT touch the site (drivers/optics/safety all bump
// `version`) must not hand every useSite() consumer a new object to re-render on.
await test("loadConfig keeps the site object identity when the site is unchanged", async () => {
  nextConfig = configWith({ ...DEFAULT_SITE });
  await useStore.getState().loadConfig();
  const first = useStore.getState().site;
  nextConfig = configWith({ ...DEFAULT_SITE }); // equal values, new object
  await useStore.getState().loadConfig();
  assert(useStore.getState().site === first, "same reference for an unchanged site");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nstoreSiteMirror.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
