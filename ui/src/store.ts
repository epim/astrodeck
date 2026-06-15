import { create } from "zustand";
import type {
  AppConfig,
  FocusEvent,
  GuideStats,
  LogLine,
  NinaHealth,
  PolarState,
  PreviewInfo,
  RigStatus,
  SequencePlan,
  SequenceState,
  SiteInfo,
  Toast,
  ToastLevel,
  ViewName,
  WsPhase,
} from "./types";
import { deriveNinaHealth } from "./lib/health";
import { humanizeLog, humanizeSeqError } from "./lib/humanize";
import { notifyAndBeep, requestNotifyPermission } from "./lib/notify";
import { api } from "./api";

// Re-export ViewName from its canonical home (types.ts) so existing imports
// `import type { ViewName } from "./store"` keep working.
export type { ViewName } from "./types";

// ---------------------------------------------------------------- toast policy
const TOAST_MAX = 3; // hard cap; on phone effectively 1-2
const TTL: Record<ToastLevel, number> = {
  success: 3000,
  info: 4000,
  warning: 6000,
  error: 10000,
};

export type EnqueueInput = {
  level: ToastLevel;
  title: string;
  detail?: string;
  kind?: Toast["kind"];
  ttl?: number;
  action?: Toast["action"];
  source?: string;
};

// --------------------------------------------------------------- confirm host
export interface ConfirmRequest {
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "warn" | "danger";
  mode?: "ok" | "confirm" | "hold";
  resolve: (ok: boolean) => void;
}

// ------------------------------------------------------------ default plan/state
const EMPTY_SEQUENCE: SequenceState = { state: "idle" };
const EMPTY_POLAR: PolarState = {
  state: "idle",
  az_error: 0,
  alt_error: 0,
  total_error: 0,
  progress: 0,
  message: "",
  source: null,
};
const EMPTY_NINA_HEALTH: NinaHealth = { active: false, ageMs: null, state: "na", lastError: null };

const PLAN_KEY = "astrodeck-plan";

function defaultPlan(): SequencePlan {
  return {
    name: "Tonight",
    targets: [],
    guide: true,
    dither_every: 0,
    dither_pixels: 3,
    autofocus_every: 0,
    cool_to: -10,
    cool_timeout_s: 600,
    apply_filter_offsets: false,
    refocus_on_temp_delta_c: 0,
    meridian_flip: true,
    recover_guiding: false,
    hfr_reject_factor: 0,
    park_when_done: false,
    warm_cooler_when_done: false,
  };
}

function loadPlan(): SequencePlan {
  try {
    const raw = localStorage.getItem(PLAN_KEY);
    if (raw) return { ...defaultPlan(), ...(JSON.parse(raw) as Partial<SequencePlan>) };
  } catch {
    /* fall through to default */
  }
  return defaultPlan();
}

interface AppState {
  // --- core view/session ---
  view: ViewName;
  night: boolean;
  status: RigStatus | null;
  preview: PreviewInfo | null;
  focus: FocusEvent | null;
  guide: (GuideStats & { name?: string }) | null;
  sequence: SequenceState;
  polar: PolarState;
  logs: LogLine[];

  // --- config / plan (settings + atlas, reconciled) ---
  config: AppConfig | null;
  plan: SequencePlan; // atlas SSOT; setPlan persists localStorage in the setter
  editorDirty: boolean;
  siteDirty: boolean;
  opticsDirty: boolean;

  // --- site (onboarding) ---
  site: SiteInfo | null;
  equipConnected: boolean; // sticky equipment flag, NOT wsConnected

  // --- reliability transport + toasts (the ONE toast model) ---
  wsPhase: WsPhase;
  wsLastEvent: number;
  telemetryStale: boolean;
  toasts: Toast[];
  logOpen: boolean;
  unseenError: number;
  ninaHealth: NinaHealth;
  notifyEnabled: boolean;

