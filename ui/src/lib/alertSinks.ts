// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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

/** The health block as the dead-man verdict reads it. `last_ok_age_s` (#125) is
 *  the seconds since the monitor last ACCEPTED a ping, null if it never has. It
 *  is typed here, optional, because `AlertHealth` (types.ts) does not carry it
 *  yet and a server older than the field does not send it; both read as "no
 *  accepted ping known", which is the safe side of a green badge. Every
 *  `AlertHealth` is assignable to this, so no caller changes. */
export type DeadmanAwareHealth = Omit<AlertHealth, "deadman"> & {
  deadman: AlertHealth["deadman"] & { last_ok_age_s?: number | null };
};

/** Three missed pings. The server pings every 60 s (alerting.py
 *  DEADMAN_INTERVAL_S); past this an external monitor's grace would have run
 *  out and paged, so a "Pinging" badge would be a claim nothing keeps. */
const DEADMAN_STALE_S = 180;

export function deadmanVerdict(health: DeadmanAwareHealth | null): HealthVerdict {
  const dm = health?.deadman;
  if (!dm?.configured) return { tone: "dim", label: "Not set", detail: "No external monitor configured" };
  if (!dm.healthy) return { tone: "bad", label: "Unreachable", detail: "Monitor URL is not being reached - check it" };
  // `healthy` only means no failure has been WARNED about, which is also true
  // before the first request leaves: a pasted typo reads healthy until the
  // first attempt fails. Only an accepted ping makes this a fact (#125).
  const age = dm.last_ok_age_s;
  if (typeof age !== "number" || !Number.isFinite(age) || age < 0) {
    return { tone: "dim", label: "Waiting", detail: "No ping accepted yet" };
  }
  const detail = `Last ping accepted ${Math.round(age)}s ago`;
  return age < DEADMAN_STALE_S
    ? { tone: "good", label: "Pinging", detail }
    : { tone: "warn", label: "Stale", detail };
}
