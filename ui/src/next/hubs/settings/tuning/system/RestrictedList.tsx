// RestrictedList.tsx - Settings > RESTRICTED ASSETS, rebuilt in the design
// vocabulary (wave R7, T-R7-12; parity table 3.F10).
//
// Things AstroDeck is not licensed to redistribute, and what it does instead.
// Two shapes (server/astrodeck/licensing.py has the full reasoning):
//
//   FETCH        the asset is fine to USE and not fine for us to REDISTRIBUTE,
//                so we ship nothing and this machine pulls it from the party
//                that publishes it. DSS2 tiles, the Player One SDK.
//
//   ACKNOWLEDGE  there is no asset - the CLIENT is what the terms do not cover,
//                and not-shipping-a-file cannot fix that. Astrospheric.
//
// WHY THE LICENSOR'S WORDS ARE ON SCREEN. `vendor/playerone/README.md` once
// said the licence "permits redistribution". It does not, and that sentence was
// an interpretation written down once and then read as a fact for weeks, with
// six binaries shipped on it. So the quote and our reading of it are separate
// blocks, and a reader can disagree with the second without going to find the
// first.
//
// THE CONSENT IS INSTANCE-WIDE. One acknowledgment covers everyone who uses
// this rig; viewers and operators are never asked. What is being asserted is a
// fact about the DEPLOYMENT, not a promise by whoever happens to be signed in -
// and a per-user prompt would collect agreements from people with no idea what
// the deployment is. Hence `config.backend`, the same gate the route uses.

import { useCallback, useEffect, useState, type JSX } from "react";
import { api } from "../../../../../api";
import { useStore } from "../../../../../store";
import { useCanConfigBackend } from "../../../../../lib/caps";
import {
  ActionButton, Card, EmptyCard, Label, LockNote, Mono, StatusPill,
} from "../../../../ui";
import { explainLock } from "../../../../shell/explain";
import {
  RESTRICTED_ASKED_NOTE, RESTRICTED_EMPTY_HINT, RESTRICTED_EMPTY_TITLE,
  RESTRICTED_LEAD, RESTRICTED_LOCK_NOTE, remedyLabel, type RestrictedAsset,
} from "./systemModel";
import "./system.css";

export function RestrictedList(): JSX.Element {
  const [assets, setAssets] = useState<RestrictedAsset[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const showToast = useStore((s) => s.showToast);
  const canAdmin = useCanConfigBackend();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ assets: RestrictedAsset[] }>("/api/licensing/restricted");
      setAssets(r.assets ?? []);
    } catch {
      // Silent: this screen is a disclosure, and a red box where the licence
      // text should be is worse than an empty list on a server too old to have
      // the route. The empty state below says which it is.
      setAssets([]);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const send = async (a: RestrictedAsset, withdraw: boolean) => {
    setBusy(a.id);
    try {
      const r = await api.post<{ assets: RestrictedAsset[] }>(
        `/api/licensing/restricted/${a.id}/acknowledge`, { withdraw });
      setAssets(r.assets ?? []);
      showToast("success", withdraw
        ? `${a.title} is off again - ${a.without}.`
        : `${a.title} is on for everyone using this AstroDeck.`);
    } catch (e) {
      showToast("error", (e as Error).message, { verbatim: true });
    } finally {
      setBusy(null);
    }
  };

  const capNote = canAdmin ? null : RESTRICTED_LOCK_NOTE;

  // A route that renders nothing is a dead end: the sheet header would sit over
  // blank space with no way to tell "nothing to disclose" from "this failed".
  if (assets && assets.length === 0) {
    return (
      <EmptyCard
        title={RESTRICTED_EMPTY_TITLE}
        hint={RESTRICTED_EMPTY_HINT}
        data-testid="restricted-empty"
      />
    );
  }

  return (
    <div className="nx-sys-stack">
      <Card padding={12} data-testid="restricted-lead">
        <div className="nx-sys-stack">
          <Label size={11}>Not ours to give you</Label>
          <p className="nx-sys-note">{RESTRICTED_LEAD}</p>
          <p className="nx-sys-note">{RESTRICTED_ASKED_NOTE}</p>
        </div>
      </Card>

      {(assets ?? []).map((a) => (
        <Card
          key={a.id}
          padding={12}
          data-testid="restricted-row"
        >
          <div className="nx-sys-stack" data-restricted-asset={a.id}>
            <div className="nx-sys-head">
              <StatusPill
                text={a.satisfied ? "IN USE" : "NOT IN USE"}
                tone={a.satisfied ? "good" : "dim"}
                data-testid="restricted-state"
              />
              <span className="nx-sys-entryname">{a.title}</span>
              <Mono size={10}>{remedyLabel(a.remedy)}</Mono>
            </div>

            {/* The licensor's own words, then ours, never merged. */}
            <blockquote className="nx-sys-quote" data-testid="restricted-quote">{a.quote}</blockquote>
            <p className="nx-sys-note">{a.reading}</p>

            {!a.satisfied && (
              <p className="nx-sys-warn" data-testid="restricted-without">
                {`Until then, ${a.without}.`}
              </p>
            )}

            {a.remedy === "fetch" ? (
              <p className="nx-sys-note" data-testid="restricted-source">
                {"Get it from "}
                <span className="nx-sys-code">{a.source}</span>
                {a.id === "dss2" ? " - the Atlas has a download button for it." : ""}
              </p>
            ) : (
              <div className="nx-sys-stack">
                <div className="nx-sys-actions">
                  <ActionButton
                    kind={a.consent ? "secondary" : "primary"}
                    onPress={() => void send(a, !!a.consent)}
                    busy={busy === a.id}
                    lockedReason={capNote}
                    onExplain={explainLock}
                    data-testid="restricted-ack"
                  >
                    {a.consent ? "TAKE IT BACK" : "THIS DESCRIBES MY USE"}
                  </ActionButton>
                </div>
                {a.consent && (
                  <Mono size={10.5} data-testid="restricted-consent">
                    {`recorded by ${a.consent.by} on `
                      + `${new Date(a.consent.at * 1000).toLocaleDateString()}`
                      + " - it covers everyone using this AstroDeck"}
                  </Mono>
                )}
                <LockNote reason={capNote} data-testid="restricted-lock-note" />
              </div>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

export default RestrictedList;