  // --- confirm host (onboarding) ---
  confirm: ConfirmRequest | null;

  // --- backward-compat shims ---
  wsConnected: boolean; // = wsPhase === "up"

  // --- actions: core ---
  setView: (v: ViewName) => void;
  toggleNight: () => void;
  handleEvent: (ev: { type: string; data: Record<string, unknown>; ts: number }) => void;

  // --- actions: config / plan / site ---
  loadConfig: () => Promise<void>;
  setPlan: (p: SequencePlan, dirty?: boolean) => void;
  setSiteDirty: (b: boolean) => void;
  setOpticsDirty: (b: boolean) => void;
  setSite: (s: SiteInfo) => void;

  // --- actions: reliability ---
  setWsPhase: (p: WsPhase) => void;
  noteWsEvent: () => void;
  setTelemetryStale: (v: boolean) => void;
  enqueueToast: (input: EnqueueInput) => void;
  dismissToast: (id: number) => void;
  dismissExpired: () => void;
  openLog: () => void;
  closeLog: () => void;
  reconcileLogs: (history: LogLine[]) => void;
  setNotifyEnabled: (v: boolean) => void;

  // --- actions: confirm ---
  pushConfirm: (req: Omit<ConfirmRequest, "resolve">) => Promise<boolean>;
  resolveConfirm: (ok: boolean) => void;

  // --- actions: compat shims ---
  setWsConnected: (ok: boolean) => void;
  showToast: (level: string, message: string) => void;
}

let toastId = 0;
let linkDownTimer: ReturnType<typeof setTimeout> | null = null;
const LINK_DOWN_ALERT_MS = 30000;

