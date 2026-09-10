// versions.ts — the build-time UI version string (ARCHITECTURE.md #12, S6:
// "the UI version is injected at build time (`define: { __APP_VERSION__ }`
// from `ui/package.json`)"). Guarded with `typeof` so this module (and any
// test importing it under plain Node/tsx, with no bundler define pass) never
// throws a ReferenceError.

declare const __APP_VERSION__: string | undefined;

/** The injected build version, or "dev" when the define is absent (a Node
 *  test run, or a dev server that never set it). */
export function appVersion(): string {
  return typeof __APP_VERSION__ !== "undefined" && __APP_VERSION__ ? __APP_VERSION__ : "dev";
}
