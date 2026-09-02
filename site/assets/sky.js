/* The hero: a tilted sky dome with cloud drifting across it and the scope's
   pointing marked — the one picture that is specific to AstroDeck rather than
   to software in general. Deliberately quiet: it sits behind type, so it stays
   low-contrast and slow, and it stops entirely for reduced-motion. */
(function () {
  "use strict";
  var cv = document.getElementById("skycanvas");
  if (!cv || !cv.getContext) return;

  var ctx = cv.getContext("2d");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var W = 0, H = 0, dpr = 1;
  var stars = [], clouds = [];

  function accent() {
    var v = getComputedStyle(document.documentElement).getPropertyValue("--accent");
    return (v || "#4DD9E8").trim();
  }
  function isLight() {
    var g = getComputedStyle(document.documentElement).getPropertyValue("--ground").trim();
    // the light ground is a pale value; sample its first hex pair
    return /^#[fFeE]/.test(g);
  }

  function seedRandom(seed) {
    // deterministic, so the sky does not reshuffle on every resize
    var s = seed >>> 0;
    return function () {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }

  function build() {
    var rnd = seedRandom(20260826);
    stars = [];
    var n = Math.round(Math.min(520, (W * H) / 3200));
    for (var i = 0; i < n; i++) {
      stars.push({
        x: rnd(), y: rnd(),
        r: 0.45 + rnd() * rnd() * 1.8,
        a: 0.3 + rnd() * 0.7,
        tw: rnd() * Math.PI * 2,
        sp: 0.4 + rnd() * 1.1
      });
    }
    clouds = [];
    for (var c = 0; c < 5; c++) {
      clouds.push({
        x: rnd(), y: 0.22 + rnd() * 0.5,
        rx: 0.13 + rnd() * 0.2, ry: 0.05 + rnd() * 0.09,
        a: 0.1 + rnd() * 0.16,
        sp: 0.0000075 + rnd() * 0.0000135
      });
    }
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    var r = cv.getBoundingClientRect();
    W = Math.max(1, Math.round(r.width));
    H = Math.max(1, Math.round(r.height));
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
  }

  /* The dome: a hemisphere seen from outside and slightly above, so altitude
     rings read as nested ellipses and the horizon is the outermost one. */
  function domeGeom() {
    // Phones stack the copy down the whole hero, so a dome centred behind it
    // draws rings and the crosshair through the lede. Sit it on the bottom
    // edge instead: a horizon arc rising under the buttons, clear of the text.
    var narrow = W < 760;
    var cx = W * (narrow ? 0.5 : 0.72);
    var cy = narrow ? H : H * 0.56;
    // keep the horizon ring inside the frame: a curve cut by the viewport
    // edge reads as clipped rather than bled
    var rx = narrow ? W * 0.5 : Math.min(W * 0.33, 372);
    // on a phone the arc must also clear the buttons, whatever the hero
    // height came out as; the bottom padding is at least 10vh
    var ry = narrow ? Math.min(rx * 0.44, H * 0.09) : rx * 0.44;
    return { cx: cx, cy: cy, rx: rx, ry: ry };
  }

  function drawDome(t, ac, light) {
    var g = domeGeom();
    ctx.save();
    var halo = ctx.createRadialGradient(g.cx, g.cy, 0, g.cx, g.cy, g.rx * 1.25);
    halo.addColorStop(0, light ? "rgba(10,110,126,0.07)" : "rgba(77,217,232,0.075)");
    halo.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(g.cx - g.rx * 1.3, g.cy - g.rx * 1.3, g.rx * 2.6, g.rx * 2.6);
    ctx.lineWidth = 1;

    // altitude rings: horizon, 30, 60, and the zenith point
    var alts = [0, 30, 60];
    for (var i = 0; i < alts.length; i++) {
      var k = Math.cos((alts[i] * Math.PI) / 180);
      ctx.beginPath();
      ctx.ellipse(g.cx, g.cy, g.rx * k, g.ry * k, 0, 0, Math.PI * 2);
      ctx.strokeStyle = ac;
      ctx.globalAlpha = (i === 0 ? 0.55 : 0.26) * (light ? 1.4 : 1);
      ctx.stroke();
    }

    // azimuth spokes every 45 degrees
    ctx.globalAlpha = 0.17 * (light ? 1.5 : 1);
    for (var a = 0; a < 360; a += 45) {
      var rad = (a * Math.PI) / 180;
      ctx.beginPath();
      ctx.moveTo(g.cx, g.cy);
      ctx.lineTo(g.cx + Math.cos(rad) * g.rx, g.cy + Math.sin(rad) * g.ry);
      ctx.stroke();
    }

    // zenith
    ctx.globalAlpha = 0.5 * (light ? 1.3 : 1);
    ctx.beginPath();
    ctx.arc(g.cx, g.cy, 1.6, 0, Math.PI * 2);
    ctx.fillStyle = ac;
    ctx.fill();

    // where the scope is pointing: a slow arc across the dome, crosshaired
    var ang = -0.9 + Math.sin(t * 0.000035) * 0.55;
    var alt = 0.56 + Math.sin(t * 0.000021) * 0.12;
    var px = g.cx + Math.cos(ang) * g.rx * (1 - alt);
    var py = g.cy + Math.sin(ang) * g.ry * (1 - alt);
    ctx.globalAlpha = 0.85;
    ctx.strokeStyle = ac;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(px, py, 7, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.moveTo(px - 13, py); ctx.lineTo(px - 9.5, py);
    ctx.moveTo(px + 9.5, py); ctx.lineTo(px + 13, py);
    ctx.moveTo(px, py - 13); ctx.lineTo(px, py - 9.5);
    ctx.moveTo(px, py + 9.5); ctx.lineTo(px, py + 13);
    ctx.stroke();
    ctx.restore();
  }

  function draw(t) {
    var ac = accent();
    var light = isLight();
    ctx.clearRect(0, 0, W, H);

    // stars, only worth drawing on the dark ground
    if (!light) {
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        var tw = reduce ? 1 : 0.72 + 0.28 * Math.sin(t * 0.0009 * s.sp + s.tw);
        ctx.globalAlpha = s.a * tw;
        ctx.beginPath();
        ctx.arc(s.x * W, s.y * H, s.r, 0, Math.PI * 2);
        ctx.fillStyle = "#DDE7F7";
        ctx.fill();
      }
    }

    // cloud: what the GOES model would be shading out
    for (var c = 0; c < clouds.length; c++) {
      var q = clouds[c];
      var drift = reduce ? 0 : (t * q.sp) % 1.4;
      var x = ((q.x + drift) % 1.4 - 0.2) * W;
      var y = q.y * H;
      var rx = q.rx * W, ry = q.ry * H;
      // Fill an ELLIPSE, not a rect. A radial gradient of radius rx clipped by
      // a rect only ry*3.2 tall cuts the falloff off mid-fade, and the hard
      // edges read as horizontal bands straight across the hero.
      var tint = light ? "10,25,45" : "150,175,215";
      ctx.save();
      ctx.globalAlpha = light ? 0.5 : 1;
      ctx.translate(x, y);
      ctx.scale(1, ry / rx);
      var grd = ctx.createRadialGradient(0, 0, 0, 0, 0, rx);
      grd.addColorStop(0, "rgba(" + tint + "," + q.a + ")");
      grd.addColorStop(0.55, "rgba(" + tint + "," + (q.a * 0.45).toFixed(3) + ")");
      grd.addColorStop(1, "rgba(" + tint + ",0)");
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(0, 0, rx, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    ctx.globalAlpha = 1;
    drawDome(t, ac, light);
    ctx.globalAlpha = 1;
  }

  var raf = 0;
  function loop(t) { draw(t); raf = requestAnimationFrame(loop); }

  function start() {
    resize();
    if (raf) cancelAnimationFrame(raf);
    if (reduce) { draw(12000); return; }
    raf = requestAnimationFrame(loop);
  }

  var rt;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(start, 140);
  });
  // repaint when the theme flips, so the sky matches its ground
  window.addEventListener("astrodeck:theme", function () { draw(performance.now()); });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
