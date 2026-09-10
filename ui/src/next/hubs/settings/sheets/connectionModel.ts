// connectionModel.ts - which origin this browser tab is on, what the other one
// would be, and whether it is even addressable from here.
//
// WHY THIS IS A MODEL AND NOT A RADIO GROUP. The design's Connection screen is
// three radio buttons plus an AUTO-SWITCH flag, and `prototype.md` (c) says out
// loud that nothing reads the flag: "there is no timer-driven reconnection code
// in this prototype ... nothing reads `connAuto` to actually change `connMode`
// ... This is a gap an implementer must build". The gap cannot be built the way
// the radios imply, because DIRECT vs RELAY is not a setting this app applies -
// it is which origin served the page. `lib/base.ts` reads that off the pathname:
// a "/h/<home_id>" prefix means the relay tunnelled this tab, "" means the LAN
// origin (or loopback). Moving between them is a NAVIGATION, so every control
// here resolves to a link, never a mode switch.
//
// WHAT WAVE S3 ADDED. `GET /api/remote/status` (view.status, so every role can
// read it) answers three things nothing could poll before: whether the tunnel is
// connected, the relay hostname and home id (which a non-admin could never read
// out of `config.auth`), and `via` - how THIS request arrived. `via` and the
// pathname answer the same question from opposite ends; when they disagree the
// rig's answer wins and the disagreement becomes a note the user can see, rather
// than a silent correction that would leave two screens quietly contradicting
// each other.
//
// Pure: no React, no store, no fetch. The one import that touches the browser is
// `deriveBase`, and it is imported rather than re-implemented so this model and
// every API URL in the app can never disagree about where the mount point is.

import { deriveBase } from "../../../../lib/base";
import type { RemoteStatus, WsPhase } from "../../../../types";
import { fmtClock, fmtDuration } from "../../../lib/format";

// ------------------------------------------------------------- the preference

export const CONN_PREF_KEY = "astrodeck-next-conn-pref";

export interface ConnPref {
  /** The origin the user was last on when they set the preference. */
  prefer: "direct" | "relay";
  /** Offer the other origin once the link has been down for 30 s. */
  auto: boolean;
  /** The last host this device reached the rig DIRECTLY on. Remembered because
   *  a phone sitting on the relay has no other way to learn the LAN address -
   *  nothing on the wire carries it. */
  lanHost: string | null;
  /** The SCHEME that host answered on, remembered alongside it. The relay is
   *  always HTTPS and the LAN is usually plain HTTP (README "Platform": the two
   *  paths are `https://<relay>/h/{home_id}/` and `http://<rig>:8800`), so
   *  building the LAN link out of the CURRENT origin's protocol - which is what
   *  the plan's formula does - hands a phone on the relay an `https://192.168...`
   *  that cannot connect. Null until this device has been there. */
  lanProtocol: string | null;
}

export const DEFAULT_CONN_PREF: ConnPref = {
  prefer: "direct",
  auto: true,
  lanHost: null,
  lanProtocol: null,
};

/** The 30 s the design already commits to (`INC.link.next`: "Retrying direct
 *  Wi-Fi every 5 s; falls back to the relay after 30 s"). The 5 s retry is
 *  `ws.ts`'s own reconnect cadence and is NOT re-implemented here; only the
 *  30 s offer is new. */
export const OFFER_AFTER_MS = 30_000;

export function readConnPref(): ConnPref {
  try {
    const raw = localStorage.getItem(CONN_PREF_KEY);
    if (!raw) return { ...DEFAULT_CONN_PREF };
    const p = JSON.parse(raw) as Partial<ConnPref>;
    return {
      prefer: p.prefer === "relay" ? "relay" : "direct",
      auto: p.auto !== false,
      lanHost: typeof p.lanHost === "string" && p.lanHost ? p.lanHost : null,
      lanProtocol:
        p.lanProtocol === "http:" || p.lanProtocol === "https:" ? p.lanProtocol : null,
    };
  } catch {
    // Private mode, a cleared store, a browser blocking site data: the sheet
    // must render correctly with nothing stored.
    return { ...DEFAULT_CONN_PREF };
  }
}

export function writeConnPref(p: ConnPref): void {
  try {
    localStorage.setItem(CONN_PREF_KEY, JSON.stringify(p));
  } catch {
    /* nothing to do: the preference is a convenience, not state the rig needs */
  }
}

// ------------------------------------------------------------------ the model

export type ReachId = "direct" | "relay";

export interface ReachAction {
  label: string;
  /** null when there is no address to open - the button is then honest-disabled
   *  carrying `lockedReason`, never hidden. */
  url: string | null;
  lockedReason: string | null;
}

export interface ReachCard {
  id: ReachId;
  title: string;
  /** True for the card you are already on; its action is absent, because a
   *  button that opens the page you are looking at is a control that cannot be
   *  right. */
  here: boolean;
  status: string;
  statusTone: "good" | "warn" | "bad" | "dim";
  address: string | null;
  description: string;
  action: ReachAction | null;
}

