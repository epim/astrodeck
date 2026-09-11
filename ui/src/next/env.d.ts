// env.d.ts - the two build-time constants the new front end reads.
//
// Deliberately NOT `/// <reference types="vite/client" />`: that pulls in a
// large ambient surface (every asset-module shape, every env key) into a
// project whose `tsconfig` currently references no external type packages, and
// the only things this codebase actually reads are the two below. A narrow
// declaration is also a check: adding a third build-time constant has to be
// declared here, which is the moment to ask whether it belongs in the bundle at
// all.
//
// `.d.ts` files are exempt from `moduleDetection: "force"`, so these stay
// global rather than becoming module-scoped and invisible.

interface ImportMetaEnv {
  /** True in `vite build` output, false under `vite dev`. */
  readonly PROD: boolean;
  readonly DEV: boolean;
  readonly MODE: string;
}

interface ImportMeta {
  /** OPTIONAL on purpose. Vite fills it in; plain Node (which is what every
   *  test in this repo runs under, via `tsx`) does not, and a shell effect that
   *  read `import.meta.env.PROD` unguarded would throw on `undefined` in every
   *  DOM test that mounts the app. */
  readonly env?: ImportMetaEnv;
}

/** `ui/package.json`'s version, injected by `vite.config.ts`'s `define`
 *  (ARCHITECTURE.md section 12, S6). Absent outside a bundler pass, which is
 *  why `next/lib/versions.ts` guards it with `typeof`. */
declare const __APP_VERSION__: string;
