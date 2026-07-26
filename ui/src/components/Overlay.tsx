// Overlay.tsx — THE overlay primitive. One implementation behind every dialog,
// sheet and drawer in the app.
//
// WHY THIS EXISTS (review S1, findings #3 #4 #22-drawer #38 #44 + the wizard):
// four reviewers hit the same class of bug on four different surfaces. The
// measured causes were all different and the symptom was always identical — the
// overlay landed somewhere other than the viewport, on a see-through surface,
// with its action buttons off the bottom edge and no gesture that could reach
// them (`body { overflow: hidden }` while the document is taller than the
// window, so there is literally nothing to scroll).
//
//   * `.view-enter`'s `animation-fill-mode: both` left an identity `matrix()`
//     transform on the wrapper around EVERY routed view, making it the
//     containing block for the preflight's `fixed inset-0`. Measured on tablet:
//     dialog x=-10 with its status column at x=-80/right=620; on desktop the
//     footer buttons at y=899..947 against vh=900. (Fixed in index.css too —
//     see the CSS-MOTION note — but see below for why that is not sufficient.)
//   * `.panel { backdrop-filter: blur(14px) }` is the same trap and CANNOT be
//     removed: a `fixed inset-0` probe inside a panel measures 700x1515.
//   * `.panel { position: relative }` is unlayered authored CSS, so it silently
//     beats the Tailwind `fixed` utility for anything that carries both. That is
//     what put the first-run wizard at y=1164 in an 1180px viewport (tablet),
//     y=844 in 844 (phone), y=884 in 900 (desktop) — confirmed by measurement,
//     which also corrects the review's inferred culprit.
//
// So the primitive does not try to keep the view tree safe. It leaves it:
// everything renders through a portal into a `.overlay-host` appended to
// <body>, which is `fixed; inset: 0` and therefore its own containing block IS
// the viewport. Nothing an author does inside a view can move an overlay again.
//
// The host carries the dimmer's brightness filter, because portalling out of
// `.dim-content` must not mean a dialog flares to full brightness on a
// dark-adapted screen. See CSS-OVERLAY in index.css for the full contract:
// portalled / clamped to 100dvh / OPAQUE surface / scrim / footer outside the
// scroll region / safe-area inset.

import {
  useEffect, useRef, useState,
  type ReactNode, type CSSProperties, type JSX,
} from "react";
import { createPortal } from "react-dom";

const HOST_ID = "ad-overlay-root";

/** Lazily create (once) the body-level portal host every overlay renders into. */
function overlayHost(): HTMLElement {
  let el = document.getElementById(HOST_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = HOST_ID;
    el.className = "overlay-host";
    document.body.appendChild(el);
  }
  return el;
}

/** Live media-query match. Used so a responsive overlay renders ONE node rather
 *  than two `display:none`-toggled ones — two nodes means the focus trap can
 *  target the invisible copy (the bug LogDrawer carried a ref-pair to work
 *  around). */
export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** Tailwind's `lg` breakpoint — the docked-drawer threshold. */
export function useIsLg(): boolean {
  return useMediaQuery("(min-width: 1024px)");
}

export type OverlayVariant =
  | "center"   // centred dialog on sm+, bottom sheet on phone (confirm, preflight)
  | "sheet"    // bottom sheet at every width (nav MORE)
  | "dock"     // right column on lg+, bottom sheet below (log / review drawers)
  | "corner";  // small docked card, bottom sheet on phone (first-run wizard)

export interface OverlayProps {
  open: boolean;
  /** Accessible name for the dialog. */
  label: string;
  onClose: () => void;
  variant?: OverlayVariant;
  /** Modal = scrim + aria-modal + focus trap + outside-tap dismiss. Default true. */
  modal?: boolean;
  role?: "dialog" | "alertdialog";
  /** Show the dark scrim. Defaults to `modal`. */
  scrim?: boolean;
  /** Tap outside dismisses. Defaults to `modal`; a non-modal overlay that opts
   *  IN gets an invisible catcher instead of a visible scrim. */
  dismissOnOutside?: boolean;
  /** Keep Tab inside. Defaults to `modal`. */
  trapFocus?: boolean;
  /** Move focus into the overlay on open and restore it on close. Defaults to
   *  `modal` — the first-run wizard deliberately does NOT steal focus. */
  autoFocus?: boolean;
  closeOnEscape?: boolean;
  /** Fixed header row, outside the scroll region. */
  head?: ReactNode;
  /** Fixed footer row, outside the scroll region — this is what guarantees the
   *  action buttons stay reachable inside the clamped height. */
  foot?: ReactNode;
  children: ReactNode;
  /** Extra classes on the surface (sizing overrides). */
  surfaceClassName?: string;
  bodyClassName?: string;
  surfaceStyle?: CSSProperties;
}

/** Custom-property bag; spread into `style` (React passes `--*` through). */
type OverlayVars = Record<string, string>;
export type OverlayGeometry = { wrap: string; surface: string; vars: OverlayVars };

/** Wrapper + surface geometry per variant.
 *
 *  SIZE GOES THROUGH CSS VARS, NOT UTILITY CLASSES. index.css is unlayered, so
 *  `.overlay-surface { max-width: ... }` beats any Tailwind `sm:max-w-*` on the
 *  same element no matter the order — measured the hard way: the preflight
 *  rendered 788px wide on an 820px tablet because the authored clamp ate the
 *  utility. The clamp and the variant size must therefore share one
 *  declaration, which is what `--ov-max-w` / `--ov-max-h` / `--ov-w` / `--ov-h`
 *  are for. Border radius and animation stay as utilities: nothing authored
 *  sets those, so there is nothing to lose to. */
