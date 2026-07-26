// ViewBoundary.tsx — the Suspense + error boundary that renders a code-split
// view (see lib/lazyViews.ts for why the views are split at all).
//
// The rule this file enforces: A LAZY VIEW MAY NEVER RENDER AS A BLANK PANE.
// The user is on a tablet in a dark field at 2am. If the chunk for "Guide" does
// not arrive, they must see what happened and get a control that actually works,
// not an empty rectangle with no explanation. So:
//
//   * Pending -> the app's existing loading treatment (the Logo + tracked
//                "Loading…" caption used by App's auth splash), so a brief pending
//                state reads as the same app, not a foreign spinner.
//   * Failed  -> an .empty-state interstitial in the same visual language as
//                NotConnectedInterstitial, saying plainly that nothing on the rig
//                is affected, and offering a RELOAD.
//
// WHY RELOAD AND NOT "TRY AGAIN" — this is the non-obvious part, and the earlier
// draft of this file got it wrong. A dynamic import() whose fetch fails is stored
// as a failure in the document's module map forever; every subsequent import() of
// that URL reuses the failure without touching the network. Measured against this
// build: after one blocked chunk fetch, a retry produced ZERO further network
// requests and simply re-showed the error four seconds later. So an in-page "Try
// again" button cannot work, no matter how the lazy component is rebuilt — it is
// a button that lies. A reload gets a fresh module map and genuinely fixes it
// (also measured). See the header of lib/lazyViews.ts for the full note.
//
// In practice this pane is close to unreachable: lazyViews preloads every chunk in
// the background right after first paint, so by the time anything is tapped the
// code is already in memory. It exists for the case that matters more than the
// common one — a link that was down during the preload window, or a mid-session
// server update invalidating a hashed chunk.
import { Component, Suspense, type ErrorInfo, type JSX, type ReactNode } from "react";
import Logo from "./Logo";
import { getLazyView } from "../lib/lazyViews";
import type { ViewName } from "../types";

function ViewLoading(): JSX.Element {
  return (
    <div className="flex-1 flex items-center justify-center py-16" role="status" aria-live="polite">
      <div className="flex flex-col items-center gap-3 text-dim">
        <Logo className="w-10 h-10 opacity-80" />
        <span className="text-[11px] tracking-[0.22em] uppercase">Loading…</span>
      </div>
    </div>
  );
}

function ViewLoadFailed(): JSX.Element {
  return (
    <div className="empty-state view-enter" role="alert">
      <div className="panel-title !text-ink">Couldn't load this screen</div>
      <p className="text-xs text-dim max-w-[46ch]">
        This screen's code didn't finish downloading from the AstroDeck box — usually
        a brief drop in the WiFi link. Nothing on the mount or camera is affected, and
        a running sequence keeps going.
      </p>
      <p className="text-xs text-dim max-w-[46ch]">
        Reloading fetches it again. Other screens keep working in the meantime.
      </p>
      <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
        <button type="button" className="btn btn-accent min-h-11" onClick={() => window.location.reload()}>
          Reload app
        </button>
      </div>
    </div>
  );
}

/** Resolves the lazy component for `view` during render. */
function LazyViewHost({ view }: { view: ViewName }): JSX.Element | null {
  const C = getLazyView(view);
  if (!C) return null; // eager view; App never routes those here
  return <C />;
}

type Props = { view: ViewName };
type State = { failed: boolean };

class ViewBoundaryInner extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Console only: this path is a failed asset *download*, not rig telemetry, so
    // it does not belong in the store-owned log/toast pipeline.
    console.error(`[astrodeck] failed to load view chunk "${this.props.view}"`, error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    // A different view mounting into a reused boundary must not inherit the
    // previous view's failure. (App keys <main> by view, so this is belt and
    // braces — but a stuck error pane is exactly the 2am failure being avoided.)
    if (prev.view !== this.props.view && this.state.failed) {
      this.setState({ failed: false });
    }
  }

  render(): ReactNode {
    if (this.state.failed) return <ViewLoadFailed />;
    return (
      <Suspense fallback={<ViewLoading />}>
        <LazyViewHost view={this.props.view} />
      </Suspense>
    );
  }
}

/** Render a code-split view with a loading fallback and an honest failure state. */
export default function ViewBoundary({ view }: Props): JSX.Element {
  // Keyed so each destination gets its own clean boundary instance.
  return <ViewBoundaryInner key={view} view={view} />;
}