export interface ConnectionInputs {
  /** `window.location.pathname`. */
  pathname: string;
  /** `window.location.host` - hostname and port, the address of this origin. */
  host: string;
  /** `window.location.protocol`, e.g. "http:". */
  protocol: string;
  /** `window.location.hash`, leading "#" included; carried across an origin
   *  change so the user lands back on the screen they were reading. */
  hash: string;
  /** `window.isSecureContext` - whether the camera, compass and geolocation
   *  APIs will work at all on this address. */
  secureContext: boolean;
  /** `GET /api/remote/status`, or null while it is in flight or has failed. */
  remote: RemoteStatus | null;
  pref: ConnPref;
  wsPhase: WsPhase;
  /** How long the WebSocket has been away from "up", or null while it is up. */
  downMs: number | null;
  /** `/healthz`'s version, or null while it is in flight. An engine version is
   *  never guessed: absent means the clause is dropped. */
  engineVersion: string | null;
  /** `status.disk.free_gb`, or null when no status has arrived. */
  freeGb: number | null;
}

export interface ConnectionModel {
  here: ReachId;
  direct: ReachCard;
  relay: ReachCard;
  /** Facts the cards themselves cannot carry - today only the `via`-vs-pathname
   *  disagreement. Rendered, never swallowed. */
  notes: string[];
  /** The rig-computer card. Every field is measured; the design's hostname,
   *  hardware model, uptime and "owner key paired 12 Aug" are absent because
   *  nothing on the wire carries them. */
  rigState: string;
  rigStateTone: "good" | "warn" | "bad";
  rigMode: string;
  rigLine: string;
  rigSub: string | null;
  transfers: string;
  /** The AUTO-SWITCH offer. It OFFERS; it does not switch - nothing in the
   *  client can silently move the browser between origins without discarding
   *  in-flight work and possibly the session. */
  offer: { text: string; label: string; url: string } | null;
  pairing: { url: string | null; remoteReason: string | null };
  secure: string[];
}

const DIRECT_DESC =
  "Join the rig's hotspot or the same home network. Full speed, no internet " +
  "needed. Over Wi-Fi or the observatory's Ethernet - the same address either way.";

const RELAY_DESC =
  "Reach the rig from the couch, the pub or the car. Mount, cooler and flows " +
  "behave the same; big files trickle.";

export const NO_LAN_ADDRESS =
  "this device has not reached the rig directly yet, so there is no LAN address to remember";

export const NO_RELAY_ADDRESS =
  "no relay address is paired with this rig, so there is nowhere to open";

export const PAIR_OVER_RELAY =
  "pairing has to be done on the rig's own network, not over the relay";

