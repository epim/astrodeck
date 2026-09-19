// test-css-stub-hooks.mjs - the actual ".css" -> empty-module hook.
//
// Shared between BOTH registration mechanisms test-css-stub.mjs can pick:
// Node's synchronous `registerHooks()` takes a plain object of hook
// functions, and the older, asynchronous `module.register()` takes a
// module specifier whose named exports ARE those same hook functions. This
// file exports `load` in the one shape both APIs expect
// (`load(url, context, nextLoad)`), so test-css-stub.mjs can hand the exact
// same function to either one and there is exactly one place that carries
// the ".css" check itself.

export function load(url, context, nextLoad) {
  // THE check: Node's ESM loader has no handler for a raw ".css" specifier
  // and throws ERR_UNKNOWN_FILE_EXTENSION before any test in the importing
  // file runs (issue #50). jsdom (this project's DOM test harness) never
  // applies stylesheets anyway, so resolving to an empty module is exactly
  // as faithful as loading the real one would be.
  if (url.endsWith(".css")) {
    return { format: "module", shortCircuit: true, source: "export default {};" };
  }
  return nextLoad(url, context);
}
