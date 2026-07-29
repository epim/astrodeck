// equipmentProfiles.test.ts — pins the profile decision layer that UX review
// round 4 found wrong in three different ways at once (S1).
//
// Run directly:  npx tsx src/__tests__/equipmentProfiles.test.ts
//
// The three states these functions exist to make impossible, all measured on a
// live instance before the fix:
//
//   1. THE DEAD END. Connect the way the setup guide says (POST /api/connect/sim,
//      a boot profile, or simply a second browser) and this browser's
//      AssignmentMap is empty. Save was gated on `assignedCount === 0` with the
//      native `disabled` attribute: measured `disabled=true`, opacity 0.35, no
//      title, no aria-label, no aria-disabled, beside eleven live devices. Real
//      keystrokes, Enter and a real touch tap all did nothing and said nothing.
//
//   2. THE RIG-KILLER. `doSaveProfile` skipped every simulator row and wrote
//      `primary_backend: "none"`, so a saved sim rig persisted as
//      `devices: [] · mode: "empty"` — the one shape that resolves to
//      `RigSpec(primary="none", roles={})`. Since every activate runs
//      `hub._teardown()` first, activating it could only destroy. Measured:
//      10 devices connected → one tap → 0, no dialog. Capturing the same rig
//      writes `primary_backend: "sim"`, which activates back to 10.
//
//   3. THE WRONG QUESTION. The activation confirm was gated on whether the
//      PROFILE resolved a real mount/focuser, so the sim case above correctly
//      skipped it. What makes an activate expensive is the rig it DROPS.
//
// `profilePrimary` mirrors server/astrodeck/profiles.py::to_rigspec +
// _derived_primary, read 2026-07-28; those cases are transcribed below rather
// than guessed, because getting them wrong is what makes the dialog lie.

const {
  liveRoleCount,
  profilePrimary,
  profileConnectsNothing,
  profileResolvesRealMotion,
  profileActivateConfirm,
  profileSaveSource,
  profileSaveLock,
} = await import("../lib/equipment");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// ====================================================================
// liveRoleCount — one number for "how big is this rig"
// ====================================================================

test("the guider counts, even though it is not in the device map", () => {
  // MEASURED on /api/status with the rig up via the RigSpec path: `connected`
  // has no `guider` key at all (the guiding engine is not a device), while
  // backend_links carries {role:"guider", connected:true}. Counting only the
  // first is what let one screen say 10 and 11 about the same rig.
  const status = {
    connected: { camera: { connected: true }, telescope: { connected: true } },
    backend_links: [
      { role: "camera", connected: true },
      { role: "telescope", connected: true },
      { role: "guider", connected: true },
    ],
  };
  eq(liveRoleCount(status), 3, "guider included, camera/telescope not double-counted");
});

test("counts nothing when nothing is up, and survives missing fields", () => {
  eq(liveRoleCount(null), 0, "no status yet");
  eq(liveRoleCount(undefined), 0, "undefined");
  eq(liveRoleCount({}), 0, "empty status");
  eq(liveRoleCount({ connected: { camera: { connected: false } }, backend_links: [] }), 0, "down");
  eq(liveRoleCount({ connected: { camera: null }, backend_links: null }), 0, "null entries");
});

test("a link that reports down does not count, even with a device entry", () => {
  eq(liveRoleCount({
    connected: { camera: { connected: true } },
    backend_links: [{ role: "camera", connected: false }, { role: "guider", connected: false }],
  }), 1, "the device is live; the guider is not");
});

// ====================================================================
// profilePrimary — transcribed from to_rigspec + _derived_primary
// ====================================================================

test("explicit primary is honoured", () => {
  eq(profilePrimary({ primary_backend: "sim", devices: [] }), "sim", "sim primary");
  eq(profilePrimary({ primary_backend: "none", devices: [] }), "none", "none primary");
  eq(profilePrimary({ primary_backend: "native", devices: [{ role: "camera" }] }), "native", "native");
});

test("an EMPTY primary still derives a working rig — never 'none'", () => {
  // _derived_primary: devices -> "native", else "sim". The trap this pins is
  // reading "" as "connects nothing"; it does not.
  eq(profilePrimary({ primary_backend: "", devices: [] }), "sim", "no rows, no primary");
  eq(profilePrimary({ primary_backend: null, devices: [] }), "sim", "null primary");
  eq(profilePrimary({ devices: [{ role: "camera" }] }), "native", "rows, no primary");
});

test("a nina_host-only legacy profile forces nina, even over an explicit primary", () => {
  // to_rigspec applies this AFTER reading primary_backend, so order matters.
  eq(profilePrimary({ primary_backend: "none", devices: [], nina_host: "10.0.0.4" }), "nina", "override");
  // ...but only when there are no device rows.
  eq(profilePrimary({ primary_backend: "native", devices: [{ role: "camera" }], nina_host: "10.0.0.4" }),
     "native", "rows win over the legacy host");
});

// ====================================================================
// profileConnectsNothing — the rig-killer predicate
// ====================================================================

test("the exact profile the old Equipment Save wrote connects nothing", () => {
  assert(profileConnectsNothing({ primary_backend: "none", devices: [], nina_host: null }),
         "primary none + no rows must read as 'connects nothing'");
});

test("the captured simulator rig does NOT connect nothing", () => {
  // Measured: POST /api/profiles/capture on a live sim rig returns
  // {primary_backend:"sim", devices:[]}, and activating it brought all 10 back.
  assert(!profileConnectsNothing({ primary_backend: "sim", devices: [] }),
         "a captured sim rig reconnects and must not be called empty");
});

test("device rows always mean something reconnects", () => {
  assert(!profileConnectsNothing({ primary_backend: "none", devices: [{ role: "camera" }] }),
         "explicit rows are connected even under primary 'none'");
  assert(!profileConnectsNothing({ primary_backend: "none", devices: [], nina_host: "10.0.0.4" }),
         "a legacy NINA rig reconnects");
});

// ====================================================================
// profileResolvesRealMotion — moved out of ProfileList, behaviour pinned
// ====================================================================

test("real motion: explicit non-sim mount/focuser row", () => {
  assert(profileResolvesRealMotion({ primary_backend: "native", devices: [{ role: "telescope", backend: "alpaca" }] }),
         "an alpaca mount row is real motion");
  assert(!profileResolvesRealMotion({ primary_backend: "sim", devices: [{ role: "telescope", backend: "sim" }] }),
         "a sim mount row is not");
});

test("real motion: fails SAFE toward asking on a non-sim primary with no motion rows", () => {
  assert(profileResolvesRealMotion({ primary_backend: "native", devices: [{ role: "camera", backend: "alpaca" }] }),
         "a native primary may resolve a mount server-side");
  assert(profileResolvesRealMotion({ primary_backend: "", devices: [], nina_host: "10.0.0.4" }),
         "a legacy NINA rig is a real rig");
});

test("real motion: the over-prompt must NOT fire on a profile that connects nothing", () => {
  // Measured in the rendered dialog: the body read "stores no devices, so
  // nothing reconnects" and then "the hardware will attach and may move" —
  // both, three words apart. A zero-role RigSpec attaches nothing.
  assert(!profileResolvesRealMotion({ primary_backend: "none", devices: [], nina_host: null }),
         "a zero-role profile cannot move hardware");
});

test("connectsNothing and realMotion are never both true", () => {
  for (const p of [
    { primary_backend: "none", devices: [], nina_host: null },
    { primary_backend: "none", devices: [] },
  ]) {
    assert(!(profileConnectsNothing(p) && profileResolvesRealMotion(p)),
           `contradictory dialog body for ${JSON.stringify(p)}`);
  }
});

// ====================================================================
// profileActivateConfirm — the question the old gate never asked
// ====================================================================

test("nothing live and nothing real: no dialog at all", () => {
  const spec = profileActivateConfirm({
    name: "Backyard", connectsNothing: false, realMotion: false,
    liveDevices: 0, sequenceRunning: false,
  });
  eq(spec, null, "an activate that costs nothing must not nag");
});

test("THE NOVICE'S TAP: a live rig + a profile that puts nothing back", () => {
  const spec = profileActivateConfirm({
    name: "My rig", connectsNothing: true, realMotion: false,
    liveDevices: 10, sequenceRunning: false,
  });
  assert(spec !== null, "this must never be silent — it is pure destruction");
  eq(spec!.mode, "hold", "purely destructive activates get hold friction");
  assert(spec!.body.includes("10 devices"), `body must count the rig being dropped: ${spec!.body}`);
  assert(spec!.body.includes("no rig"), `body must say nothing comes back: ${spec!.body}`);
  eq(spec!.confirmLabel, "Activate anyway", "the affirmative admits what it is");
  eq(spec!.cancelLabel, "Keep this rig", "the escape names what it preserves");
  eq(spec!.tone, "danger", "danger tone");
});

test("a live rig + a profile that DOES reconnect: confirm, not hold", () => {
  const spec = profileActivateConfirm({
    name: "Backyard", connectsNothing: false, realMotion: false,
    liveDevices: 11, sequenceRunning: false,
  });
  assert(spec !== null, "dropping 11 devices always deserves a question");
  eq(spec!.mode, "confirm", "a swap is not a catastrophe");
  assert(spec!.body.includes("11 devices"), "counts the drop");
  assert(spec!.body.includes("connects the devices it stores"), "says what comes back");
  assert(!spec!.body.includes("Hold to confirm"), "no hold copy on a tap-confirm");
});

test("one device reads as one device", () => {
  const spec = profileActivateConfirm({
    name: "x", connectsNothing: false, realMotion: false, liveDevices: 1, sequenceRunning: false,
  })!;
  assert(spec.body.includes("the 1 device running"), `singular: ${spec.body}`);
  assert(!spec.body.includes("1 devices"), "no '1 devices'");
});

test("a running sequence escalates and says the teardown aborts it", () => {
  const spec = profileActivateConfirm({
    name: "x", connectsNothing: false, realMotion: false, liveDevices: 4, sequenceRunning: true,
  })!;
  eq(spec.mode, "hold", "never a single tap while frames are being taken");
  assert(spec.body.includes("aborts"), `must name the abort: ${spec.body}`);
});

test("the OLD gate still fires: real hardware, nothing connected yet", () => {
  const spec = profileActivateConfirm({
    name: "AM5N", connectsNothing: false, realMotion: true, liveDevices: 0, sequenceRunning: false,
  })!;
  eq(spec.mode, "hold", "attaching real motion keeps its hold-confirm");
  assert(spec.body.includes("may move"), "states the physical consequence");
  eq(spec.cancelLabel, "Cancel", "with no live rig there is none to 'keep'");
  assert(!spec.body.includes("disconnects the"), "nothing to disconnect, so don't claim it");
});

test("every produced body ends in a sentence, and hold copy only on hold", () => {
  for (const live of [0, 1, 9]) {
    for (const nothing of [false, true]) {
      for (const real of [false, true]) {
        for (const seq of [false, true]) {
          const spec = profileActivateConfirm({
            name: "n", connectsNothing: nothing, realMotion: real,
            liveDevices: live, sequenceRunning: seq,
          });
          if (!spec) continue;
          assert(spec.body.trim().endsWith("."), `body must be sentences: ${spec.body}`);
          eq(spec.body.includes("Hold to confirm"), spec.mode === "hold",
             `hold copy must match hold mode: ${spec.body}`);
        }
      }
    }
  }
});

// ====================================================================
// profileSaveSource / profileSaveLock — the dead end
// ====================================================================

test("a connected rig is the authority, whatever this browser picked", () => {
  eq(profileSaveSource(11, 0), "connected-rig", "the designer's state");
  eq(profileSaveSource(11, 11), "connected-rig", "connected still wins");
  eq(profileSaveSource(0, 3), "assignments", "picked but not connected");
  eq(profileSaveSource(0, 0), null, "nothing to save");
});

test("THE DESIGNER'S DEAD END IS GONE: 11 connected, 0 assigned, named", () => {
  eq(profileSaveLock({ permission: null, name: "Field rig", live: 11, assigned: 0, busy: false }),
     null, "Save must be live for a connected rig with an empty assignment map");
});

test("every block states a reason, and never an empty string", () => {
  const cases = [
    { permission: "Saving a profile needs an operator sign-in.", name: "x", live: 11, assigned: 0, busy: false },
    { permission: null, name: "x", live: 11, assigned: 0, busy: true },
    { permission: null, name: "x", live: 0, assigned: 0, busy: false },
    { permission: null, name: "   ", live: 11, assigned: 0, busy: false },
  ];
  for (const c of cases) {
    const r = profileSaveLock(c);
    assert(typeof r === "string" && r.trim().length > 10,
           `blocked Save must carry a sentence, got ${JSON.stringify(r)} for ${JSON.stringify(c)}`);
  }
});

test("lock precedence: permission > busy > nothing-to-save > unnamed", () => {
  eq(profileSaveLock({ permission: "PERM", name: "", live: 0, assigned: 0, busy: true }),
     "PERM", "permission outranks everything");
  const busy = profileSaveLock({ permission: null, name: "", live: 0, assigned: 0, busy: true })!;
  assert(busy.includes("running"), `busy reason: ${busy}`);
  const empty = profileSaveLock({ permission: null, name: "", live: 0, assigned: 0, busy: false })!;
  assert(empty.includes("connect a rig"), `nothing-to-save must point somewhere: ${empty}`);
  const unnamed = profileSaveLock({ permission: null, name: "  ", live: 4, assigned: 0, busy: false })!;
  assert(unnamed.toLowerCase().includes("name"), `unnamed reason: ${unnamed}`);
});

test("the assignments fallback still saves when nothing is connected", () => {
  eq(profileSaveLock({ permission: null, name: "Planned", live: 0, assigned: 5, busy: false }),
     null, "picked-but-not-connected is a legitimate profile");
});

// ---------------------------------------------------------------- report
console.log(`\nequipmentProfiles: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) throw new Error(`${failed} test(s) failed`);

export const result = { passed, failed };
