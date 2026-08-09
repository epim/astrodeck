// Things AstroDeck is not licensed to redistribute, and what it does instead.
//
// Three of them, two shapes (server/astrodeck/licensing.py has the full
// reasoning):
//
//   FETCH        the asset is fine to USE and not fine for us to REDISTRIBUTE,
//                so we ship nothing and this machine pulls it from the party
//                that publishes it. DSS2 tiles, the Player One SDK.
//
//   ACKNOWLEDGE  there is no asset — the CLIENT is what the terms do not cover,
//                and not-shipping-a-file cannot fix that. Astrospheric.
//
// WHY THE LICENSOR'S WORDS ARE ON SCREEN. `vendor/playerone/README.md` once
// said the licence "permits redistribution". It does not, and that sentence was
// an interpretation written down once and then read as a fact for weeks, with
// six binaries shipped on it. So the quote and our reading of it are separate
// blocks here, and a reader can disagree with the second without going to find
// the first.
//
// THE CONSENT IS INSTANCE-WIDE. One acknowledgment covers everyone who uses
// this rig; viewers and operators are never asked. What is being asserted is a
// fact about the DEPLOYMENT, not a promise by whoever happens to be signed in —
// and a per-user prompt would collect agreements from people with no idea what
// the deployment is.
import { useCallback, useEffect, useState, type JSX } from "react";
import { api } from "../../api";
import { useStore } from "../../store";
import { Panel, HonestButton, Led } from "../ui";
import { useCanConfigBackend } from "../../lib/caps";

export interface RestrictedAsset {
  id: string;
  title: string;
  quote: string;
  reading: string;
  remedy: "fetch" | "acknowledge";
  source: string;
  without: string;
  satisfied: boolean;
  consent: { at: number; by: string; note: string } | null;
}

export function RestrictedAssetsPanel(): JSX.Element | null {
  const [assets, setAssets] = useState<RestrictedAsset[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const showToast = useStore((s) => s.showToast);
  // The same gate the route uses. An acknowledgment is a statement about the
  // deployment, so it comes from whoever administers the deployment.
  const canAdmin = useCanConfigBackend();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ assets: RestrictedAsset[] }>(
        "/api/licensing/restricted");
      setAssets(r.assets ?? []);
    } catch {
      // Silent: this panel is a disclosure, and a red box where the licence
      // text should be is worse than the section simply not being there on a
      // server too old to have the route.
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
        ? `${a.title} is off again — ${a.without}.`
        : `${a.title} is on for everyone using this AstroDeck.`);
    } catch (e) {
      showToast("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (!assets || assets.length === 0) return null;

  return (
    <Panel title="Not ours to give you">
      <p className="text-sm text-dim">
        Three things AstroDeck uses are not ours to hand on. Two of them we
        simply do not ship — this machine gets them from the people who publish
        them, which their licences do allow. The third is a service whose terms
        do not cover a product like this one at all, so it stays switched off
        until you say it covers <em>your</em> use of it.
      </p>
      <p className="mt-1 text-xs text-dim">
        We have asked all three for permission. Nothing on this page is settled
        by it being here.
      </p>

      <div className="mt-3 flex flex-col gap-3">
        {assets.map((a) => (
          <div key={a.id} className="rounded border border-line2 p-3"
               data-restricted-asset={a.id}>
            <div className="flex items-center gap-2 flex-wrap">
              <Led state={a.satisfied ? "on" : "off"}
                   label={a.satisfied ? "in use" : "not in use"} />
              <span className="font-medium">{a.title}</span>
              <span className="text-xs text-dim ml-auto mono">
                {a.remedy === "fetch" ? "fetched, never shipped" : "needs your word"}
              </span>
            </div>

            <blockquote className="mt-2 border-l-2 border-line2 pl-2 text-xs
                                   text-dim italic">
              {a.quote}
            </blockquote>
            <p className="mt-2 text-xs text-dim">{a.reading}</p>

            {!a.satisfied && (
              <p className="mt-2 text-xs text-[var(--color-warn)]">
                Until then, {a.without}.
              </p>
            )}

            {a.remedy === "fetch" ? (
              <p className="mt-2 text-xs text-dim">
                Get it from{" "}
                <span className="mono break-all">{a.source}</span>
                {a.id === "dss2" && " — the Atlas has a download button for it."}
              </p>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <HonestButton
                  reason={canAdmin ? null
                    : "only someone who administers this AstroDeck can answer "
                      + "this — it is a statement about the whole rig"}
                  onClick={() => void send(a, !!a.consent)}
                  onExplain={(why) => showToast("info", why)}
                  className={a.consent ? "btn" : "btn btn-primary"}
                >
                  {busy === a.id ? "…"
                    : a.consent ? "Take it back" : "This describes my use"}
                </HonestButton>
                {a.consent && (
                  <span className="text-xs text-dim">
                    recorded by {a.consent.by} on{" "}
                    {new Date(a.consent.at * 1000).toLocaleDateString()} — it
                    covers everyone using this AstroDeck
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}

export default RestrictedAssetsPanel;
