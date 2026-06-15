import { create } from "zustand";
import type {
  FocusEvent, GuideStats, LogLine, PolarState, PreviewInfo, RigStatus, SequenceState,
} from "./types";

export type ViewName =
  | "connect" | "capture" | "focus" | "mount" | "polar" | "guide" | "sequence" | "power";

interface AppState {
  view: ViewName;
  night: boolean;
  wsConnected: boolean;
  status: RigStatus | null;
  preview: PreviewInfo | null;
  focus: FocusEvent | null;
  guide: (GuideStats & { name?: string }) | null;
  sequence: SequenceState;
  polar: PolarState;
  logs: LogLine[];
  toast: { level: string; message: string; key: number } | null;

  setView: (v: ViewName) => void;
  toggleNight: () => void;
  handleEvent: (ev: { type: string; data: Record<string, unknown>; ts: number }) => void;
  setWsConnected: (ok: boolean) => void;
  showToast: (level: string, message: string) => void;
}

let toastKey = 0;

export const useStore = create<AppState>((set, get) => ({
  view: "connect",
  night: localStorage.getItem("astrodeck-night") === "1",
  wsConnected: false,
  status: null,
  preview: null,
  focus: null,
  guide: null,
  sequence: { state: "idle" },
  polar: { state: "idle", az_error: 0, alt_error: 0, total_error: 0, progress: 0, message: "", source: null },
  logs: [],
  toast: null,

  setView: (v) => set({ view: v }),

  toggleNight: () => {
    const night = !get().night;
    localStorage.setItem("astrodeck-night", night ? "1" : "0");
    document.documentElement.classList.toggle("night", night);
    set({ night });
  },

  setWsConnected: (ok) => set({ wsConnected: ok }),

  showToast: (level, message) =>
    set({ toast: { level, message, key: ++toastKey } }),

  handleEvent: (ev) => {
    switch (ev.type) {
      case "status":
        set({ status: ev.data as unknown as RigStatus });
        break;
      case "preview":
        set({ preview: ev.data as unknown as PreviewInfo });
        break;
      case "focus":
        set({ focus: ev.data as unknown as FocusEvent });
        break;
      case "guide":
        set({ guide: ev.data as unknown as GuideStats });
        break;
      case "sequence":
        set({ sequence: ev.data as unknown as SequenceState });
        break;
      case "polar":
        set({ polar: ev.data as unknown as PolarState });
        break;
      case "log": {
        const line = ev as unknown as LogLine;
        set((s) => ({ logs: [...s.logs.slice(-199), line] }));
        const level = (ev.data.level as string) ?? "info";
        if (level === "error" || level === "warning") {
          get().showToast(level, ev.data.message as string);
        }
        break;
      }
    }
  },
}));

if (localStorage.getItem("astrodeck-night") === "1") {
  document.documentElement.classList.add("night");
}
