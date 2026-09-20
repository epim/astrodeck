import type { ReactNode } from "react";

/** Keep the active tool mounted when the overview covers it. Local form drafts,
 * graph selection and in-flight UI operations survive both mode switches. */
export function RetainedTool({ hidden, children }: { hidden: boolean; children: ReactNode }) {
  return <div data-testid="retained-tool" style={{ display: hidden ? "none" : "flex" }} className="flex flex-1 min-h-0 flex-col">{children}</div>;
}
