/** An explicitly illustrative night. This model never sends equipment commands. */
export const TARGETS = [
  { id: "M31", name: "Andromeda Galaxy", kind: "Galaxy", constellation: "Andromeda", alt: 62, az: 62, color: "mint", note: "A whole galaxy, in your frame.", window: "21:10–03:40", ra: "00h 42m 44s", dec: "+41° 16′ 09″" },
  { id: "NGC 7000", name: "North America Nebula", kind: "Nebula", constellation: "Cygnus", alt: 72, az: 280, color: "rose", note: "An ocean of glowing hydrogen.", window: "20:30–01:20", ra: "20h 59m 17s", dec: "+44° 31′ 44″" },
  { id: "M33", name: "Triangulum Galaxy", kind: "Galaxy", constellation: "Triangulum", alt: 39, az: 110, color: "blue", note: "Meet our other galactic neighbor.", window: "22:00–04:10", ra: "01h 33m 51s", dec: "+30° 39′ 37″" },
] as const;
export type Target = typeof TARGETS[number];
export type Options = { focus: boolean; guide: boolean; dither: boolean; clouds: boolean; park: boolean };
export const DEFAULT_OPTIONS: Options = { focus: true, guide: true, dither: true, clouds: true, park: true };
export function stepsFor(options: Options, target: Target, frames: number, exposure: number) {
  return [
    { id: "prepare", title: "Get ready", detail: "Check equipment and wait for darkness", icon: "sun" },
    { id: "center", title: "Find & frame", detail: `Center ${target.id} and confirm the field`, icon: "target" },
    ...(options.focus ? [{ id: "focus", title: "Find sharp focus", detail: "Measure stars and adjust the focuser", icon: "focus" }] : []),
    ...(options.guide ? [{ id: "guide", title: "Keep stars steady", detail: "Start guiding and wait for it to settle", icon: "activity" }] : []),
    { id: "capture", title: "Collect light", detail: `${frames} × ${exposure}s exposures${options.dither ? ", dither every 3 frames" : ""}`, icon: "camera" },
    ...(options.park ? [{ id: "park", title: "Finish gently", detail: "Park the mount and warm the camera", icon: "moon" }] : []),
  ];
}
export function durationLabel(frames: number, exposure: number) {
  const minutes = Math.round(frames * exposure / 60);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}
export function previewTime(offset: number) {
  const minutes = 21 * 60 + 30 + offset;
  return `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
