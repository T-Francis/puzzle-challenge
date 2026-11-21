"use strict";
// Moons puzzle — animated reconstruction (direct path). Static, no dependency; data in demo_data.js (window.DEMO).
(() => {
  const D = window.DEMO;
  const NS = "http://www.w3.org/2000/svg";
  const $ = (s) => document.querySelector(s);
  const svg = $("#scene");

  // ---------------------------------------------------------------- texts (EN / FR)
  const STR = {
    en: {
      title: "Moons · reconstruction", subtitle: "412 plain black pieces placed by shape alone",
      badge: "SIMULATION", badgeTip: "Hypotheses of the engine: the real puzzle is completed trial by trial",
      placedOf: "placed / 412", zoomIn: "Zoom in (+)", zoomOut: "Zoom out (−)", frameBoard: "Frame the puzzle (0)",
      frameAll: "See everything, sheets included", restart: "Restart", playPause: "Play / pause (Space)", speed: "Speed",
      language: "Passer en français", about: "About", timeline: "Timeline", sheet: "SHEET",
      caption: (p, c) => `Piece <b>${p}</b> → cell <b>${c}</b>`, finale: "puzzle complete", credit: "Moons — photograph by Marcella Giulia Pace",
      aboutTitle: "What you are watching",
      about1: "Every black piece of the “Moons” puzzle was photographed and modelled to a few tenths of a millimetre. The background is the real puzzle; the gold outlines are pieces really placed.",
      about2: "Each piece leaves its numbered sheet and flies to the cell where the engine places it, judging by shape alone: no picture, only the outline of the tabs and blanks.",
      about3: "This is a simulation: it assumes the engine corrects itself to the end. The real puzzle is completed piece by piece at the table, and every real answer corrects the engine.",
      keys: "<kbd>Space</kbd> play · <kbd>+</kbd> <kbd>−</kbd> zoom · <kbd>0</kbd> puzzle · wheel, drag, pinch to explore.",
      close: "Got it", sceneLabel: "Board and sheets",
      fitToggle: "Colour the pieces by fit quality", fitGood: "fits well", fitFair: "approximate", fitPoor: "does not fit",
      fitNote: "fit with the real board and the neighbouring pieces, measured after drawing",
    },
    fr: {
      title: "Lunes · reconstitution", subtitle: "412 pièces noires unies placées par la forme seule",
      badge: "SIMULATION", badgeTip: "Hypothèses du moteur : le vrai puzzle se termine essai par essai",
      placedOf: "posées / 412", zoomIn: "Zoomer (+)", zoomOut: "Dézoomer (−)", frameBoard: "Cadrer le puzzle (0)",
      frameAll: "Tout voir, plaques comprises", restart: "Recommencer", playPause: "Lecture / pause (Espace)", speed: "Vitesse",
      language: "Switch to English", about: "À propos", timeline: "Chronologie", sheet: "PLAQUE",
      caption: (p, c) => `Pièce <b>${p}</b> → case <b>${c}</b>`, finale: "puzzle terminé", credit: "Lunes — photographie de Marcella Giulia Pace",
      aboutTitle: "Ce que vous regardez",
      about1: "Chaque pièce noire du puzzle « Lunes » a été photographiée et modélisée à quelques dixièmes de millimètre près. Le fond est le vrai puzzle ; les contours dorés sont des pièces réellement posées.",
      about2: "Chaque pièce quitte sa plaque numérotée et vole vers la case où le moteur la place, d'après sa seule forme : aucune image, seulement le dessin des tenons et des creux.",
      about3: "C'est une simulation : elle suppose que le moteur se corrige jusqu'au bout. Le vrai puzzle se termine pièce par pièce, à la table, et chaque réponse réelle corrige le moteur.",
      keys: "<kbd>Espace</kbd> lecture · <kbd>+</kbd> <kbd>−</kbd> zoom · <kbd>0</kbd> puzzle · molette, glisser, pincer pour explorer.",
      close: "Compris", sceneLabel: "Puzzle et plaques",
      fitToggle: "Colorer les pièces selon leur emboîtement", fitGood: "s'emboîte bien", fitFair: "approximatif", fitPoor: "ne s'emboîte pas",
      fitNote: "emboîtement avec le vrai puzzle et les pièces voisines, mesuré après le dessin",
    },
  };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  const qs = new URLSearchParams(location.search);
  let lang = qs.get("lang") || store.get("moons.lang") || ((navigator.language || "en").toLowerCase().startsWith("fr") ? "fr" : "en");
  if (!STR[lang]) lang = "en";
  const T = (k) => STR[lang][k];
  function applyLang() {
    document.documentElement.lang = lang;
    document.title = T("title");
    document.querySelectorAll("[data-t]").forEach((e) => {
      const v = T(e.dataset.t);
      if (["keys", "about1", "about2", "about3"].includes(e.dataset.t)) e.innerHTML = v; else e.textContent = v;
    });
    document.querySelectorAll("[data-title]").forEach((e) => { e.title = T(e.dataset.title); e.setAttribute("aria-label", T(e.dataset.title)); });
    document.querySelectorAll("[data-label]").forEach((e) => e.setAttribute("aria-label", T(e.dataset.label)));
    $("#sceneLabel").textContent = T("sceneLabel");
    $("#lang").textContent = lang === "fr" ? "EN" : "FR";
    document.querySelectorAll(".sheet-title").forEach((e) => (e.textContent = `${T("sheet")} ${e.dataset.sheet}`));
  }

  // ---------------------------------------------------------------- data
  const PIECES = new Map(D.pieces.map((p) => [p.id, p]));
  const PATH = D.path;                       // [cell, piece, x, y, angle, fit rms (u) or null]
  const FIT = D.fit || null;                 // thresholds of the fit classes (u)
  const fitClass = (v) => (v == null || !FIT ? "" : v <= FIT.good_u ? "good" : v <= FIT.fair_u ? "fair" : "poor");
  const N = PATH.length;
  const SLOT = 140, HEAD = 120, GAP = 140;
  const ON_SHEET = 0.74;        // with its tabs a piece reaches up to 181 u for a 140 u slot: drawn smaller on its sheet, full size once flown

  // timeline: slow at first, then a cascade (several pieces in flight)
  const GAP0 = 1100, DECAY = 0.94, GAP_MIN = 120, DUR0 = 1300, DDECAY = 0.97, DUR_MIN = 650;
  const start = new Float64Array(N), dur = new Float64Array(N);
  { let t = 600; for (let i = 0; i < N; i++) { start[i] = t; dur[i] = Math.max(DUR_MIN, DUR0 * Math.pow(DDECAY, i)); t += Math.max(GAP_MIN, GAP0 * Math.pow(DECAY, i)); } }
  const T_END = N ? start[N - 1] + dur[N - 1] : 0;

  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pathD(flat) {
    let s = `M${flat[0]} ${flat[1]}`;
    for (let i = 2; i < flat.length; i += 2) s += `L${flat[i]} ${flat[i + 1]}`;
    return s + "Z";
  }

  // ---------------------------------------------------------------- layout (landscape: sheets on the right; portrait: below)
  let L;
  function layout() {
    const W = D.board.w, H = D.board.h;
    const portrait = innerHeight > innerWidth * 1.05;
    const rowsE = Math.max(...D.pieces.filter((p) => p.sheet === "E").map((p) => p.row));
    const pw = 10 * SLOT, ph = (r) => HEAD + r * SLOT;
    const ox = portrait ? 0 : W + 300, oy = portrait ? H + 300 : 0;
    const sheets = {
      A: { x: ox, y: oy, rows: 10 }, B: { x: ox + pw + GAP, y: oy, rows: 10 },
      C: { x: ox, y: oy + ph(10) + GAP, rows: 10 }, D: { x: ox + pw + GAP, y: oy + ph(10) + GAP, rows: 10 },
      E: { x: ox, y: oy + 2 * (ph(10) + GAP), rows: rowsE },
    };
    const sw = Math.max(W, ox + 2 * pw + GAP), sh = Math.max(H, sheets.E.y + ph(rowsE));
    L = { W, H, portrait, sheets, pw, ph, scene: { x: -120, y: -120, w: sw + 240, h: sh + 240 } };
  }

  // ---------------------------------------------------------------- scene
  const pieceEl = new Map();
  function pieceColours() {
    // drawn pieces take the colour of the black pieces as the photo renders them
    const [r, g, b] = D.board.piece_rgb || [40, 38, 40];
    const shade = (f) => `rgb(${Math.min(255, Math.round(r * f))},${Math.min(255, Math.round(g * f))},${Math.min(255, Math.round(b * f))})`;
    const stops = document.querySelectorAll("#pieceFill stop");
    stops[0].setAttribute("stop-color", shade(1.5));
    stops[1].setAttribute("stop-color", shade(1.0));
    stops[2].setAttribute("stop-color", shade(0.72));
  }
  function build() {
    layout();
    pieceColours();
    const board = $("#board"), sheets = $("#sheets"), pieces = $("#pieces");
    board.replaceChildren(); sheets.replaceChildren();
    el("rect", { x: -16, y: -16, width: L.W + 32, height: L.H + 32, rx: 24, class: "frame-glow" }, board);
    el("image", { href: D.board.background, x: 0, y: 0, width: L.W, height: L.H, preserveAspectRatio: "none" }, board);
    const holes = el("g", { id: "holes" }, board);
    for (const h of D.holes) el("path", { d: pathD(h), class: "hole" }, holes);
    for (const [k, s] of Object.entries(L.sheets)) {
      el("rect", { x: s.x - 20, y: s.y, width: L.pw + 40, height: L.ph(s.rows) + 20, rx: 30, class: "sheet-bg" }, sheets);
      el("text", { x: s.x + 20, y: s.y + 85, class: "sheet-title", "data-sheet": k }, sheets);
    }
    for (const p of D.pieces) {
      p.path = p.path || pathD(p.d);
      const t = slotT(p);
      el("path", { d: p.path, class: "ghost", transform: `translate(${t.x} ${t.y}) rotate(${t.a}) scale(${ON_SHEET})` }, sheets);
      if (!pieceEl.has(p.id)) {
        const g = el("g", { class: "piece" }, pieces);
        el("path", { d: p.path }, g);
        pieceEl.set(p.id, g);
      }
    }
    for (const p of PATH) { const c = fitClass(p[5]); if (c) pieceEl.get(p[1]).dataset.fit = c; }
    for (const [pid, x, y, a] of D.real) {
      const g = pieceEl.get(pid);
      g.classList.add("real");
      setT(g, { x, y, a });
    }
    applyLang();
  }
  function slotT(p) {
    const s = L.sheets[p.sheet];
    return { x: s.x + (p.col - 0.5) * SLOT, y: s.y + HEAD + (p.row - 0.5) * SLOT, a: 90 * p.k };
  }
  function setT(g, t, s = 1) {
    g.setAttribute("transform", `translate(${t.x.toFixed(1)} ${t.y.toFixed(1)}) rotate(${t.a.toFixed(2)})${s !== 1 ? ` scale(${s.toFixed(3)})` : ""}`);
  }

  // ---------------------------------------------------------------- timeline state
  const state = new Int8Array(N);             // 0 on its sheet, 1 flying, 2 placed
  let vt = 0, playing = false, speed = 1, finaleDone = false;
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const short = (a) => ((a % 360) + 540) % 360 - 180;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function flightPos(i, k) {
    const [, pid, x, y, a] = PATH[i];
    const from = slotT(PIECES.get(pid)), to = { x, y, a };
    const dx = to.x - from.x, dy = to.y - from.y, dist = Math.hypot(dx, dy) || 1;
    const side = i % 2 ? 1 : -1;
    const cx = (from.x + to.x) / 2 - (dy / dist) * dist * 0.22 * side, cy = (from.y + to.y) / 2 + (dx / dist) * dist * 0.22 * side - dist * 0.1;
    let da = short(to.a - from.a);
    if (dur[i] > 1000) da += 360 * (da >= 0 ? 1 : -1);            // the first pieces spin once
    const e = ease(k);
    return { t: { x: (1 - e) * (1 - e) * from.x + 2 * (1 - e) * e * cx + e * e * to.x,
                  y: (1 - e) * (1 - e) * from.y + 2 * (1 - e) * e * cy + e * e * to.y, a: from.a + da * e },
             s: lerp(ON_SHEET, 1, e) + 0.45 * Math.sin(Math.PI * e) };
  }
  function render(effects) {
    let placed = D.real.length, captionI = -1;
    for (let i = 0; i < N; i++) {
      const [, pid, x, y, a] = PATH[i], g = pieceEl.get(pid);
      const k = (vt - start[i]) / dur[i];
      const ns = k <= 0 ? 0 : k >= 1 ? 2 : 1;
      if (ns === 2) placed++;
      if (ns === 1) {
        if (state[i] !== 1) { g.parentNode.appendChild(g); g.classList.add("flying"); }
        const f = flightPos(i, k); setT(g, f.t, f.s);
        if (dur[i] > 900) captionI = i;
      } else if (ns !== state[i] || !effects) {
        g.classList.remove("flying");
        if (ns === 2) {
          setT(g, { x, y, a });
          if (effects && state[i] === 1 && !reduced) {
            g.classList.remove("land"); void g.getBBox(); g.classList.add("land");
            if (dur[i] > 900 || i % 6 === 0) burst(x, y);
          }
        } else setT(g, slotT(PIECES.get(pid)), ON_SHEET);
      }
      if (ns !== state[i]) g.classList.toggle("placed", ns === 2);
      state[i] = ns;
    }
    $("#placed").textContent = placed;
    $("#count").textContent = `${placed} / ${D.real.length + N}`;
    $("#slider").value = T_END ? Math.round((1000 * vt) / T_END) : 0;
    $("#progress i").style.width = `${T_END ? (100 * vt) / T_END : 0}%`;
    $("#board").classList.toggle("holes-done", vt >= T_END);
    const cap = $("#caption");
    if (captionI >= 0) { cap.innerHTML = T("caption")(PATH[captionI][1], PATH[captionI][0]); cap.classList.remove("hidden"); }
    else cap.classList.add("hidden");
  }

  // ---------------------------------------------------------------- effects
  function tween(ms, fn) {
    const t0 = performance.now();
    return new Promise((res) => {
      const tick = (now) => { const k = Math.min(1, (now - t0) / ms); fn(k); k < 1 ? requestAnimationFrame(tick) : res(); };
      requestAnimationFrame(tick);
    });
  }
  function burst(x, y) {
    const ring = el("circle", { cx: x, cy: y, r: 10, class: "ring", stroke: "#6ee7ff" }, $("#fx"));
    tween(650, (k) => {
      const e = easeOut(k);
      ring.setAttribute("r", 10 + 110 * e); ring.setAttribute("stroke-opacity", 1 - k); ring.setAttribute("stroke-width", 8 * (1 - k) + 1);
    }).then(() => ring.remove());
  }
  async function finale() {
    finaleDone = true;
    await animCam(frame(boardBox(), 1), 900);
    const fx = $("#fx");
    if (!reduced) {
      const sweep = el("rect", { x: -L.W, y: -40, width: L.W * 0.6, height: L.H + 80, fill: "url(#sweep)" }, fx);
      tween(1700, (k) => sweep.setAttribute("x", -L.W * 0.6 + L.W * 1.6 * easeOut(k))).then(() => sweep.remove());
    }
    $("#finale").classList.remove("hidden");
    if (!reduced) {
      const colors = ["#ff8a3d", "#ff4d6d", "#ffd36e", "#6ee7ff", "#b47cff", "#e9e1c9", "#7aa7ff"];
      const parts = [...Array(160)].map(() => ({
        x: L.W / 2 + (Math.random() - 0.5) * 400, y: L.H * 0.45, vx: (Math.random() - 0.5) * 2600, vy: -900 - Math.random() * 2200,
        r: 8 + Math.random() * 14, c: colors[(Math.random() * colors.length) | 0], s: Math.random() * 360,
      }));
      parts.forEach((q) => (q.e = el("rect", { width: q.r, height: q.r * 0.6, fill: q.c, rx: 2 }, fx)));
      await tween(3200, (k) => {
        const t = k * 3.2;
        for (const q of parts) {
          q.e.setAttribute("transform", `translate(${q.x + q.vx * t * 0.6} ${q.y + q.vy * t + 1400 * t * t}) rotate(${q.s + 600 * t})`);
          q.e.setAttribute("opacity", Math.max(0, 1 - k * 1.1));
        }
      });
      parts.forEach((q) => q.e.remove());
    }
    setTimeout(() => $("#finale").classList.add("hidden"), 2500);
  }

  // ---------------------------------------------------------------- playback
  let last = 0;
  function loop(now) {
    if (!playing) return;
    const dt = Math.min(100, now - (last || now));
    last = now;
    vt = Math.min(T_END, vt + dt * speed);
    render(true);
    if (vt >= T_END) { pause(); if (!finaleDone) finale(); return; }
    requestAnimationFrame(loop);
  }
  function play() {
    if (vt >= T_END) { vt = 0; finaleDone = false; render(false); }
    playing = true; last = 0; $("#play").textContent = "⏸";
    $("#finale").classList.add("hidden");
    requestAnimationFrame(loop);
  }
  function pause() { playing = false; $("#play").textContent = "▶"; }
  function seek(v) { pause(); vt = Math.max(0, Math.min(T_END, v)); finaleDone = vt >= T_END; render(false); }

  // ---------------------------------------------------------------- camera (viewBox): zoom, drag, pinch
  const cam = { x: 0, y: 0, w: 100, h: 100 };
  const applyCam = () => svg.setAttribute("viewBox", `${cam.x} ${cam.y} ${cam.w} ${cam.h}`);
  const aspect = () => svg.clientWidth / Math.max(1, svg.clientHeight);
  function frame(box, pad = 1.08) {
    let w = box.w * pad, h = box.h * pad;
    const a = aspect();
    if (w / h < a) w = h * a; else h = w / a;
    return { x: box.x + box.w / 2 - w / 2, y: box.y + box.h / 2 - h / 2, w, h };
  }
  function boardBox() {
    const extra = innerWidth < 700 ? 0.22 : 0.12;
    return { x: -40, y: -40 - L.H * extra * 0.4, w: L.W + 80, h: L.H * (1 + extra) + 80 };
  }
  function setCam(b) { Object.assign(cam, b); applyCam(); }
  function toWorld(cx, cy) { const pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy; return pt.matrixTransform(svg.getScreenCTM().inverse()); }
  function zoomAt(wx, wy, f) {
    const nw = Math.max(160, Math.min(L.scene.w * 1.6, cam.w * f)), r = nw / cam.w;
    setCam({ x: wx - (wx - cam.x) * r, y: wy - (wy - cam.y) * r, w: nw, h: cam.h * r });
  }
  let camAnim = 0;
  function animCam(to, ms) {
    const from = { ...cam }, id = ++camAnim;
    return tween(ms, (k) => {
      if (id !== camAnim) return;
      const e = ease(k);
      setCam({ x: lerp(from.x, to.x, e), y: lerp(from.y, to.y, e), w: lerp(from.w, to.w, e), h: lerp(from.h, to.h, e) });
    });
  }
  svg.addEventListener("wheel", (ev) => { ev.preventDefault(); camAnim++; const p = toWorld(ev.clientX, ev.clientY); zoomAt(p.x, p.y, Math.exp(ev.deltaY * 0.0015)); }, { passive: false });
  const ptrs = new Map();
  let pinch = null;
  svg.addEventListener("pointerdown", (ev) => {
    svg.setPointerCapture(ev.pointerId);
    ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }; }
    svg.classList.add("dragging");
  });
  svg.addEventListener("pointermove", (ev) => {
    if (!ptrs.has(ev.pointerId)) return;
    const prev = ptrs.get(ev.pointerId);
    ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    camAnim++;
    if (ptrs.size === 1) {
      const s = cam.w / svg.clientWidth * Math.max(1, aspect() / (cam.w / cam.h));
      setCam({ ...cam, x: cam.x - (ev.clientX - prev.x) * s, y: cam.y - (ev.clientY - prev.y) * s });
    } else if (ptrs.size === 2 && pinch) {
      const [a, b] = [...ptrs.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const w = toWorld(mx, my);
      zoomAt(w.x, w.y, pinch.d / d);
      const w0 = toWorld(pinch.mx, pinch.my), w1 = toWorld(mx, my);
      setCam({ ...cam, x: cam.x - (w1.x - w0.x), y: cam.y - (w1.y - w0.y) });
      pinch = { d, mx, my };
    }
  });
  const endPtr = (ev) => { ptrs.delete(ev.pointerId); if (ptrs.size < 2) pinch = null; if (!ptrs.size) svg.classList.remove("dragging"); };
  svg.addEventListener("pointerup", endPtr);
  svg.addEventListener("pointercancel", endPtr);
  svg.addEventListener("dblclick", (ev) => { camAnim++; const p = toWorld(ev.clientX, ev.clientY); zoomAt(p.x, p.y, 0.45); });

  // ---------------------------------------------------------------- controls
  $("#play").onclick = () => (playing ? pause() : play());
  $("#restart").onclick = () => { seek(0); play(); };
  $("#slider").oninput = (ev) => seek((+ev.target.value / 1000) * T_END);
  $("#speed").onchange = (ev) => { speed = +ev.target.value; };
  $("#lang").onclick = () => { lang = lang === "fr" ? "en" : "fr"; store.set("moons.lang", lang); applyLang(); render(false); };
  function setFit(on) {
    document.body.classList.toggle("fit-on", on);
    $("#fitBtn").setAttribute("aria-pressed", on ? "true" : "false");
    $("#fitLegend").classList.toggle("hidden", !on);
    store.set("moons.fit", on ? "1" : "0");
  }
  if (!FIT) $("#fitBtn").hidden = true;
  $("#fitBtn").onclick = () => setFit(!document.body.classList.contains("fit-on"));
  $("#aboutBtn").onclick = () => $("#about").classList.remove("hidden");
  $("#aboutClose").onclick = () => $("#about").classList.add("hidden");
  $("#about").onclick = (ev) => { if (ev.target.id === "about") $("#about").classList.add("hidden"); };
  $("#zIn").onclick = () => { camAnim++; zoomAt(cam.x + cam.w / 2, cam.y + cam.h / 2, 0.7); };
  $("#zOut").onclick = () => { camAnim++; zoomAt(cam.x + cam.w / 2, cam.y + cam.h / 2, 1.4); };
  $("#zBoard").onclick = () => animCam(frame(boardBox(), 1), 500);
  $("#zAll").onclick = () => animCam(frame(L.scene, 1), 500);
  addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
    if (ev.key === " ") { ev.preventDefault(); playing ? pause() : play(); }
    else if (ev.key === "+" || ev.key === "=") $("#zIn").click();
    else if (ev.key === "-") $("#zOut").click();
    else if (ev.key === "0") $("#zBoard").click();
    else if (ev.key === "Escape") $("#about").classList.add("hidden");
  });
  let lastPortrait;
  addEventListener("resize", () => {
    const portrait = innerHeight > innerWidth * 1.05;
    if (portrait !== lastPortrait) { lastPortrait = portrait; build(); render(false); setCam(frame(portrait ? boardBox() : L.scene, 1.02)); }
    else applyCam();
  });

  // ---------------------------------------------------------------- stars
  (function stars() {
    const cv = $("#stars"), ctx = cv.getContext("2d");
    const draw = () => {
      const r = devicePixelRatio || 1;
      cv.width = innerWidth * r; cv.height = innerHeight * r; cv.style.width = innerWidth + "px"; cv.style.height = innerHeight + "px";
      ctx.scale(r, r);
      for (let i = 0; i < 260; i++) {
        const x = Math.random() * innerWidth, y = Math.random() * innerHeight, s = Math.random() * 1.4 + 0.2;
        ctx.fillStyle = `rgba(${200 + Math.random() * 55},${210 + Math.random() * 45},255,${0.2 + Math.random() * 0.7})`;
        ctx.beginPath(); ctx.arc(x, y, s, 0, 7); ctx.fill();
      }
    };
    draw(); addEventListener("resize", draw);
  })();

  // ---------------------------------------------------------------- start (?t=0..1 position, ?speed=V, ?play=1, ?frame=all|board, ?lang=en|fr)
  build();
  setFit(!!FIT && (qs.get("fit") ?? store.get("moons.fit")) === "1");
  if (FIT && FIT.classes) {
    $("#fitN-good").textContent = FIT.classes.good; $("#fitN-fair").textContent = FIT.classes.fair; $("#fitN-poor").textContent = FIT.classes.poor;
  }
  lastPortrait = L.portrait;
  if (qs.has("speed")) { speed = +qs.get("speed") || 1; $("#speed").value = String(speed); }
  seek((+(qs.get("t") || 0)) * T_END);
  const wide = !L.portrait && aspect() > 1.25;
  setCam(frame(qs.get("frame") === "all" || (wide && qs.get("frame") !== "board") ? L.scene : boardBox(), 1.02));
  if (qs.get("play") === "1") play();
})();