export const useStore = create<AppState>((set, get) => ({
  // --- core ---
  view: "connect",
  night: localStorage.getItem("astrodeck-night") === "1",
  status: null,
  preview: null,
  focus: null,
  guide: null,
  sequence: EMPTY_SEQUENCE,
  polar: EMPTY_POLAR,
  logs: [],

  // --- config / plan ---
  config: null,
  plan: loadPlan(),
  editorDirty: false,
  siteDirty: false,
  opticsDirty: false,

  // --- site ---
  site: null,
  equipConnected: false,

  // --- reliability ---
  wsPhase: "connecting",
  wsLastEvent: Date.now(),
  telemetryStale: false,
  toasts: [],
  logOpen: false,
  unseenError: 0,
  ninaHealth: EMPTY_NINA_HEALTH,
  notifyEnabled: false,

  // --- confirm ---
  confirm: null,

  // --- compat ---
  wsConnected: false,

  // --------------------------------------------------------------- core actions
  setView: (v) => set({ view: v }),

  toggleNight: () => {
    const night = !get().night;
    localStorage.setItem("astrodeck-night", night ? "1" : "0");
    document.documentElement.classList.toggle("night", night);
    set({ night });
  },

  // --------------------------------------------------------- config/plan/site
  loadConfig: async () => {
    try {
      const config = await api.get<AppConfig>("/api/config");
      set({ config });
    } catch {
      /* leave config as-is; UI shows loading/defaults */
    }
  },

  setPlan: (p, dirty = true) => {
    try {
      localStorage.setItem(PLAN_KEY, JSON.stringify(p));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    set({ plan: p, editorDirty: dirty });
  },

  setSiteDirty: (b) => set({ siteDirty: b }),
  setOpticsDirty: (b) => set({ opticsDirty: b }),
  setSite: (s) => set({ site: s }),

  // ----------------------------------------------------------- reliability
  setWsPhase: (p) => {
    const up = p === "up";
    set({ wsPhase: p, wsConnected: up, telemetryStale: up ? get().telemetryStale : false });
    if (p === "down") {
      if (!linkDownTimer) {
        linkDownTimer = setTimeout(() => {
          linkDownTimer = null;
          if (get().wsPhase !== "up") {
            notifyAndBeep(
              get(),
              "AstroDeck disconnected",
              "The display lost the server for 30s — the rig keeps running.",
            );
          }
        }, LINK_DOWN_ALERT_MS);
      }
    } else if (up && linkDownTimer) {
      clearTimeout(linkDownTimer);
      linkDownTimer = null;
    }
  },

  noteWsEvent: () => set({ wsLastEvent: Date.now() }),
  setTelemetryStale: (v) => set({ telemetryStale: v }),

  enqueueToast: (input) =>
    set((s) => {
      const now = Date.now();
      const level = input.level;
      const kind = input.kind ?? "generic";
      let toasts = s.toasts;

      if (kind === "sequence") {
        // exactly one focal sequence toast — replace any prior
        toasts = toasts.filter((t) => t.kind !== "sequence");
      } else {
        const dupe = toasts.find(
          (t) =>
            t.kind === "generic" &&
            t.title === input.title &&
            t.level === level &&
            now - t.createdAt < 5000,
        );
        if (dupe) {
          // coalesce: bump count, leave createdAt UNCHANGED so it still ages out
          const next = toasts.map((t) => (t.id === dupe.id ? { ...t, count: t.count + 1 } : t));
          return { toasts: next };
        }
      }

      const toast: Toast = {
        id: ++toastId,
        level,
        title: input.title,
        detail: input.detail,
        kind,
        createdAt: now,
        ttl: input.ttl ?? TTL[level],
        count: 1,
        action: input.action,
        source: input.source,
      };
      let next = [...toasts, toast];
      if (next.length > TOAST_MAX) {
        // drop oldest dismissible generic first; never drop the focal sequence toast
        const victim = next.find((t) => t.ttl > 0 && t.kind === "generic")?.id ?? next[0].id;
        next = next.filter((t) => t.id !== victim);
      }
      return { toasts: next };
    }),

  dismissToast: (id) =>
    set((s) => {
      const next = s.toasts.filter((t) => t.id !== id);
      return { toasts: next };
    }),

  dismissExpired: () =>
    set((s) => {
      const now = Date.now();
      const next = s.toasts.filter((t) => t.ttl === 0 || now - t.createdAt < t.ttl);
      if (next.length === s.toasts.length) return {} as Partial<AppState>;
      return { toasts: next };
    }),

  openLog: () => set({ logOpen: true, unseenError: 0 }),
  closeLog: () => set({ logOpen: false }),

  reconcileLogs: (history) =>
    set(() => {
      // Refill the drawer from /api/logs after a reconnect; do NOT retroactively
      // inflate unseenError (we can't know which the user already saw).
      const logs = history.slice(-200);
      return { logs };
    }),

  setNotifyEnabled: (v) => {
    set({ notifyEnabled: v });
    if (v) void requestNotifyPermission();
  },

  // -------------------------------------------------------------- confirm host
  pushConfirm: (req) =>
    new Promise<boolean>((resolve) => {
      set({ confirm: { ...req, resolve } });
    }),

  resolveConfirm: (ok) => {
    const c = get().confirm;
    if (c) c.resolve(ok);
    set({ confirm: null });
  },

  // -------------------------------------------------------------- compat shims
  setWsConnected: (ok) => get().setWsPhase(ok ? "up" : "down"),

  showToast: (level, message) =>
    get().enqueueToast({ level: level as ToastLevel, title: humanizeLog(message) }),

  // ------------------------------------------------------------------ events
  handleEvent: (ev) => {
    switch (ev.type) {
      case "status": {
        const status = ev.data as unknown as RigStatus;
        const equipConnected = status.mode !== undefined && status.mode !== "none";
        set({ status, ninaHealth: deriveNinaHealth(status), equipConnected });
        // poll_status carries the full 6-field site; keep store.site fresh while
        // connected so is_default/horizon_min_deg don't go stale until reconnect.
        if (status.site) set({ site: status.site });
        break;
      }
      case "hello": {
        // cold hydration snapshot — backend sends {"data": hub.summary()} with no
        // wrapper, so treat data directly as the summary dict (may carry site / mode).
        const summary = ev.data as unknown as Partial<RigStatus> & {
          site?: SiteInfo;
        };
        if (summary.site) set({ site: summary.site });
        if (summary.mode !== undefined) {
          set({ equipConnected: summary.mode !== "none" });
        }
        break;
      }
      case "config":
        // re-GET, never partial-merge (settings spec C1-H28/C2-12)
        void get().loadConfig();
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
      case "sequence": {
        const seq = ev.data as unknown as SequenceState;
        const prev = get().sequence.state;
        set({ sequence: seq });
        if (seq.state === "error" && prev !== "error") {
          get().enqueueToast({
            level: "error",
            kind: "sequence",
            ttl: 0,
            title: "Sequence failed",
            detail: humanizeSeqError(seq.detail),
            action: { label: "View log", kind: "openLog" },
          });
          notifyAndBeep(get(), "Sequence failed", humanizeSeqError(seq.detail));
        } else if (seq.state === "complete" && prev !== "complete") {
          notifyAndBeep(
            get(),
            "Sequence complete",
            `${seq.progress?.frames_done ?? 0} frames captured`,
          );
          // no toast — the green panel is enough
        }
        break;
      }
      case "polar":
        set({ polar: ev.data as unknown as PolarState });
        break;
      case "log": {
        const line = ev as unknown as LogLine;
        const level = (ev.data.level as string) ?? "info";
        const source = (ev.data.source as string) ?? "";
        set((s) => ({
          logs: [...s.logs.slice(-199), line],
          unseenError: s.unseenError + (!s.logOpen && level === "error" ? 1 : 0),
        }));
        // Errors → toast, EXCEPT sequence-fatal (handled by the sequence frame so
        // we don't double-surface). Warnings never toast.
        if (level === "error" && source !== "sequence") {
          get().enqueueToast({
            level: "error",
            title: humanizeLog({ message: ev.data.message as string, source }),
            source,
          });
        }
        break;
      }
    }
  },
}));

if (localStorage.getItem("astrodeck-night") === "1") {
  document.documentElement.classList.add("night");
}

// ============================================================================
// Narrow selector hooks (reliability §13). Subscribing to a single slice means a
// guide tick (which mutates only `guide`) re-renders only guide consumers, not
// the whole tree. Prefer these over a broad useStore() in components.
// ============================================================================
export const useView = () => useStore((s) => s.view);
export const useNight = () => useStore((s) => s.night);
export const useStatus = () => useStore((s) => s.status);
export const useSequence = () => useStore((s) => s.sequence);
export const useGuide = () => useStore((s) => s.guide);
export const useFocus = () => useStore((s) => s.focus);
export const usePreview = () => useStore((s) => s.preview);
export const usePolar = () => useStore((s) => s.polar);
export const useLogs = () => useStore((s) => s.logs);

export const useToasts = () => useStore((s) => s.toasts);
export const useWsPhase = () => useStore((s) => s.wsPhase);
export const useWsConnected = () => useStore((s) => s.wsConnected);
export const useTelemetryStale = () => useStore((s) => s.telemetryStale);
export const useLinkDown = () => useStore((s) => s.wsPhase !== "up");
export const useNinaHealth = () => useStore((s) => s.ninaHealth);
export const useLogOpen = () => useStore((s) => s.logOpen);
export const useUnseenError = () => useStore((s) => s.unseenError);
export const useNotifyEnabled = () => useStore((s) => s.notifyEnabled);

export const useConfig = () => useStore((s) => s.config);
export const usePlan = () => useStore((s) => s.plan);
export const useSite = () => useStore((s) => s.site);
export const useEquipConnected = () => useStore((s) => s.equipConnected);
export const useConfirm = () => useStore((s) => s.confirm);
