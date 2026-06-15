// PreviewMeta.tsx — compact header readout for the live-preview Panel (stream V).
// Replaces the old `preview.width × preview.height` header span (which read the
// renamed-away field — master §C-Risk-7). Reads data_width/data_height.
import type { PreviewInfo } from "../../types";

export function PreviewMeta({ preview }: { preview: PreviewInfo | null }) {
  if (!preview) return null;
  return (
    <span className="mono text-[11px] text-dim">
      {preview.data_width}×{preview.data_height} · {preview.exposure_s}s · gain {preview.gain} · bin{" "}
      {preview.binning}
      {preview.source === "nina" && <span className="text-warn ml-1">· NINA</span>}
      {preview.bayer_pattern && <span className="ml-1">· OSC</span>}
    </span>
  );
}
