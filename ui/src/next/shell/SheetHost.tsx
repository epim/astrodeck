// SheetHost.tsx - renders `route.sheets` through the global registry
// (ARCHITECTURE.md section 5).
//
// Phone: a fixed layer over the hub. The sheet UNDER the top one stays mounted
// and goes `inert`, so a BACK returns to its scroll position, its half-typed
// field and its in-flight request instead of remounting it - and nothing under
// the glass can answer a tap that lands through a gap.
//
// Tablet and desktop: a 420 px panel beside the hub, showing only the top sheet.
// Nothing is hidden behind a sheet at those widths, so keeping the one below
// mounted would only pay for a rerender nobody sees.
//
// Escape and the BACK pill both call `nav.back()`. There is exactly one way out
// of a sheet, and the browser's own Back button is a third door onto the same
// route change, because the sheet stack lives in the hash.

import { useEffect, useRef, type JSX } from "react";
import { EmptyCard } from "../ui";
import { nav, type Route } from "../router";
import { SHEETS } from "../hubs";
import { HubBoundary } from "./HubBoundary";

/** A sheet name in the hash that no hub registers. This is REACHABLE by design,
 *  not a bug guard: the legacy bridge maps `setView("focus")` to
 *  `/rig/devices/focuser`, and that sheet does not exist until the Rig task
 *  lands. Saying so is the honest answer; rendering nothing would be a tap that
 *  changed the URL and the screen alike, with no way to tell which broke. */
function MissingSheet({ name }: { name: string }): JSX.Element {
  return (
    <div className="nx-sheet" data-testid="sheet-missing">
      <EmptyCard
        title="THIS SHEET IS NOT BUILT YET"
        hint={`"${name}" has no screen in this build. The rest of the app is unaffected; press BACK.`}
      />
    </div>
  );
}

function Slot({ name, params, depth, under }: {
  name: string;
  params: Record<string, string>;
  depth: 0 | 1;
  under: boolean;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // `inert` is set through the DOM rather than as a JSX prop: React 18 has no
    // typed `inert` and would render the boolean as the string "false", which
    // is still an inert element in every browser that implements it.
    if (under) el.setAttribute("inert", "");
    else el.removeAttribute("inert");
  }, [under]);

  const Comp = SHEETS[name];
  return (
    <div className="nx-sheet-slot" data-under={under ? "true" : "false"} ref={ref}>
      {/* EACH SLOT GETS ITS OWN BOUNDARY (review #2). A sheet is where the
          catalogue rows, the driver payloads and the report folds are rendered
          - the most likely places for an unguarded null - and an unguarded
          throw here took the whole app down with it, sheet, hub, tab bar and
          all. Keyed by the sheet name so a different sheet in a reused slot
          starts clean. */}
      {Comp ? (
        <HubBoundary key={name} name={name.toUpperCase()}>
          <Comp params={params} depth={depth} />
        </HubBoundary>
      ) : (
        <MissingSheet name={name} />
      )}
    </div>
  );
}

export function SheetHost({ route, phone }: { route: Route; phone: boolean }): JSX.Element | null {
  const open = route.sheets.length > 0;

  // Escape closes the top sheet. Bound while a sheet is open only, so the key
  // keeps whatever meaning a hub screen gives it the rest of the time.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      nav.back();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Body scroll lock, phone only: the layer is `position: fixed`, and without
  // this the page behind it scrolls under the sheet on iOS and the user comes
  // back to a hub scrolled somewhere they never went.
  useEffect(() => {
    if (!open || !phone) return;
    const body = document.body;
    const prev = body.style.overflow;
    body.style.overflow = "hidden";
    return () => { body.style.overflow = prev; };
  }, [open, phone]);

  if (!open) return null;

  const sheets = route.sheets;

  if (!phone) {
    const name = sheets[sheets.length - 1];
    return (
      <aside className="nx-sheet-panel" data-testid="sheet-panel">
        <Slot
          name={name}
          params={route.params}
          depth={(sheets.length - 1) as 0 | 1}
          under={false}
        />
      </aside>
    );
  }

  return (
    <div className="nx-sheet-layer" data-testid="sheet-layer">
      {sheets.map((name, i) => (
        <Slot
          key={`${i}:${name}`}
          name={name}
          params={route.params}
          depth={i as 0 | 1}
          under={i < sheets.length - 1}
        />
      ))}
    </div>
  );
}
