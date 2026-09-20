import { rootForHash } from "../rootChoice";
import { nav } from "../next/router";

export type SettingsPanel = "site" | "optics";
export function settingsPanelFromHash(hash: string): SettingsPanel | null {
  const [path, query] = hash.split("?");
  if (path !== "#/classic/settings") return null;
  const panel = new URLSearchParams(query).get("panel");
  return panel === "site" || panel === "optics" ? panel : null;
}

/** Navigate to the existing editor, preserving its permissions and save path. */
export function openSettingsPanel(panel: SettingsPanel): void {
  if (rootForHash(window.location.hash) === "next") {
    nav.sheet(panel);
    return;
  }
  const params = new URLSearchParams();
  params.set("panel", panel);
  const experience = new URLSearchParams(window.location.hash.split("?")[1]).get("experience");
  if (experience === "guided" || experience === "pro") params.set("experience", experience);
  const destination = `#/classic/settings?${params}`;
  if (window.location.hash === destination) {
    // The editor may still be mounted on a different Settings tab. Reopening
    // the same destination must reveal it without adding duplicate history.
    window.dispatchEvent(new Event("hashchange"));
  } else {
    window.location.hash = destination;
  }
}
