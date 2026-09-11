// ConnectionSheet.tsx - Settings > RIG > Connection (plan section C.4).
//
// THE ONE HONESTY FIX ON THIS SCREEN. The design draws three radio buttons
// (DIRECT / RELAY / HOME LAN) and an AUTO-SWITCH toggle. A radio button implies
// the app applies a setting; this app cannot, because DIRECT vs RELAY is which
// ORIGIN served the page, and only a navigation changes that. So each card
// carries a LINK to the other origin, the card you are already on says "you are
// here" and has no button at all, and AUTO-SWITCH offers rather than switches.
// See `connectionModel.ts` for the derivation and the two quotes it rests on.
//
// HOME LAN . ETHERNET is dropped as a third option (plan F.2): nothing in the
// product distinguishes it from DIRECT - it is the same origin over a different
// cable - and a radio that cannot be wrong is not a control. Its meaning is
// folded into card 1's description.
//
// WHAT IS DELIBERATELY ABSENT. The design's rig card reads "astrodeck-pi .
// Orange Pi 5 . engine 1.9.2 / up 3 d 4 h . 412 GB free . owner key paired 12
// Aug". Nothing on the wire carries a hostname, a hardware model, an uptime or a
// pairing date - `DiskInfo` is `{free_gb, low, critical}` and that is the lot -
// so those clauses are omitted rather than filled with a dash where a fact was
// promised. The same rule kills the design's "38 MB/s": no MB/s figure appears
// anywhere, because nothing here transfers enough bytes to measure one.