export function overlayGeometry(variant: OverlayVariant, lg: boolean, sm: boolean): OverlayGeometry {
  switch (variant) {
    case "sheet":
      return {
        wrap: "absolute inset-0 flex flex-col justify-end",
        surface: "rounded-t-2xl more-sheet-in",
        vars: { "--ov-max-h": "85dvh" },
      };
    case "dock":
      return lg
        ? {
            wrap: "absolute inset-y-0 right-0 flex",
            surface: "",
            vars: { "--ov-w": "420px", "--ov-max-w": "92vw", "--ov-h": "100%" },
          }
        : {
            wrap: "absolute inset-0 flex flex-col justify-end",
            surface: "rounded-t-2xl sheet-enter",
            vars: { "--ov-max-h": "60dvh" },
          };
    case "corner":
      return {
        wrap: sm
          ? "absolute right-4 bottom-4 flex justify-end"
          // overlay-above-nav: a non-modal card must not sit on top of the
          // phone's fixed bottom nav — this one's whole job is to point at it.
          : "absolute inset-x-0 bottom-0 overlay-above-nav flex justify-center",
        surface: sm ? "rounded-2xl sheet-enter" : "rounded-t-2xl sheet-enter",
        vars: sm
          ? { "--ov-w": "380px", "--ov-max-w": "380px", "--ov-max-h": "80dvh" }
          : { "--ov-max-h": "70dvh" },
      };
    case "center":
    default:
      return {
        wrap: sm
          ? "absolute inset-0 flex items-center justify-center p-4"
          : "absolute inset-0 flex items-end justify-center",
        surface: sm ? "rounded-2xl sheet-enter" : "rounded-t-2xl sheet-enter",
        vars: sm
          ? { "--ov-max-w": "560px", "--ov-max-h": "calc(100dvh - 2rem)" }
          : { "--ov-max-h": "92dvh" },
      };
  }
}

/** Tab-reachable controls inside `root`. Roving-tabindex radiogroups mark their
 *  inactive options tabIndex=-1 and those still match the selector, so drop
 *  anything not actually reachable or the trap lands on an unfocusable node. */
function tabbablesIn(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]",
    ),
  ).filter((el) => el.tabIndex >= 0 && el.getClientRects().length > 0);
}

export function Overlay({
  open,
  label,
  onClose,
  variant = "center",
  modal = true,
  role = "dialog",
  scrim,
  dismissOnOutside,
  trapFocus,
  autoFocus,
  closeOnEscape = true,
  head,
  foot,
  children,
  surfaceClassName = "",
  bodyClassName = "",
  surfaceStyle,
}: OverlayProps): JSX.Element | null {
  const lg = useIsLg();
  const sm = useMediaQuery("(min-width: 640px)");
  const surfaceRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  const showScrim = scrim ?? modal;
  const outsideDismiss = dismissOnOutside ?? modal;
  const doTrap = trapFocus ?? modal;
  const doFocus = autoFocus ?? modal;

  // Focus in / restore out. Kept separate from the key handler so a non-focusing
  // overlay (the wizard) still gets Escape.
  useEffect(() => {
    if (!open || !doFocus) return;
    openerRef.current = (document.activeElement as HTMLElement) ?? null;
    tabbablesIn(surfaceRef.current)[0]?.focus();
    return () => {
      openerRef.current?.focus?.();
    };
  }, [open, doFocus]);

  useEffect(() => {
    if (!open || (!doTrap && !closeOnEscape)) return;
    const onKey = (e: KeyboardEvent) => {
      if (closeOnEscape && e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (!doTrap || e.key !== "Tab") return;
      const items = tabbablesIn(surfaceRef.current);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, doTrap, closeOnEscape, onClose]);

  if (!open) return null;

  const g = overlayGeometry(variant, lg, sm);
  // A non-modal overlay must not swallow taps over the rest of the screen — the
  // wrapper opts out via .overlay-passthrough and the surface opts back in.
  const passthrough = !showScrim && !outsideDismiss;

  return createPortal(
    <div className={`${g.wrap} ${passthrough ? "overlay-passthrough" : ""}`}>
      {showScrim && (
        <button
          type="button"
          className="overlay-scrim"
          aria-label={`Close ${label}`}
          tabIndex={-1}
          onClick={outsideDismiss ? onClose : undefined}
        />
      )}
      {!showScrim && outsideDismiss && (
        <button
          type="button"
          className="overlay-catcher"
          aria-label={`Close ${label}`}
          tabIndex={-1}
          onClick={onClose}
        />
      )}
      <div
        ref={surfaceRef}
        role={role}
        aria-modal={modal || undefined}
        aria-label={label}
        className={`overlay-surface ${g.surface} ${surfaceClassName}`}
        style={{ ...(g.vars as CSSProperties), ...surfaceStyle }}
      >
        {head && <div className="overlay-head">{head}</div>}
        <div className={`overlay-body ${!foot ? "overlay-safe-b" : ""} ${bodyClassName}`}>
          {children}
        </div>
        {foot && <div className="overlay-foot">{foot}</div>}
      </div>
    </div>,
    overlayHost(),
  );
}

export default Overlay;
