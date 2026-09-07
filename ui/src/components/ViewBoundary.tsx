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
//   * Failed to DOWNLOAD -> an .empty-state interstitial in the same visual
//                language as NotConnectedInterstitial, saying plainly that
//                nothing on the rig is affected, and offering a RELOAD.
//   * Failed to RENDER   -> a different interstitial. A render throw (a bad
//                catalog row, a null the view forgot to guard) is a bug in the
//                mounted view, not a dropped WiFi link, and telling the user to
//                blame the network sent them nowhere useful — see the NGC 604
//                incident, where a magnitude-less catalog row crashed the whole
//                Mount view and the pane on screen said "brief drop in the WiFi
//                link". This pane names itself honestly, shows the thrown
//                message (so a screenshot tells us what broke), and offers a
//                TRY AGAIN that resets the boundary in place, alongside Reload.
//
// WHY RELOAD AND NOT "TRY AGAIN" FOR A CHUNK FAILURE — this is the non-obvious
// part, and the earlier draft of this file got it wrong. A dynamic import()
// whose fetch fails is stored as a failure in the document's module map
// forever; every subsequent import() of that URL reuses the failure without
// touching the network. Measured against this build: after one blocked chunk
// fetch, a retry produced ZERO further network requests and simply re-showed
// the error four seconds later. So an in-page "Try again" button cannot work
// for THIS failure, no matter how the lazy component is rebuilt — it is a
// button that lies. A reload gets a fresh module map and genuinely fixes it
// (also measured). See the header of lib/lazyViews.ts for the full note.
//
// A RENDER failure is a different animal: nothing about the module map is
// broken, so re-mounting the same component can genuinely recover (the state
// that triggered the throw may already be gone) — which is why only that pane
// gets a working "Try again".
//
// In practice the chunk-failure pane is close to unreachable: lazyViews
// preloads every chunk in the background right after first paint, so by the
// time anything is tapped the code is already in memory. It exists for the
// case that matters more than the common one — a link that was down during the
// preload window, or a mid-session server update invalidating a hashed chunk.
import { Component, Suspense, type ErrorInfo, type JSX, type ReactNode } from "react";
import Logo from "./Logo";
import { getLazyView } from "../lib/lazyViews";
import type { ViewName } from "../types";

/** A failed CODE-SPLIT CHUNK, not a render throw. Chrome/webpack throw an
 *  Error named "ChunkLoadError"; a native `import()` (Vite's dev/preview path,
 *  Safari, Firefox) instead rejects with one of a handful of message shapes.
 *  Anything that doesn't match either is treated as a genuine bug in the
 *  mounted view. Exported so the classification is unit-tested directly
 *  rather than only through the rendered pane. */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (error.name === "ChunkLoadError") return true;
  return /Loading chunk|dynamically imported module|Failed to fetch dynamically imported|Importing a module script failed/
    .test(error.message || "");
}

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

/** The honest counterpart to ViewLoadFailed: this pane is for a render throw
 *  inside the mounted view, not a missing download. `message` is the thrown
 *  Error's own text, shown verbatim and in monospace so a screenshot from the
 *  field is a bug report rather than a mystery. */
function ViewRenderError({ message, onRetry }: { message: string; onRetry: () => void }): JSX.Element {
  return (
    <div className="empty-state view-enter" role="alert">
      <div className="panel-title !text-ink">This screen hit an error</div>
      <p className="text-xs text-dim max-w-[46ch]">
        The rest of the app keeps working, and a running sequence is not affected.
      </p>
      <p className="mono text-xs text-bad max-w-[46ch] break-words">{message}</p>
      <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
        <button type="button" className="btn btn-accent min-h-11" onClick={() => window.location.reload()}>
          Reload app
        </button>
        <button type="button" className="btn min-h-11" onClick={onRetry}>
          Try again
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
type State = { failed: boolean; chunkFailure: boolean; message: string };

const NO_FAILURE: State = { failed: false, chunkFailure: false, message: "" };

class ViewBoundaryInner extends Component<Props, State> {
  state: State = NO_FAILURE;

  static getDerivedStateFromError(error: Error): State {
    return {
      failed: true,
      chunkFailure: isChunkLoadError(error),
      message: error?.message || String(error),
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (isChunkLoadError(error)) {
      // Console only: this path is a failed asset *download*, not rig telemetry, so
      // it does not belong in the store-owned log/toast pipeline.
      console.error(`[astrodeck] failed to load view chunk "${this.props.view}"`, error, info.componentStack);
    } else {
      // A genuine render throw, not a download — a distinct prefix so a log
      // grep (or a support screenshot) doesn't confuse the two.
      console.error(`[astrodeck] view "${this.props.view}" threw while rendering`, error, info.componentStack);
    }
  }

  componentDidUpdate(prev: Props): void {
    // A different view mounting into a reused boundary must not inherit the
    // previous view's failure. (App keys <main> by view, so this is belt and
    // braces — but a stuck error pane is exactly the 2am failure being avoided.)
    if (prev.view !== this.props.view && this.state.failed) {
      this.setState(NO_FAILURE);
    }
  }

  /** "Try again" for a render failure: re-mount the subtree in place. Never
   *  offered for a chunk failure — see the header note on why that retry
   *  cannot work. */
  retry = (): void => {
    this.setState(NO_FAILURE);
  };

  render(): ReactNode {
    if (this.state.failed) {
      return this.state.chunkFailure
        ? <ViewLoadFailed />
        : <ViewRenderError message={this.state.message} onRetry={this.retry} />;
    }
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