export function connectionModel(inp: ConnectionInputs): ConnectionModel {
  const base = deriveBase(inp.pathname);
  const fromPath: ReachId = base === "" ? "direct" : "relay";
  // `via` wins. It is stamped by the relay client onto the ASGI scope of the
  // request in your hand; the pathname is a guess made from the mount point.
  const here: ReachId = inp.remote ? inp.remote.via : fromPath;

  const notes: string[] = [];
  if (inp.remote && inp.remote.via !== fromPath) {
    notes.push(
      `This address reads as ${fromPath.toUpperCase()}, but the rig says the request ` +
      `arrived over the ${inp.remote.via}. The rig's answer is the one used here.`,
    );
  }

  const hash = inp.hash ? (inp.hash.startsWith("#") ? inp.hash : `#${inp.hash}`) : "";

  const homeId = base !== "" ? base.replace("/h/", "") : (inp.remote?.home_id ?? null);
  const relayHost = here === "relay" ? inp.host : (inp.remote?.relay_host ?? null);
  const relayAddr = relayHost && homeId ? `${relayHost}/h/${homeId}/` : null;
  const relayUrl = relayAddr ? `https://${relayAddr}` : null;

  const lanHost = here === "direct" ? inp.host : inp.pref.lanHost;
  // The LAN's own scheme, never the current origin's: a phone on the HTTPS relay
  // building `https://192.168.4.1/` would hand the user a link that cannot
  // connect. `http:` is the fallback because that is what the rig serves without
  // an nginx certificate, which is the default deployment.
  const lanProtocol = here === "direct" ? inp.protocol : (inp.pref.lanProtocol ?? "http:");
  const directUrl = lanHost ? `${lanProtocol}//${lanHost}/${hash}` : null;

  const direct: ReachCard = {
    id: "direct",
    title: "DIRECT · RIG WI-FI",
    here: here === "direct",
    status: here === "direct" ? "you are here" : lanHost ? "open" : "no address yet",
    statusTone: here === "direct" ? "good" : lanHost ? "dim" : "warn",
    address: lanHost,
    description: DIRECT_DESC,
    action:
      here === "direct"
        ? null
        : {
            label: "OPEN DIRECT",
            url: directUrl,
            lockedReason: directUrl ? null : NO_LAN_ADDRESS,
          },
  };

  let relayStatus: string;
  let relayTone: ReachCard["statusTone"];
  if (here === "relay") {
    relayStatus = "you are here";
    relayTone = "good";
  } else if (inp.remote?.enabled && inp.remote.connected) {
    relayStatus = "connected";
    relayTone = "good";
  } else if (inp.remote?.enabled) {
    relayStatus = "not connected";
    relayTone = "warn";
  } else if (relayUrl) {
    relayStatus = "configured";
    relayTone = "dim";
  } else {
    relayStatus = "not set up";
    relayTone = "dim";
  }

  let relayDesc = RELAY_DESC;
  if (!relayUrl) {
    relayDesc += " An admin pairs the rig with a relay; this device has no relay address.";
  }
  if (inp.remote?.connected && inp.remote.since_unix != null) {
    relayDesc += ` The tunnel has been up since ${fmtClock(inp.remote.since_unix * 1000)}.`;
  }
  if (inp.remote && !inp.remote.connected && inp.remote.last_error) {
    relayDesc += ` Last error from the tunnel: ${inp.remote.last_error}.`;
  }

  const relay: ReachCard = {
    id: "relay",
    title: "RELAY · ANYWHERE",
    here: here === "relay",
    status: relayStatus,
    statusTone: relayTone,
    address: relayAddr,
    description: relayDesc,
    action:
      here === "relay"
        ? null
        : {
            label: "OPEN VIA RELAY",
            url: relayUrl ? `${relayUrl}${hash}` : null,
            lockedReason: relayUrl ? null : NO_RELAY_ADDRESS,
          },
  };

  const rigState =
    inp.wsPhase === "up" ? "online" : inp.wsPhase === "down" ? "unreachable" : "reconnecting";
  const rigStateTone: ConnectionModel["rigStateTone"] =
    inp.wsPhase === "up" ? "good" : inp.wsPhase === "down" ? "bad" : "warn";

  const rigLine = [inp.host, inp.engineVersion ? `engine ${inp.engineVersion}` : null]
    .filter(Boolean)
    .join(" · ");

  const other = here === "direct" ? relay.action : direct.action;
  const offer =
    inp.pref.auto && inp.downMs != null && inp.downMs >= OFFER_AFTER_MS && other?.url
      ? {
          text:
            `The rig has not answered for ${fmtDuration(inp.downMs / 1000)}. ` +
            `Open it ${here === "direct" ? "via the relay" : "on the LAN address"}.`,
          label: other.label,
          url: other.url,
        }
      : null;

  const secure: string[] = inp.secureContext
    ? ["This connection is secure, so the AR camera, the compass and Use my location all work."]
    : [
        "AR camera and gyro need a secure connection - set up in Connection.",
        "The relay is HTTPS and works today. On a plain-HTTP LAN address the browser " +
          "refuses the camera, the compass and the location APIs; the finder falls back " +
          "to MAP mode with tap-to-aim and everything else works.",
      ];
  secure.push(
    "To use the finder's camera on the LAN with no internet, the rig needs a certificate " +
      "for its own hostname - either issued through the relay or a local CA installed on " +
      "this phone during pairing. Until then, use the relay for AR.",
  );

  return {
    here,
    direct,
    relay,
    notes,
    rigState,
    rigStateTone,
    rigMode: here.toUpperCase(),
    rigLine,
    rigSub: inp.freeGb != null ? `${inp.freeGb.toFixed(0)} GB free` : null,
    transfers:
      here === "direct"
        ? "full speed on the local network"
        : "through the relay - a 2 GB FITS set takes a while",
    offer,
    pairing: {
      url: relayUrl,
      remoteReason: here === "relay" ? PAIR_OVER_RELAY : null,
    },
    secure,
  };
}

// --------------------------------------------------------------------- TEST

export interface ConnTestResult {
  /** `/healthz` answered. */
  ok: boolean;
  /** Round-trip milliseconds for `/healthz`, or null when it did not answer.
   *  A LATENCY, never a throughput: nothing this button does moves enough bytes
   *  to measure MB/s, and the design's "38 MB/s" is simulated. */
  ms: number | null;
  /** `/api/me` answered, i.e. the SESSION works on this origin. */
  signedIn: boolean;
  email: string | null;
  role: string | null;
  error: string | null;
}

/** Names both halves of the test, because the interesting failure is the one in
 *  the middle: the rig answers and the browser is not signed in HERE, which is
 *  exactly what a relay/LAN switch produces and the sentence that makes it
 *  diagnosable. */
export function testLine(r: ConnTestResult): string {
  if (!r.ok) return r.error ?? "the rig did not answer on this address";
  if (!r.signedIn) {
    return "the rig answered, but this browser is not signed in on this address - sign in again here";
  }
  return `reachable in ${r.ms} ms · signed in as ${r.email ?? "this device"} (${r.role ?? "unknown role"})`;
}
