// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
/* Apply before paint; keep the controls usable when storage is unavailable. */
(function () {
  "use strict";
  var key = "astrodeck-theme";
  var root = document.documentElement;
  var choice = "system";
  try { choice = localStorage.getItem(key) || "system"; } catch (error) {}
  if (choice !== "light" && choice !== "dark") choice = "system";
  function apply() {
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
  }
  apply();
  function wire() {
    var toggle = document.querySelector("[data-theme-toggle]");
    if (toggle) toggle.hidden = false;
    function paint() {
      if (!toggle) return;
      var name = choice.charAt(0).toUpperCase() + choice.slice(1);
      var next = choice === "system" ? "light" : choice === "light" ? "dark" : "system";
      toggle.textContent = "Theme: " + name;
      toggle.setAttribute("aria-label", "Theme: " + name + ". Switch to " + next + " theme.");
    }
    if (toggle) toggle.addEventListener("click", function () {
      choice = choice === "system" ? "light" : choice === "light" ? "dark" : "system";
      apply();
      paint();
      try {
        if (choice === "system") localStorage.removeItem(key);
        else localStorage.setItem(key, choice);
      } catch (error) {}
    });
    paint();
    var menu = document.querySelector("[data-menu-toggle]");
    var nav = document.getElementById("site-nav");
    if (menu && nav) {
      root.setAttribute("data-nav-ready", "");
      function close() {
        menu.setAttribute("aria-expanded", "false");
        nav.removeAttribute("data-open");
        menu.textContent = "Menu";
      }
      menu.addEventListener("click", function () {
        if (menu.getAttribute("aria-expanded") === "true") close();
        else {
          menu.setAttribute("aria-expanded", "true");
          nav.setAttribute("data-open", "");
          menu.textContent = "Close";
        }
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && menu.getAttribute("aria-expanded") === "true") {
          close();
          menu.focus();
        }
      });
      nav.addEventListener("click", function (event) {
        if (event.target.closest("a")) close();
      });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();