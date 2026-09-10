// index.ts — barrel for the T0.3 pure library modules (ARCHITECTURE.md #1
// `next/lib/`). `gateHook.ts` is deliberately NOT re-exported here: it is the
// one file in this directory that imports React and the store, and keeping it
// out of this barrel means importing `next/lib` never pulls React/zustand into
// a plain-Node/tsx test. Import it directly: `next/lib/gateHook`.

export * from "./gate";
export * from "./incidents";
export * from "./allocation";
export * from "./reach";
export * from "./advection";
export * from "./cloudTiles";
export * from "./horizonModel";
export * from "./fov";
export * from "./format";
// `qr.ts` is pure arithmetic over arrays and strings - no React, no DOM, no
// store - so it belongs here; the component that draws its output lives in
// `hubs/settings/sheets/QrCode.tsx`.
export * from "./qr";
export * from "./versions";
