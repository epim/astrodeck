// HubBoundary.tsx - the error boundary the new root did not have (review #2).
//
// THE REGRESSION THIS CLOSES. The legacy root wraps every view in
// `components/ViewBoundary.tsx`, and that file exists because of a real
// incident stated in its own header: the NGC 604 night, where a magnitude-less
// catalog row threw inside the Mount view. Under `NextApp` the hub body and
// every sheet slot were mounted bare, so ONE unguarded null anywhere under
// `hubs/**` blanked the entire app - header, tab bar, toasts, and the
// brightness reset with it - leaving no message and no way back but a manual
// reload, on a phone, in the dark.
//
// TWO FAILURES, TWO PANES, AND THE DIFFERENCE IS LOAD-BEARING.
//
//   * A failed CHUNK DOWNLOAD (the hubs are code-split - see `hubs/index.ts`)
//     offers RELOAD APP and no retry. A dynamic import() whose fetch fails is
//     recorded as a failure in the document's module map for the lifetime of
//     the document; every later import() of that URL reuses the stored failure
//     WITHOUT touching the network. `lib/lazyViews.ts` measured exactly that.
//     So an in-page "try again" for this failure is a button that lies, and
//     only a fresh document can fix it.
//   * A RENDER THROW is a different animal: nothing about the module map is
//     broken, the state that threw may already be gone, and re-mounting the
//     subtree can genuinely recover. That pane gets a working TRY AGAIN, the
//     same semantics `ViewBoundary` has always had.
//
// The classifier itself is IMPORTED from `ViewBoundary`, not copied: one regex
// deciding "download" from "bug" is one place for it to be wrong, and the
// legacy file exports it precisely so it can be reasoned about once.
//
// THE MESSAGE NAMES THE SCREEN. "This screen hit an error" on a phone held at
// arm's length in the dark does not say which of six hubs and thirty sheets
// broke; `RIG - CAPTURE HIT AN ERROR` does, and it is the difference between a
// support screenshot and a mystery. The thrown text is rendered verbatim under
// it for the same reason.
//
// No `console.error` here, deliberately: `ui/src/next` carries none, and the
// pane on screen IS the report - a console line the user cannot see would only
// duplicate what they are already looking at.

import { Component, Suspense, type JSX, type ReactNode } from "react";
import { ActionButton, EmptyCard } from "../ui";
import { isChunkLoadError } from "../../components/ViewBoundary";

/** What the pending state looks like while a hub chunk is in flight. Dashed,
 *  in the app's own vocabulary, and it says which screen is coming - a bare
 *  spinner over a blank body reads as a broken app on a slow field link. */
function HubLoading({ name }: { name: string }): JSX.Element {
  return (
    <EmptyCard
      title="LOADING"
      hint={`${name} is still arriving from the rig computer.`}
      data-testid="hub-loading"
    />
  );
}

function reload(): void {
  if (typeof window !== "undefined") window.location.reload();
}

function ChunkFailed({ name }: { name: string }): JSX.Element {
  return (
    <EmptyCard
      title={`${name} DID NOT FINISH LOADING`}
      hint={
        "This screen's code did not arrive from the AstroDeck box - usually a brief "
        + "drop in the link. Nothing on the mount or camera is affected and a running "
        + "sequence keeps going. Reloading fetches it again; the other screens keep "
        + "working in the meantime."
      }
      action={
        <ActionButton kind="primary" onPress={reload} data-testid="hub-error-reload">
          RELOAD APP
        </ActionButton>
      }
      data-testid="hub-chunk-failed"
    />
  );
}

function RenderFailed({ name, message, onRetry }: {
  name: string;
  message: string;
  onRetry: () => void;
}): JSX.Element {
  return (
    <EmptyCard
      title={`${name} HIT AN ERROR`}
      hint={
        <>
          <span>
            The rest of the app keeps working and a running sequence is not affected.
          </span>
          {" "}
          <span className="nx-boundary-message" data-testid="hub-error-message">{message}</span>
        </>
      }
      action={
        <>
          <ActionButton kind="primary" onPress={onRetry} data-testid="hub-error-retry">
            TRY AGAIN
          </ActionButton>
          <ActionButton kind="secondary" onPress={reload} data-testid="hub-error-reload">
            RELOAD APP
          </ActionButton>
        </>
      }
      data-testid="hub-render-failed"
    />
  );
}

export interface HubBoundaryProps {
  /** What the screen is CALLED, in the user's own words - "RIG - CAPTURE",
   *  "CAMERA". It is printed in the failure title, so it must be the name on
   *  the tab or the sheet header, never a module path. */
  name: string;
  children: ReactNode;
}

interface State { failed: boolean; chunkFailure: boolean; message: string }

const NO_FAILURE: State = { failed: false, chunkFailure: false, message: "" };

class HubBoundaryInner extends Component<HubBoundaryProps, State> {
  state: State = NO_FAILURE;

  static getDerivedStateFromError(error: Error): State {
    return {
      failed: true,
      chunkFailure: isChunkLoadError(error),
      message: error?.message || String(error),
    };
  }

  /** THE RESET. A boundary reused for a DIFFERENT screen must not inherit the
   *  previous screen's failure, or one bad hub leaves a stuck error pane over
   *  every other. `name` is the destination (`route.hub + route.sub`, or the
   *  sheet name), so a change in it IS a change of screen - which is why
   *  `NextApp` relies on this rather than on a React `key`: a key would also
   *  remount the hub body on every sub-nav tap. `SheetHost` does key its slots,
   *  because a sheet swapped in place is genuinely a different screen. */
  componentDidUpdate(prev: HubBoundaryProps): void {
    if (prev.name !== this.props.name && this.state.failed) this.setState(NO_FAILURE);
  }

  /** Re-mount the subtree in place. Offered ONLY for a render throw - see the
   *  header for why a chunk failure cannot be retried in this document. */
  retry = (): void => { this.setState(NO_FAILURE); };

  render(): ReactNode {
    const { name, children } = this.props;
    if (this.state.failed) {
      return this.state.chunkFailure
        ? <ChunkFailed name={name} />
        : <RenderFailed name={name} message={this.state.message} onRetry={this.retry} />;
    }
    return <Suspense fallback={<HubLoading name={name} />}>{children}</Suspense>;
  }
}

/** Wrap a hub body or a sheet slot. `name` must change when the destination
 *  does (`route.hub + route.sub`, or the sheet name): that is what clears a
 *  failure when the user moves on. */
export function HubBoundary({ name, children }: HubBoundaryProps): JSX.Element {
  return <HubBoundaryInner name={name}>{children}</HubBoundaryInner>;
}
