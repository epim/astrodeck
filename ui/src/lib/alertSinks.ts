// lib/alertSinks.ts - pure logic behind the Settings → Alerts panel (PRO-9).
// Load-bearing: per-kind draft defaults, per-kind validation, and the health/
// deadman verdict derivation. The panel (AlertsPanel.tsx) binds to these
// helpers directly - no decision logic is duplicated in the component.
import type { AlertSink, AlertSinkInput, AlertHealth } from "../types";

export type AlertKind = AlertSink["kind"];
export const ALERT_KINDS: AlertKind[] = ["ntfy", "webhook", "telegram", "discord", "slack", "email"];
export const ALL_EVENTS = ["run_start", "run_end", "safety", "reconnect", "warning", "error"] as const;

export function kindLabel(kind: AlertKind): string {
  switch (kind) {
    case "ntfy":
      return "ntfy";
    case "webhook":
      return "Webhook";
    case "telegram":
      return "Telegram";
    case "discord":
      return "Discord";
    case "slack":
      return "Slack";
    case "email":
      return "Email (SMTP)";
  }
}

/** A fresh draft for `kind` with sensible defaults (events preselected, port 587…). */
export function defaultDraft(kind: AlertKind, id: string): AlertSinkInput {
  return {
    id,
    kind,
    enabled: true,
    url: "",
    chat_id: "",
    min_level: "warning",
    events: ["run_start", "run_end", "safety", "error"],
    verified: false,
    heartbeat_min: 0,
    token: "",
    smtp_host: "",
    smtp_port: 587,
    smtp_user: "",
    smtp_from: "",
    smtp_to: "",
    smtp_starttls: true,
  };
}

const isHttp = (u: string) => /^https?:\/\/.+/i.test(u.trim());

/** First validation error for a draft, or null if send-able. `tokenConfigured`
 *  = the secret is already stored server-side (so a blank secret is OK on edit). */
export function validateDraft(d: AlertSinkInput, tokenConfigured: boolean): string | null {
  const secret = (d.token ?? "").trim();
  const haveSecret = secret !== "" || tokenConfigured;
  switch (d.kind) {
    case "ntfy":
    case "webhook":
      if (!isHttp(d.url)) return "Enter an http(s) URL";
      return null;
    case "telegram":
      if (!(d.chat_id ?? "").trim()) return "Telegram needs a chat id";
      if (!haveSecret) return "Telegram needs a bot token";
      return null;
    case "discord":
    case "slack":
      if (secret !== "" && !isHttp(secret)) return "Webhook must be an http(s) URL";
      if (!haveSecret) return `Paste the ${kindLabel(d.kind)} webhook URL`;
      return null;
    case "email": {
      if (!(d.smtp_host ?? "").trim()) return "SMTP host is required";
      const port = d.smtp_port ?? 0;
      if (!(port >= 1 && port <= 65535)) return "SMTP port must be 1-65535";
      if (!(d.smtp_from ?? "").trim()) return "From address is required";
      if (!(d.smtp_to ?? "").trim()) return "At least one recipient is required";
      return null;   // auth optional (open relays exist)
    }
  }
}

export interface HealthVerdict { tone: "good" | "warn" | "bad" | "dim"; label: string; detail: string; }

export function deriveSinkHealth(sink: AlertSink, health: AlertHealth | null): HealthVerdict {
  if (!sink.enabled) return { tone: "dim", label: "Disabled", detail: "Not receiving alerts" };
  const queued = health?.undelivered_by_sink[sink.id] ?? 0;
  if (queued > 0) return { tone: "bad", label: `${queued} queued`, detail: "Delivery is failing - retrying" };
  if (sink.verified) return { tone: "good", label: "Verified", detail: "Last test delivered" };
  return { tone: "warn", label: "Untested", detail: "Send a test to verify delivery" };
}

export function deadmanVerdict(health: AlertHealth | null): HealthVerdict {
  const dm = health?.deadman;
  if (!dm?.configured) return { tone: "dim", label: "Not set", detail: "No external monitor configured" };
  return dm.healthy
    ? { tone: "good", label: "Pinging", detail: "External monitor is being pinged" }
    : { tone: "bad", label: "Unreachable", detail: "Monitor URL is not being reached - check it" };
}
