/* Three states, not two: an explicit choice is stamped on <html>, and no stamp
   means "follow the OS". The toggle cycles dark -> light -> system. */
(function () {
  "use strict";
  var KEY = "astrodeck-theme";
  var root = document.documentElement;

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function apply(v) {
    if (v === "dark" || v === "light") root.setAttribute("data-theme", v);
    else root.removeAttribute("data-theme");
  }
  function label(v) { return v === "dark" ? "Dark" : v === "light" ? "Light" : "Auto"; }

  apply(stored());

  function wire() {
    var btns = document.querySelectorAll("[data-theme-toggle]");
    if (!btns.length) return;
    function paint() {
      var v = stored() || "auto";
      for (var i = 0; i < btns.length; i++) {
        btns[i].textContent = label(v);
        btns[i].setAttribute("aria-label", "Theme: " + label(v) + ". Change theme.");
      }
    }
    paint();
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        var v = stored() || "auto";
        var next = v === "dark" ? "light" : v === "light" ? "auto" : "dark";
        try {
          if (next === "auto") localStorage.removeItem(KEY);
          else localStorage.setItem(KEY, next);
        } catch (e) { /* private window: the choice just does not persist */ }
        apply(next === "auto" ? null : next);
        paint();
        window.dispatchEvent(new Event("astrodeck:theme"));
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
