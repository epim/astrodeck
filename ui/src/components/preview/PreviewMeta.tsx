// PreviewMeta.tsx — compact header readout for the live-preview Panel (stream V).
// Replaces the old `preview.width × preview.height` header span (which read the
// renamed-away field — master §C-Risk-7). Reads data_width/data_height.
import type { PreviewInfo } from "../../types";

export function PreviewMeta({ preview }: { preview: PreviewInfo | null }) {
  if (!preview) return null;
  // Plain-text mirror of the line below, for `title`: on a phone this line
  // wraps rather than truncates (the panel header row is `min-w-0`/wraps all
  // the way up to the CSS grid item), so nothing here is ever actually hidden
  // -- but a `title` costs nothing and gives desktop hover the same summary in
  // one line.
  const parts = [
    `${preview.data_width}×${preview.data_height}`,
    `${preview.exposure_s}s`,
    `gain ${preview.gain}`,
    `bin ${preview.binning}`,
  ];
  if (preview.source === "nina") parts.push("NINA");
  if (preview.bayer_pattern) parts.push("OSC");
  return (
    <span className="mono text-[11px] text-dim" title={parts.join(" · ")}>
      {preview.data_width}×{preview.data_height} · {preview.exposure_s}s · gain {preview.gain} · bin{" "}
      {preview.binning}
      {preview.source === "nina" && <span className="text-warn ml-1">· NINA</span>}
      {preview.bayer_pattern && <span className="ml-1">· OSC</span>}
    </span>
  );
}
