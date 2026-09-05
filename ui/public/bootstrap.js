// Anti-flash pre-paint bootstrap. Loaded as an EXTERNAL blocking <script> from
// index.html (not inline) so the Content-Security-Policy can drop
// script-src 'unsafe-inline' (OPEN-012). It still runs before first paint, so a
// night-mode / dimmed reload never flashes day/full-brightness.
//
// Keep the clamp floor (0.5), night default (1), and the scrim formula in
// lockstep with store.ts (clampBright / applyBrightnessVars); this pre-paint
// copy exists only so first paint never flashes.
(function () {
  try {
    var d = document.documentElement;
    var night = localStorage.getItem("astrodeck-night") === "1";
    if (night) d.classList.add("night");
    var clamp = function (v) { return Math.min(1, Math.max(0.5, v)); };
    var num = function (k, def) { var n = Number(localStorage.getItem(k)); return isFinite(n) && n > 0 ? n : def; };
    var b = clamp(night ? num("astrodeck-bright-night", 1) : num("astrodeck-bright-day", 1));
    d.style.setProperty("--screen-brightness", String(b));
    d.style.setProperty("--scrim-opacity", String(Math.max(0, 0.5 - b)));
  } catch (e) { /* localStorage may be unavailable; first paint falls back to :root defaults */ }
})();
