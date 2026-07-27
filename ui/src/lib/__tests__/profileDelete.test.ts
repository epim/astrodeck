// profileDelete.test.ts — the two judgements behind Profiles -> Delete.
//
// Run: npx tsx src/lib/__tests__/profileDelete.test.ts
//
// What is worth pinning here is NOT the wording but the guarantees the wording
// has to keep: a blocked principal always gets a sentence; the confirm always
// names the profile; the body always says the two things a tired user fears
// (the rig, the data) are safe; and deleting the ACTIVE profile is BOTH harder
// to confirm and honest about the boot consequence — while never being
// impossible (that would strand a single-profile user with no way out).

// profileDelete imports `accessPhrase` from lib/caps, which pulls in the store
// graph — and that reads localStorage / `document` / `window.location` at import
// time. Install the same minimal browser stubs caps.test.ts uses, BEFORE the
// dynamic import.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage; document?: unknown; window?: unknown;
};
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage() as unknown as Storage;
if (typeof g.document === "undefined") {
  const classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  const style = { setProperty() {}, getPropertyValue() { return ""; } };
  g.document = { documentElement: { classList, style } };
}
if (typeof g.window === "undefined") {
  g.window = {
    location: { pathname: "/", protocol: "http:", host: "test" },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    setInterval: globalThis.setInterval.bind(globalThis),
    clearInterval: globalThis.clearInterval.bind(globalThis),
    addEventListener() {},
    removeEventListener() {},
  };
}

const { profileDeleteLock, profileDeleteConfirm } = await import("../profileDelete");

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

test("a principal WITH config.backend is not locked", () => {
  assert(profileDeleteLock(true) === null, "holder should get no lock reason");
});

test("a principal WITHOUT config.backend gets a stated reason, not a bare flag", () => {
  const reason = profileDeleteLock(false);
  assert(typeof reason === "string" && reason.length > 0, "must return a sentence");
  assert(/admin access/.test(reason!), `reason must name who can: ${reason}`);
  assert(/delet/i.test(reason!), `reason must name the action: ${reason}`);
});

test("the confirm names the profile in the title", () => {
  const c = profileDeleteConfirm({ name: "Dark Site Refractor", active: false });
  assert(c.title.includes("Dark Site Refractor"), `title must name it: ${c.title}`);
});

test("the body says what is lost AND what is not", () => {
  const c = profileDeleteConfirm({ name: "Backyard Newt", active: false });
  assert(/no undo|permanent/i.test(c.body), "must say it is irreversible");
  assert(/does not disconnect/i.test(c.body), "must say the live rig survives");
  assert(/captured frames/i.test(c.body), "must say captured data is untouched");
});

test("an inactive profile is a plain tap-confirm; the affirmative is danger-toned", () => {
  const c = profileDeleteConfirm({ name: "Backyard Newt", active: false });
  assert(c.mode === "confirm", `expected confirm, got ${c.mode}`);
  assert(c.tone === "danger", "destructive dialogs are danger-toned");
  assert(c.cancelLabel.length > 0, "dismissal must be labelled, never implicit");
});

test("the ACTIVE profile escalates to hold and states the boot consequence", () => {
  const c = profileDeleteConfirm({ name: "Backyard Newt", active: true });
  assert(c.mode === "hold", `active profile must need a hold, got ${c.mode}`);
  assert(/auto-connect/i.test(c.body), `must name the boot consequence: ${c.body}`);
  assert(/ACTIVE/.test(c.body), "must say which profile this is");
});

test("deleting the active profile is never made impossible", () => {
  // A user whose only profile is the active one must still have a way out.
  const c = profileDeleteConfirm({ name: "Only Rig", active: true });
  assert(c.confirmLabel.length > 0, "there must still be an affirmative path");
});

// eslint-disable-next-line no-console
console.log(`profileDelete.test: ${passed} passed, ${failed} failed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed };
