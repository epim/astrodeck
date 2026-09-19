// test-css-stub.mjs - makes ".css" a no-op for the plain-Node test runner,
// on whichever Node version is actually running it.
//
// Registered by run-tests.mjs via `--import` on every child process, BEFORE
// `--import tsx`. THE ORDER MATTERS: `--import` targets run in the order
// given, each one's own top-level work finishing before the next starts, so
// whichever hook mechanism this file installs (see below) is fully in place
// before tsx's `--import` runs and registers its own hooks. Whichever
// internal order Node then composes the two hook chains in, ours was
// registered first and gets first refusal on every specifier -- verified by
// hand in both `--import` orderings on Node 24 (see task-7-report.md).
//
// TWO REGISTRATION MECHANISMS, because one Node version does not have both:
//   - `registerHooks()` (synchronous, in-thread) only exists from Node
//     v22.15.0 / v23.5.0 onward.
//   - `module.register()` (asynchronous, off-thread) exists back to Node
//     v20.6.0 and is what CI's `ui` job (pinned to Node 20 in
//     .github/workflows/ci.yml) actually has available.
// The first version of this file used `registerHooks()` unconditionally and
// crashed CI outright -- see issue #61. Both mechanisms are handed the exact
// same `load(url, context, nextLoad)` function from test-css-stub-hooks.mjs,
// so there is exactly one implementation of the ".css" check regardless of
// which branch runs.
//
// ASTRODECK_CSS_STUB_FORCE_ASYNC=1 forces the module.register() branch even
// on a Node that has registerHooks, so a machine that only has modern Node
// (this one) can still exercise and mutation-test the branch CI's Node 20
// will actually take. See src/__tests__/runTestsCss.test.ts.

import { register, registerHooks } from "node:module";

const HOOKS_URL = new URL("./test-css-stub-hooks.mjs", import.meta.url).href;
const forceAsync = process.env.ASTRODECK_CSS_STUB_FORCE_ASYNC === "1";

if (!forceAsync && typeof registerHooks === "function") {
  const { load } = await import(HOOKS_URL);
  registerHooks({ load });
} else {
  register(HOOKS_URL, import.meta.url);
}