import { useCallback, useEffect, useRef, useState, type CSSProperties, type JSX } from "react";
import {
  ActionButton,
  BannerCard,
  Card,
  Label,
  Mono,
  Sheet,
  Switch,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { QR_MAX_BYTES, qrByteLength } from "../../../lib/qr";
import { QrCode } from "./QrCode";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { useConfig, usePrincipal, useStatus, useWsPhase } from "../../../../store";
import { usePrincipalRole } from "../../../../lib/caps";
import { getHealth, getMe, getRemoteStatus } from "../../../../api/backends";
import type { RemoteStatus } from "../../../../types";
import { noteRemoteStatus } from "../../../lib/relay";
import {
  connectionModel,
  readConnPref,
  testLine,
  writeConnPref,
  type ConnPref,
  type ConnTestResult,
  type ReachCard,
} from "./connectionModel";

/** How often the down-clock re-renders while the link is away. Five seconds is
 *  enough resolution for a 30 s threshold and cheap enough to leave running. */
const TICK_MS = 5000;

/** The drawn size of the pairing code. 200 px puts a version 3 symbol's modules
 *  at about 5 px each, which a phone camera resolves from arm's length. */
const QR_PX = 200;

const PARA: CSSProperties = {
  margin: "6px 0 0",
  fontSize: 11.5,
  lineHeight: 1.45,
  color: "var(--text-faint)",
};

function ReachCardView({
  card,
  onOpen,
  onExplain,
}: {
  card: ReachCard;
  onOpen: (url: string) => void;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const action = card.action;
  return (
    <Card data-testid={`conn-card-${card.id}`} tone={card.here ? "accent" : "default"}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <Label>{card.title}</Label>
        <span data-testid={`conn-status-${card.id}`}>
          <Mono tone={card.statusTone} size={10.5}>{card.status}</Mono>
        </span>
      </div>
      {card.address != null && (
        <div style={{ marginTop: 4, overflow: "hidden", textOverflow: "ellipsis" }}>
          <Mono size={10.5}>{card.address}</Mono>
        </div>
      )}
      <p style={PARA}>{card.description}</p>
      {action && (
        <div style={{ marginTop: 8 }}>
          <ActionButton
            kind="secondary"
            onPress={() => { if (action.url) onOpen(action.url); }}
            lockedReason={action.lockedReason}
            onExplain={onExplain}
            data-testid={`conn-open-${card.id}`}
          >
            {action.label}
          </ActionButton>
        </div>
      )}
    </Card>
  );
}

export function ConnectionSheet(): JSX.Element {
  const wsPhase = useWsPhase();
  const status = useStatus();
  const config = useConfig();
  const principal = usePrincipal();
  const role = usePrincipalRole();

  const [pref, setPref] = useState<ConnPref>(() => readConnPref());
  const [remote, setRemote] = useState<RemoteStatus | null>(null);
  const [engineVersion, setEngineVersion] = useState<string | null>(null);
  const [result, setResult] = useState<ConnTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [copied, setCopied] = useState(false);

  // The down-clock. `wsPhase` is the store's own (store.ts:717-725); this only
  // measures how long it has been away, because the 30 s offer is a claim about
  // duration and a phase word carries none.
  const downSince = useRef<number | null>(null);
  const [downMs, setDownMs] = useState<number | null>(null);
  useEffect(() => {
    if (wsPhase === "up") {
      downSince.current = null;
      setDownMs(null);
      return;
    }
    if (downSince.current == null) downSince.current = Date.now();
    setDownMs(Date.now() - downSince.current);
    const t = setInterval(() => {
      if (downSince.current != null) setDownMs(Date.now() - downSince.current);
    }, TICK_MS);
    return () => clearInterval(t);
  }, [wsPhase]);

  // Both reads are view.status or unauthenticated, so every role issues them: a
  // viewer looking at the rig through the relay is exactly the caller who needs
  // to know whether the link is up.
  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const h = await getHealth();
        if (live) setEngineVersion(h.version || null);
      } catch {
        /* leave the engine clause off rather than printing a dash */
      }
    })();
    void (async () => {
      try {
        const r = await getRemoteStatus();
        // The rig's own `via`, handed to the shared derivation so every
        // LAN-fenced control elsewhere in the app locks off the same answer
        // this screen draws its cards from (`next/lib/relay.ts`).
        noteRemoteStatus(r);
        if (live) setRemote(r);
      } catch {
        /* an older rig has no such route: the model falls back to the pathname */
      }
    })();
    return () => { live = false; };
  }, []);

  const loc = typeof window === "undefined" ? null : window.location;
  const host = loc?.host ?? "";
  const model = connectionModel({
    pathname: loc?.pathname ?? "/",
    host,
    protocol: loc?.protocol ?? "http:",
    hash: loc?.hash ?? "",
    secureContext: typeof window === "undefined" ? false : !!window.isSecureContext,
    remote,
    pref,
    wsPhase,
    downMs,
    engineVersion,
    freeGb: status?.disk?.free_gb ?? null,
  });
  const here = model.here;
  const offer = model.offer;

  // Remember the LAN address the moment we are ON it. Without this a phone that
  // walks out of range has no way back: nothing on the wire carries the rig's
  // LAN host, so the only record of it is the one this browser makes.
  const protocol = loc?.protocol ?? "http:";
  useEffect(() => {
    if (here !== "direct" || !host) return;
    if (pref.lanHost === host && pref.lanProtocol === protocol) return;
    const next = { ...pref, lanHost: host, lanProtocol: protocol };
    writeConnPref(next);
    setPref(next);
  }, [here, host, protocol, pref]);

  const pairLock = useLock({ cap: "admin.users" });
  const onExplain = pairLock.onExplain;
  const pairReasons = [pairLock.lockedReason, model.pairing.remoteReason].filter(
    (r): r is string => !!r,
  );

  const open = useCallback((url: string) => {
    window.location.assign(url);
  }, []);

  const setAuto = (auto: boolean) => {
    const next: ConnPref = { ...pref, auto, prefer: here };
    setPref(next);
    writeConnPref(next);
  };

  const runTest = useCallback(async () => {
    if (testing) return;
    setTesting(true);
    const t0 = performance.now();
    try {
      await getHealth();
      const ms = Math.round(performance.now() - t0);
      try {
        const me = await getMe();
        setResult({ ok: true, ms, signedIn: true, email: me.email, role: me.role, error: null });
      } catch {
        // /healthz answered and /api/me did not: the rig is reachable and this
        // browser has no session ON THIS ORIGIN. That is exactly what a move
        // between the LAN and the relay produces, and naming both halves is
        // what makes it diagnosable instead of "connection failed".
        setResult({ ok: true, ms, signedIn: false, email: null, role: null, error: null });
      }
    } catch (e) {
      setResult({
        ok: false,
        ms: null,
        signedIn: false,
        email: null,
        role: null,
        error: e instanceof Error ? e.message : null,
      });
    } finally {
      setTesting(false);
    }
  }, [testing]);

  const pairUrl = model.pairing.url;
  // The code is drawn only when there is an address AND the encoder can hold
  // it. Asking here rather than letting `QrCode` decide keeps the caption from
  // standing alone under nothing: a relay URL is 30-60 bytes and the ceiling is
  // 213, so this is a guard against a future relay scheme, not a live case.
  const qrUrl = pairUrl && qrByteLength(pairUrl) <= QR_MAX_BYTES ? pairUrl : null;
  const copyLink = useCallback(() => {
    if (!pairUrl) return;
    try {
      const n = navigator as Navigator & { clipboard?: { writeText(t: string): Promise<void> } };
      if (n.clipboard?.writeText) {
        void n.clipboard.writeText(pairUrl);
      } else {
        // No Clipboard API. A plain-HTTP LAN origin is not a secure context, so
        // here that is the COMMON case rather than the exotic one: select the
        // text instead, so a long-press copy has something to grab.
        const el = document.getElementById("nx-conn-pair-url");
        const sel = window.getSelection();
        if (el && sel) {
          const range = document.createRange();
          range.selectNodeContents(el);
          sel.removeAllRanges();
          sel.addRange(range);
        }
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* the URL is on screen and selectable either way */
    }
  }, [pairUrl]);

  // "signed in as", following AccountPanel's identity line: the email, else the
  // open-LAN sentence, else not signed in.
  const methods = config?.auth?.methods ?? [];
  const openLan = !config?.auth || methods.length === 0;
  const identity =
    principal?.email ?? (openLan ? "Local network - no sign-in required" : "Not signed in");
  const loopback = /^(127\.0\.0\.1|\[::1\]|localhost)(:|$)/.test(host);
  const trustLoopback = config?.auth?.trust_loopback !== false;

  return (
    <Sheet
      title="CONNECTION"
      sub="the rig runs AstroDeck on its own computer"
      icon={<NxIcon name="rig" size={18} />}
      live={`${model.rigState} · ${model.rigMode}`}
      onBack={() => nav.back()}
      backLabel="SETTINGS"
      data-testid="sheet-connection"
    >
      {offer && (
        <BannerCard
          tone="warn"
          text={offer.text}
          cta={{ label: offer.label, onPress: () => open(offer.url) }}
          data-testid="conn-offer"
        />
      )}

      {model.notes.map((n) => (
        <BannerCard key={n} tone="info" text={n} data-testid="conn-note" />
      ))}

      {/* ------------------------------------------------------ rig computer */}
      <Card data-testid="conn-rig-card">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
          <Label>RIG COMPUTER</Label>
          <span data-testid="conn-rig-state">
            <Mono tone={model.rigStateTone} size={10.5}>
              {model.rigState} · {model.rigMode}
            </Mono>
          </span>
        </div>
        <div style={{ marginTop: 4 }}>
          <Mono size={12}>{model.rigLine}</Mono>
        </div>
        {model.rigSub && (
          <div style={{ marginTop: 2 }}>
            <Mono size={10.5} tone="dim">{model.rigSub}</Mono>
          </div>
        )}
        <div style={{ marginTop: 6 }} data-testid="conn-signed-in">
          <Mono size={10.5} tone="dim">signed in as {identity} · {role}</Mono>
        </div>
        {loopback && openLan && trustLoopback && (
          <p style={PARA} data-testid="conn-loopback">
            This browser is on the rig&apos;s own machine and no sign-in method is enabled, so it
            resolves to admin without signing in. Turn that off under Sign-in methods to check
            operator and viewer gating from here.
          </p>
        )}
      </Card>

      {/* ---------------------------------------- how this phone reaches it */}
      <Label>HOW THIS PHONE REACHES IT</Label>
      <ReachCardView card={model.direct} onOpen={open} onExplain={onExplain} />
      <ReachCardView card={model.relay} onOpen={open} onExplain={onExplain} />

      {/* ------------------------------------------------------ auto-switch */}
      <Card>
        <Switch
          checked={pref.auto}
          onChange={setAuto}
          label="AUTO-SWITCH"
          note="prefer direct when the rig is in range, and offer the relay when the link has been down for 30 seconds"
          data-testid="conn-autoswitch"
        />
        <p style={PARA}>
          It offers; it does not switch. Moving this browser between addresses on its own would
          discard whatever you were editing and can land on a page that needs a fresh sign-in.
        </p>
        <div style={{ marginTop: 10 }}>
          <Label>FILE TRANSFERS</Label>
          <span data-testid="conn-transfers">
            <Mono size={10.5} tone="dim">
              {model.transfers}
              {result?.ok && result.ms != null
                ? ` · ${result.ms} ms round trip on the last check`
                : ""}
            </Mono>
          </span>
        </div>
      </Card>

      {/* ------------------------------------------------- pair another rig */}
      <Card data-testid="conn-pair">
        <Label>PAIR ANOTHER RIG · LINK</Label>
        <p style={PARA}>
          Open this address on the other device to reach this rig from anywhere. It carries the
          relay host and the home id only - never the device token that registered this home.
        </p>
        {/* Two columns on a tablet, stacked on a phone. The left basis plus the
            code plus the gap is 384 px, so the code drops under the link at
            about 380 px of card width - which is every phone. */}
        <div style={{ display: "flex", gap: 14, marginTop: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
          <div style={{ flex: "1 1 170px", minWidth: 0 }}>
            <div
              id="nx-conn-pair-url"
              data-testid="conn-pair-url"
              style={{
                userSelect: "all",
                wordBreak: "break-all",
                border: "1px solid var(--line)",
                borderRadius: 10,
                padding: "8px 10px",
                background: "var(--bg)",
              }}
            >
              <Mono size={11}>{pairUrl ?? "no relay address is paired with this rig"}</Mono>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
              <ActionButton
                kind="secondary"
                glyph={<NxIcon name="share" size={15} />}
                onPress={copyLink}
                lockedReason={pairUrl ? null : "there is no relay address to copy"}
                onExplain={onExplain}
                data-testid="conn-copy"
              >
                {copied ? "COPIED" : "COPY LINK"}
              </ActionButton>
              <ActionButton
                kind="ghost"
                glyph={<NxIcon name="plus" size={15} />}
                onPress={() => nav.sheet("authMethods")}
                lockedReason={pairReasons[0] ?? null}
                onExplain={onExplain}
                data-testid="conn-pair-new"
              >
                PAIR A NEW RIG
              </ActionButton>
            </div>
            {pairReasons.length > 0 && (
              <ul style={{ margin: "8px 0 0", paddingLeft: 16, fontSize: 11, color: "var(--text-faint)" }}>
                {pairReasons.map((r) => (
                  <li key={r} data-testid="conn-pair-reason">{r}</li>
                ))}
              </ul>
            )}
          </div>
          {qrUrl && (
            <div style={{ flex: "0 0 auto", maxWidth: QR_PX }}>
              <Label>PAIR ANOTHER RIG · QR</Label>
              <div style={{ marginTop: 6 }}>
                <QrCode text={qrUrl} px={QR_PX} label={`QR code for ${qrUrl}`} />
              </div>
              <p style={{ ...PARA, maxWidth: QR_PX }}>
                Scan it with the other phone&apos;s camera - it opens the same address as the link.
              </p>
            </div>
          )}
        </div>
      </Card>

      {/* -------------------------------------------------------------- test */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
          <Label>TEST THIS ADDRESS</Label>
          <ActionButton
            kind="secondary"
            onPress={() => void runTest()}
            busy={testing}
            data-testid="conn-test"
          >
            TEST
          </ActionButton>
        </div>
        <p style={PARA}>
          Asks the rig twice: once for its health, which needs no sign-in, and once for who you
          are, which does. Both answers matter - the second is the one that fails after a move
          between addresses.
        </p>
        {result && (
          <div style={{ marginTop: 8 }} data-testid="conn-test-result">
            <Mono size={11} tone={result.ok && result.signedIn ? "good" : result.ok ? "warn" : "bad"}>
              {testLine(result)}
            </Mono>
          </div>
        )}
      </Card>

      {/* --------------------------------------------------- secure context */}
      <Card data-testid="conn-secure">
        <Label>CAMERA, COMPASS AND LOCATION</Label>
        {model.secure.map((s) => (
          <p key={s} style={PARA}>{s}</p>
        ))}
      </Card>

      <p style={{ ...PARA, lineHeight: 1.5 }}>
        Everything runs on the rig computer; the phone is a remote. Direct Wi-Fi is fastest and
        works with no internet. The relay is end-to-end encrypted with the owner key you paired,
        so a session keeps running and stays reachable when you leave the yard - files just move
        slower.
      </p>
    </Sheet>
  );
}

export default ConnectionSheet;
