"use strict";
// Workshop ("atelier") of the Moons puzzle: one piece at a time, fits / does not fit. Talks to /api/*.
(() => {
  const $ = (s) => document.querySelector(s);
  const NS = "http://www.w3.org/2000/svg";
  const PAGE_VERSION = document.querySelector('meta[name="app-version"]').content;
  const BOARD_IMG = document.querySelector('meta[name="board-image"]').content;
  const pid = (id) => id[0] + Number(id.slice(1));        // "D051" -> "D51", as written on the sheets
  const MAX_PHOTO_MB = 80;                                   // = server/app.py MAX_PHOTO

  // ------------------------------------------------------------------ texts (French by default, English available)
  const STR = {
    fr: {
      appName: "Atelier · Lunes", starting: "Démarrage…", loginPasskey: "Se connecter avec l'empreinte", password: "Mot de passe",
      putBackDone: "C'est fait", login: "Se connecter", showPw: "Afficher", hidePw: "Masquer", wrongPassword: "Mot de passe incorrect.", tooMany: "Trop d'essais : réessaie dans 15 minutes.",
      take: "Prendre", turn: "Tourner", place: "Poser", sheet: "plaque", line: "ligne", column: "colonne",
      turns: ["Ne pas la tourner", "¼ de tour vers la droite", "Demi-tour", "¼ de tour vers la gauche"],
      otherWay: "Rentre dans un autre sens", skip: "Passer la case", undo: "Annuler", noFit: "ne rentre pas", fits: "rentre",
      whichWay: "Dans quel sens est-elle rentrée ?", cancel: "Fermer", undoTitle: "Annuler la dernière réponse ?",
      undoConfirm: "Annuler la réponse", menu: "Menu", sendPhoto: "Envoyer une photo du puzzle",
      photoHint: "De face, au-dessus du centre du puzzle, lumière douce, sans flash. Le mieux : la prendre avec l'appli Appareil photo en pleine résolution, puis la choisir ici dans la galerie.", addPasskey: "Activer l'empreinte",
      logout: "Se déconnecter", close: "Fermer", later: "Plus tard", pkOfferTitle: "Se connecter avec l'empreinte ?",
      pkOfferText: "La prochaine fois, l'empreinte (ou le code du téléphone) suffira.", noProposal: "Plus aucune proposition pour l'instant.",
      try_: (k) => (k > 1 ? `Essai ${k} dans cette case` : "1er essai dans cette case"), chance: "chance", buckets: { A: "fiable", B: "possible", C: "faible" },
      counts: (c) => `${c.placed} posées · ${c.left} restantes`,
      putBack: (p) => `Remets <b>${pid(p.piece)}</b> : plaque ${p.sheet} · ligne ${p.row} · col. ${p.col}, <b>dans ce sens</b>.`,
      removeBack: (p) => `Retire <b>${pid(p.piece)}</b> de ${p.cell} et remets-la : plaque ${p.sheet} · ligne ${p.row} · col. ${p.col}, <b>dans ce sens</b>.`,
      weak: "Plus de proposition fiable : celles-ci sont faibles. Une photo du puzzle aiderait (menu).",
      photoDue: (n) => `${n} pièces posées depuis la dernière photo : pense à envoyer une photo du puzzle (menu).`,
      undoText: (u) => (u.type === "skip" ? `Case ${u.cell} passée.` : u.type === "does_not_fit" ? `${pid(u.piece)} ne rentrait pas en ${u.cell}.`
        : u.type === "remove" ? `${pid(u.piece)} retirée de ${u.cell} (elle y sera de nouveau notée posée).` : u.type === "manual" ? `${pid(u.piece)} posée par toi en ${u.cell}.` : `${pid(u.piece)} posée en ${u.cell}.`),
      fixTitle: "Corriger", removeOpen: "Retirer une pièce posée", manualOpen: "J'ai posé une pièce moi-même",
      removeFilterPh: "Case (7 8) ou pièce (D51)", removeNone: "Aucune pièce posée ne correspond.", removeItem: (x) => `${pid(x.piece)} en ${x.cell}${x.manual ? " (posée par toi)" : ""}`,
      removeAsk: (x) => `Retirer <b>${pid(x.piece)}</b> de <b>${x.cell}</b> ? Pourquoi ?`,
      removeWrong: "Elle avait du jeu / n'allait pas ici", removeWrongHint: "Elle ne sera plus proposée dans cette case.",
      removeOther: "Je la retire seulement", removeOtherHint: "Erreur de saisie ou autre raison : elle pourra être reproposée ici.",
      checkToo: (l) => `Vérifie aussi ${l.map((x) => `${pid(x.piece)} en ${x.cell}`).join(", ")} : posée${l.length > 1 ? "s" : ""} ensuite à côté, en s'appuyant sur elle.`,
      needsPhoto: "Cette case sera de nouveau proposée après la prochaine photo du puzzle.",
      pieceLabel: "Pièce", next: "Suivant", manualWhichWay: "Dans quel sens l'as-tu posée ? (vue comme sur le puzzle)", likely: "le plus probable",
      manualErr: { cell: "Cette case n'existe pas.", piece: "Pièce inconnue : écris la plaque et le numéro, par ex. D51.", cell_filled: "Cette case est déjà occupée.",
        piece_placed: (d) => `Cette pièce est déjà notée posée en ${d.cell}.` },
      notSaved: "Réponse non enregistrée (réseau).", retry: "Réessayer", stale: "Déjà pris en compte : voici la proposition à jour.",
      badFit: "Attention : d'après les mesures, cette pièce s'emboîte mal ici. Vérifie qu'elle est bien à plat et bien orientée.",
      photoSent: "Photo envoyée : analyse en cours, tu peux continuer à poser.", photoError: "Photo non envoyée.", photoBusy: "Une photo est déjà en cours d'analyse.",
      photoSending: (mb) => `Envoi de la photo (${mb} Mo)… garde l'appli ouverte.`, photoNotJpeg: "Photo non envoyée : ce n'est pas un JPEG (règle l'appareil photo sur JPEG, pas HEIF).",
      photoTooBig: (mb) => `Photo trop lourde (${mb} Mo, maximum 80) : reprends-la en résolution normale ou 50 Mpx.`,
      recalRunning: (s) => `Photo reçue : analyse en cours (${({ start: "démarrage", photo: "lecture", board: "plateau", sharpness: "netteté", model: "modèle", sockets: "trous", scores: "calcul", switch: "mise en service" })[s.stage] || "…"}, ${Math.round(s.seconds / 60)} min). Tu peux continuer à poser.`,
      recalOk: "Photo prise en compte : les propositions repartent du puzzle photographié.",
      recalNo: {
        frame: "Photo refusée : le cadre du puzzle n'est pas entièrement visible. Reprends-la de face, avec un peu de marge tout autour (menu).",
        blur: "Photo refusée : elle est floue. Tiens le téléphone immobile (appuyé, ou avec le retardateur) et reprends-la (menu).",
        cells: (d) => "Photo refusée : le puzzle ne correspond pas aux réponses notées." + ((d && d.empty_on_photo && d.empty_on_photo.length) ? ` Vides sur la photo mais notées posées : ${d.empty_on_photo.join(", ")}.` : "") + ((d && d.filled_on_photo && d.filled_on_photo.length) ? ` Remplies sur la photo mais pas notées : ${d.filled_on_photo.join(", ")}.` : "") + " Vérifie ces cases, puis reprends la photo (menu).",
        sockets: "Photo refusée : les trous y sont moins nets que sur la précédente (lumière, reflets ?). Reprends-la sous une lumière douce (menu).",
        interrupted: "La photo n'a pas pu être analysée (serveur redémarré). Renvoie-la (menu).",
        error: "La photo n'a pas pu être analysée. Renvoie-la (menu) ; si ça recommence, préviens l'administrateur.",
      },
      passkeyOn: "Empreinte activée.", passkeyFail: "Empreinte non activée.",
      stats: "Statistiques", placedToday: "Aujourd'hui", fitsMisses: (a, b) => `${a} ✓ · ${b} ✗`, calibTitle: "Réussite par niveau",
      calib: (c) => `${c.successes}/${c.trials} · ≈ ${Math.round(100 * c.chance)} %`, version: (a, m) => `Version ${a} · modèle ${m}`,
      stages: { board: "Plateau…", catalog: "Catalogue des pièces…", reliability: "Fiabilité", journal: "Relecture du journal…", start: "Démarrage…" },
      error: "Le serveur a un problème. Réessaie dans un instant.", lang: "English", themeTo: { dark: "Thème sombre", light: "Thème clair" },
    },
    en: {
      appName: "Workshop · Moons", starting: "Starting…", loginPasskey: "Log in with fingerprint", password: "Password",
      putBackDone: "Done", login: "Log in", showPw: "Show", hidePw: "Hide", wrongPassword: "Wrong password.", tooMany: "Too many attempts: try again in 15 minutes.",
      take: "Take", turn: "Turn", place: "Place", sheet: "sheet", line: "row", column: "column",
      turns: ["Do not turn it", "¼ turn clockwise", "Half turn", "¼ turn anticlockwise"],
      otherWay: "Fits another way", skip: "Skip cell", undo: "Undo", noFit: "does not fit", fits: "fits",
      whichWay: "Which way did it fit?", cancel: "Close", undoTitle: "Undo the last answer?",
      undoConfirm: "Undo the answer", menu: "Menu", sendPhoto: "Send a photo of the puzzle",
      photoHint: "From the front, above the centre of the puzzle, soft light, no flash. Best: take it with the Camera app at full resolution, then pick it here from the gallery.", addPasskey: "Enable fingerprint",
      logout: "Log out", close: "Close", later: "Later", pkOfferTitle: "Log in with your fingerprint?",
      pkOfferText: "Next time, your fingerprint (or the phone code) will be enough.", noProposal: "No proposal left for now.",
      try_: (k) => (k > 1 ? `Try ${k} in this cell` : "1st try in this cell"), chance: "chance", buckets: { A: "reliable", B: "possible", C: "weak" },
      counts: (c) => `${c.placed} placed · ${c.left} left`,
      putBack: (p) => `Put <b>${pid(p.piece)}</b> back: sheet ${p.sheet} · row ${p.row} · col. ${p.col}, <b>this way up</b>.`,
      removeBack: (p) => `Take <b>${pid(p.piece)}</b> out of ${p.cell} and put it back: sheet ${p.sheet} · row ${p.row} · col. ${p.col}, <b>this way up</b>.`,
      weak: "No reliable proposal left: these are weak. A photo of the puzzle would help (menu).",
      photoDue: (n) => `${n} pieces placed since the last photo: please send a photo of the puzzle (menu).`,
      undoText: (u) => (u.type === "skip" ? `Cell ${u.cell} skipped.` : u.type === "does_not_fit" ? `${pid(u.piece)} did not fit in ${u.cell}.`
        : u.type === "remove" ? `${pid(u.piece)} taken out of ${u.cell} (it will be recorded as placed there again).` : u.type === "manual" ? `${pid(u.piece)} placed by you in ${u.cell}.` : `${pid(u.piece)} placed in ${u.cell}.`),
      fixTitle: "Correct", removeOpen: "Take out a placed piece", manualOpen: "I placed a piece myself",
      removeFilterPh: "Cell (7 8) or piece (D51)", removeNone: "No placed piece matches.", removeItem: (x) => `${pid(x.piece)} in ${x.cell}${x.manual ? " (placed by you)" : ""}`,
      removeAsk: (x) => `Take <b>${pid(x.piece)}</b> out of <b>${x.cell}</b>? Why?`,
      removeWrong: "It had play / did not belong here", removeWrongHint: "It will no longer be proposed in this cell.",
      removeOther: "Just taking it out", removeOtherHint: "Wrong entry or another reason: it may be proposed here again.",
      checkToo: (l) => `Check ${l.map((x) => `${pid(x.piece)} in ${x.cell}`).join(", ")} too: placed next to it afterwards, partly on its sides.`,
      needsPhoto: "This cell will be proposed again after the next photo of the puzzle.",
      pieceLabel: "Piece", next: "Next", manualWhichWay: "Which way did you place it? (seen as on the puzzle)", likely: "most likely",
      manualErr: { cell: "This cell does not exist.", piece: "Unknown piece: write the sheet and the number, e.g. D51.", cell_filled: "This cell is already filled.",
        piece_placed: (d) => `This piece is already recorded as placed in ${d.cell}.` },
      notSaved: "Answer not saved (network).", retry: "Retry", stale: "Already taken into account: here is the current proposal.",
      badFit: "Careful: according to the measurements, this piece fits badly here. Check that it lies flat and the right way.",
      photoSent: "Photo sent: being analysed, you can keep placing.", photoError: "Photo not sent.", photoBusy: "A photo is already being analysed.",
      photoSending: (mb) => `Sending the photo (${mb} MB)… keep the app open.`, photoNotJpeg: "Photo not sent: it is not a JPEG (set the camera to JPEG, not HEIF).",
      photoTooBig: (mb) => `Photo too heavy (${mb} MB, 80 max): take it again at normal resolution or 50 Mpx.`,
      recalRunning: (s) => `Photo received: being analysed (${({ start: "starting", photo: "reading", board: "board", sharpness: "sharpness", model: "model", sockets: "holes", scores: "computing", switch: "switching" })[s.stage] || "…"}, ${Math.round(s.seconds / 60)} min). You can keep placing.`,
      recalOk: "Photo taken into account: the proposals now start from the photographed puzzle.",
      recalNo: {
        frame: "Photo rejected: the frame of the puzzle is not entirely visible. Take it again from the front, with some margin all round (menu).",
        blur: "Photo rejected: it is blurred. Hold the phone still (resting on something, or with the timer) and take it again (menu).",
        cells: (d) => "Photo rejected: the puzzle does not match the recorded answers." + ((d && d.empty_on_photo && d.empty_on_photo.length) ? ` Empty on the photo but recorded as placed: ${d.empty_on_photo.join(", ")}.` : "") + ((d && d.filled_on_photo && d.filled_on_photo.length) ? ` Filled on the photo but not recorded: ${d.filled_on_photo.join(", ")}.` : "") + " Check these cells, then take the photo again (menu).",
        sockets: "Photo rejected: the holes are less sharp than on the previous one (light, glare?). Take it again under soft light (menu).",
        interrupted: "The photo could not be analysed (server restarted). Send it again (menu).",
        error: "The photo could not be analysed. Send it again (menu); if it happens again, tell the administrator.",
      },
      passkeyOn: "Fingerprint enabled.", passkeyFail: "Fingerprint not enabled.",
      stats: "Statistics", placedToday: "Today", fitsMisses: (a, b) => `${a} ✓ · ${b} ✗`, calibTitle: "Success by level",
      calib: (c) => `${c.successes}/${c.trials} · ≈ ${Math.round(100 * c.chance)} %`, version: (a, m) => `Version ${a} · model ${m}`,
      stages: { board: "Board…", catalog: "Piece catalog…", reliability: "Reliability", journal: "Replaying the journal…", start: "Starting…" },
      error: "The server has a problem. Try again in a moment.", lang: "Français", themeTo: { dark: "Dark theme", light: "Light theme" },
    },
  };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  let lang = store.get("atelier.lang") || "fr";
  const T = (k) => STR[lang][k];
  let theme = store.get("atelier.theme") === "light" ? "light" : "dark";      // dark by default, whatever the phone setting
  function applyTheme() {
    document.documentElement.dataset.theme = theme;
    document.querySelector("meta[name=theme-color]").content = theme === "dark" ? "#0b0d18" : "#f4f5fa";
  }
  applyTheme();
  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-t]").forEach((e) => { const v = T(e.dataset.t); if (typeof v === "string") e.textContent = v; });
    document.querySelectorAll("[data-title]").forEach((e) => { e.title = T(e.dataset.title); e.setAttribute("aria-label", T(e.dataset.title)); });
    document.querySelectorAll("[data-ph]").forEach((e) => { e.placeholder = T(e.dataset.ph); });
    $("#langBtn").textContent = T("lang");
    $("#pwShow").textContent = T($("#pw").type === "password" ? "showPw" : "hidePw");
    $("#themeBtn").textContent = T("themeTo")[theme === "dark" ? "light" : "dark"];
  }

  // ------------------------------------------------------------------ API
  class NetError extends Error {}
  async function api(method, path, body, raw) {
    let r;
    try {
      r = await fetch(path, {
        method, credentials: "same-origin", cache: "no-store",
        headers: raw ? { "Content-Type": "image/jpeg", "X-Device": device() } : body ? { "Content-Type": "application/json" } : {},
        body: raw || (body ? JSON.stringify(body) : undefined),
      });
    } catch (e) { throw new NetError(String(e)); }
    let data = {};
    try { data = await r.json(); } catch { /* empty body */ }
    if (r.status === 401 && path !== "/api/login" && !path.startsWith("/api/passkey/login")) { showLogin(); throw new Error("login"); }
    return { status: r.status, data };
  }
  function device() { return (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "?"; }
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join(""));
  addEventListener("error", (e) => report(e.message, `${e.filename}:${e.lineno}`));
  addEventListener("unhandledrejection", (e) => { if (!(e.reason instanceof NetError) && String(e.reason) !== "Error: login") report(String(e.reason), "promise"); });
  function report(message, where) {
    fetch("/api/client-log", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, where, page: PAGE_VERSION }) }).catch(() => {});
  }

  // ------------------------------------------------------------------ screens
  function show(id) { for (const s of ["#loading", "#login", "#main"]) $(s).classList.toggle("hidden", s !== id); }
  async function showLogin() {
    show("#login");
    $("#loginErr").textContent = "";
    try {
      const { data } = await api("GET", "/api/me");
      $("#pkLogin").classList.toggle("hidden", !(data.passkeys > 0 && window.PublicKeyCredential));
    } catch { /* offline: password form only */ }
  }
  let toastTimer;
  function toast(html, opts = {}) {
    const t = $("#toast");
    t.innerHTML = html;
    t.classList.toggle("err", !!opts.err);
    if (opts.action) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = opts.action[0]; b.onclick = () => { t.classList.remove("on"); opts.action[1](); };
      t.appendChild(b);
    }
    t.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("on"), opts.ms || (opts.action ? 12000 : 4000));
  }

  // ------------------------------------------------------------------ state
  let S = null, inFlight = false, pending = null;
  async function refresh() {
    if (inFlight) return;
    let r;
    try { r = await api("GET", "/api/state"); } catch (e) { if (e instanceof NetError) setTimeout(refresh, 3000); return; }
    const s = r.data;
    if (s.app_version && s.app_version !== PAGE_VERSION && !inFlight) { location.reload(); return; }
    if (s.status !== "ready") { loading(s); setTimeout(refresh, s.status === "error" ? 5000 : 800); return; }
    if (s.busy) { setTimeout(refresh, 700); return; }
    render(s);
  }
  function loading(s) {
    show("#loading");
    if (s.status === "error") { $("#loadStage").textContent = T("error"); return; }
    const p = s.progress || {};
    const name = T("stages")[p.stage] || T("starting");
    $("#loadStage").textContent = p.stage === "reliability" ? `${name} ${p.done + 1} / ${p.total}` : name;
    const order = ["start", "board", "catalog", "journal", "reliability"];
    const base = Math.max(0, order.indexOf(p.stage)) / order.length;
    $("#loadBar").style.width = `${Math.round(100 * Math.min(1, base + (p.total ? p.done / p.total : 0) / order.length))}%`;
  }

  // ------------------------------------------------------------------ drawing helpers
  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  const pathD = (f) => { let s = `M${f[0]} ${f[1]}`; for (let i = 2; i < f.length; i += 2) s += `L${f[i]} ${f[i + 1]}`; return s + "Z"; };
  function pieceSvg(svg, shape, angle) {
    svg.replaceChildren();
    const g = el("g", { transform: `rotate(${angle})` }, svg);
    el("path", { d: pathD(shape), class: "piece-shape" }, g);
    return g;
  }
  const signedTurns = (t) => (t === 3 ? -1 : t);

  // ------------------------------------------------------------------ render
  function render(s) {
    S = s;
    show("#main");
    $("#counts").textContent = T("counts")(s.counts);
    const banners = $("#banners");
    banners.replaceChildren();
    if (s.put_back && String(s.put_back.seq) !== store.get("atelier.putBackDone")) {
      const b = document.createElement("div"); b.className = "banner putback";
      const sv = document.createElementNS(NS, "svg"); sv.setAttribute("viewBox", "-80 -80 160 160");
      pieceSvg(sv, s.put_back.shape, 90 * s.put_back.display_turns);
      const p = document.createElement("p"); p.innerHTML = "↩ " + T(s.put_back.remove ? "removeBack" : "putBack")(s.put_back);
      if (s.put_back.check && s.put_back.check.length) { const c = document.createElement("small"); c.className = "check"; c.textContent = T("checkToo")(s.put_back.check); p.appendChild(c); }
      const ok = document.createElement("button"); ok.type = "button"; ok.textContent = T("putBackDone");
      ok.onclick = () => { store.set("atelier.putBackDone", String(s.put_back.seq)); b.remove(); };
      b.append(sv, p, ok); banners.appendChild(b);
    }
    const prop = s.proposal;
    if (prop && prop.weak) addBanner(T("weak"));
    const rc = s.recalibration;
    if (rc && rc.state === "running") addBanner(T("recalRunning")(rc));
    else if (rc && rc.state === "ok") addBanner(T("recalOk"));
    else if (rc) { const m = T("recalNo")[rc.reason] || T("recalNo").error; addBanner(typeof m === "function" ? m(rc.detail) : m); }
    if (s.counts.since_photo >= 30 && !(rc && rc.state === "running")) addBanner(T("photoDue")(s.counts.since_photo));
    $("#card").classList.toggle("hidden", !prop);
    $("#empty").classList.toggle("hidden", !!prop);
    for (const id of ["#yesBtn", "#noBtn", "#otherBtn", "#skipBtn"]) $(id).disabled = !prop;
    $("#undoBtn").disabled = !s.undo;
    if (!prop) return;

    // 1 take
    $("#pieceText").textContent = pid(prop.piece);
    $("#sheetLetter").textContent = prop.sheet;
    $("#slotText").textContent = `${T("line")} ${prop.row} · ${T("column")} ${prop.col}`;
    miniSheet(prop, s.placed_pieces);
    pieceSvg($("#thumb"), prop.shape, 90 * prop.display_turns);
    // 2 turn
    const a0 = 90 * prop.display_turns, a1 = a0 + 90 * signedTurns(prop.plate_turns);
    $("#turnText").textContent = T("turns")[prop.plate_turns];
    turnView(prop.shape, a0, a1, prop.plate_turns);
    // 3 place
    $("#cellText").textContent = prop.cell.replace("-", " · ");
    zoom(prop, s.board);
    locator(prop, s.board);
    const pct = Math.round(100 * prop.chance);
    $("#meta").innerHTML = `${T("try_")(prop.rank)} · ${T("chance")} ≈ <b>${pct} %</b> <span class="chip ${prop.bucket}">${T("buckets")[prop.bucket]}</span>`;
  }
  function addBanner(text) {
    const b = document.createElement("div"); b.className = "banner warn";
    const p = document.createElement("p"); p.textContent = text; b.appendChild(p); $("#banners").appendChild(b);
  }
  function miniSheet(prop, placed) {
    const svg = $("#miniSheet"); svg.replaceChildren();
    const rows = prop.sheet === "E" ? 2 : 10;
    svg.setAttribute("viewBox", `0 0 10 ${rows}`);
    const gone = new Set(placed.filter((p) => p[0] === prop.sheet).map((p) => +p.slice(1)));
    for (let r = 0; r < rows; r++) for (let c = 0; c < 10; c++) {
      const n = r * 10 + c + 1;
      if (prop.sheet === "E" && n > 12) continue;
      const cls = n === (prop.row - 1) * 10 + prop.col ? "slot target" : gone.has(n) ? "slot gone" : "slot";
      el("rect", { x: c + 0.08, y: r + 0.08, width: 0.84, height: 0.84, rx: 0.15, class: cls }, svg);
    }
  }
  let turnAnim;
  function turnView(shape, a0, a1, turns) {
    const svg = $("#turnView");
    const g = pieceSvg(svg, shape, 0);
    g.removeAttribute("transform");
    if (turns) {                                    // curved arrow showing the direction of the turn
      const cw = turns !== 3, r = 86, sweep = turns === 2 ? 170 : 80;
      const s0 = -90 - sweep / 2, s1 = -90 + sweep / 2;
      const p = (deg) => [r * Math.cos((deg * Math.PI) / 180), r * Math.sin((deg * Math.PI) / 180)];
      const [x0, y0] = p(cw ? s0 : s1), [x1, y1] = p(cw ? s1 : s0);
      el("path", { d: `M${x0} ${y0} A${r} ${r} 0 0 ${cw ? 1 : 0} ${x1} ${y1}`, class: "arrow" }, svg);
      const ang = (cw ? s1 + 90 : s0 - 90) * Math.PI / 180, hx = Math.cos(ang), hy = Math.sin(ang);
      el("path", { d: `M${x1 + hx * 14} ${y1 + hy * 14} L${x1 - hy * 9} ${y1 + hx * 9} L${x1 + hy * 9} ${y1 - hx * 9} Z`, class: "arrow-head" }, svg);
    }
    if (turnAnim) turnAnim.cancel();
    g.style.transform = `rotate(${a1}deg)`;
    if (a0 !== a1 && !matchMedia("(prefers-reduced-motion: reduce)").matches && g.animate) {
      turnAnim = g.animate([{ transform: `rotate(${a0}deg)` }, { transform: `rotate(${a0}deg)`, offset: 0.25 },
        { transform: `rotate(${a1}deg)`, offset: 0.75 }, { transform: `rotate(${a1}deg)` }], { duration: 2400, iterations: Infinity, easing: "ease-in-out" });
    }
  }
  function zoom(prop, board) {
    const svg = $("#zoom"); svg.replaceChildren();
    // 4:3 window centred on the cell: about 3.4 cells wide, 2.6 cells high (the cell and its neighbours)
    const [cx, cy] = prop.center, hh = 1.3 * board.ch, hw = (hh * 4) / 3;
    svg.setAttribute("viewBox", `${cx - hw} ${cy - hh} ${2 * hw} ${2 * hh}`);
    el("image", { href: BOARD_IMG, x: 0, y: 0, width: board.w, height: board.h, preserveAspectRatio: "none" }, svg);
    for (const n of prop.near) el("path", { d: pathD(n.contour), class: "near" }, svg);
    el("path", { d: pathD(prop.fit.flat()), class: "target-fit" }, svg);
  }
  function locator(prop, board) {
    const svg = $("#locator"); svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${board.w} ${board.h}`);
    el("image", { href: BOARD_IMG, x: 0, y: 0, width: board.w, height: board.h, preserveAspectRatio: "none" }, svg);
    el("circle", { cx: prop.center[0], cy: prop.center[1], r: 110, class: "cell-dot" }, svg);
  }

  // ------------------------------------------------------------------ answers
  async function send(path, body) {
    if (inFlight) return;
    inFlight = true; document.body.classList.add("busy");
    pending = { path, body };
    try {
      const { status, data } = await api("POST", path, body);
      pending = null;
      if (status === 409) toast(T("stale"));
      else if (status >= 400 && status !== 503) toast(T("error"), { err: true });
      if (data.warning && data.warning.code === "bad_fit") toast(T("badFit"), { err: true, ms: 9000 });
      if (data.needs_photo) toast(T("needsPhoto"), { ms: 9000 });
      if (navigator.vibrate) navigator.vibrate(status === 200 ? 25 : [60, 40, 60]);
      inFlight = false; document.body.classList.remove("busy");
      if (data.status === "ready" && !data.busy && data.counts) render(data); else refresh();
    } catch (e) {
      inFlight = false; document.body.classList.remove("busy");
      if (e instanceof NetError) toast(T("notSaved"), { err: true, action: [T("retry"), () => { const p = pending; pending = null; if (p) send(p.path, p.body); }] });
    }
  }
  function answer(verdict, rotation) {
    if (!S || !S.proposal) return;
    send("/api/answer", { event_id: uuid(), proposal_id: S.proposal.id, verdict, rotation, device: device() });
  }
  $("#yesBtn").onclick = () => answer("fits");
  $("#noBtn").onclick = () => answer("does_not_fit");
  $("#skipBtn").onclick = () => answer("skip");
  $("#otherBtn").onclick = () => {
    const prop = S.proposal, box = $("#rotChoices"); box.replaceChildren();
    for (const o of prop.other_rotations) {
      const b = document.createElement("button"); b.type = "button";
      const sv = document.createElementNS(NS, "svg"); sv.setAttribute("viewBox", "-80 -80 160 160");
      // drawn as it would sit on the board (rotation from the canonical frame), like the board zoom
      pieceSvg(sv, prop.shape, o.rotation);
      const t = document.createElement("small"); t.textContent = T("turns")[o.plate_turns];
      b.append(sv, t);
      b.onclick = () => { $("#rotDlg").close(); answer("fits_other_rotation", o.rotation); };
      box.appendChild(b);
    }
    $("#rotDlg").showModal();
  };
  $("#undoBtn").onclick = () => { if (!S || !S.undo) return; $("#undoText").textContent = T("undoText")(S.undo); $("#undoDlg").showModal(); };
  $("#undoGo").onclick = () => { $("#undoDlg").close(); send("/api/undo", { event_id: uuid(), target: S.undo.seq, device: device() }); };
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));

  // ------------------------------------------------------------------ menu
  $("#menuBtn").onclick = () => {
    const s = S, c = s.counts, cal = s.calibration || {};
    $("#stats").innerHTML = `<h4>${T("stats")}</h4><table>
      <tr><td>${T("counts")(c)}</td><td></td></tr>
      <tr><td>${T("placedToday")}</td><td>${T("fitsMisses")(c.today_fits, c.today_misses)}</td></tr>
      ${["A", "B", "C"].map((b) => (cal[b] ? `<tr><td>${T("calibTitle")} · ${T("buckets")[b]}</td><td>${T("calib")(cal[b])}</td></tr>` : "")).join("")}
    </table>`;
    $("#versionText").textContent = T("version")(s.app_version, s.model_version);
    $("#pkRegister").classList.toggle("hidden", !window.PublicKeyCredential);
    $("#menuDlg").showModal();
  };
  $("#langBtn").onclick = () => { lang = lang === "fr" ? "en" : "fr"; store.set("atelier.lang", lang); applyLang(); if (S) render(S); };
  $("#themeBtn").onclick = () => { theme = theme === "dark" ? "light" : "dark"; store.set("atelier.theme", theme); applyTheme(); applyLang(); };
  // ------------------------------------------------------------------ corrections: take out a placed piece, a piece placed by hand
  let placedList = [];
  function matchPlaced(x, query) {
    const q = query.trim().toUpperCase();
    if (!q) return true;
    const [l, c] = x.cell.match(/\d+/g).map(Number);
    const nums = (q.match(/\d+/g) || []).map(Number);
    const piece = q.replace(/\s+/g, "").match(/^([A-E])0*(\d+)$/);
    if (piece) return pid(x.piece).startsWith(piece[1] + Number(piece[2])) || (piece[1] === "C" && c === Number(piece[2]));
    if (nums.length >= 2) return l === nums[0] && c === nums[1];
    if (nums.length === 1) return l === nums[0];
    return `${pid(x.piece)} ${x.cell}`.toUpperCase().includes(q);
  }
  function drawRemoveList() {
    const ul = $("#removeList"); ul.replaceChildren();
    const shown = placedList.filter((x) => matchPlaced(x, $("#removeFilter").value)).slice(0, 80);
    if (!shown.length) { const li = document.createElement("li"); li.className = "hint"; li.textContent = T("removeNone"); ul.appendChild(li); return; }
    for (const x of shown) {
      const li = document.createElement("li"), b = document.createElement("button"); b.type = "button";
      const when = (x.ts || "").slice(5, 16).replace("T", " ").replace(/^(\d\d)-(\d\d)/, "$2/$1");
      b.innerHTML = `<span>${T("removeItem")(x)}</span><small>${when}</small>`;
      b.onclick = () => {
        $("#removePick").classList.add("hidden"); $("#removeConfirm").classList.remove("hidden");
        $("#removeText").innerHTML = T("removeAsk")(x);
        const go = (reason) => { $("#removeDlg").close(); send("/api/remove", { event_id: uuid(), target: x.seq, reason, device: device() }); };
        $("#removeWrong").onclick = () => go("wrong");
        $("#removeOther").onclick = () => go("other");
      };
      li.appendChild(b); ul.appendChild(li);
    }
  }
  $("#removeBtn").onclick = async () => {
    $("#menuDlg").close();
    placedList = []; $("#removeFilter").value = "";
    $("#removePick").classList.remove("hidden"); $("#removeConfirm").classList.add("hidden");
    $("#removeList").replaceChildren();
    $("#removeDlg").showModal();
    try {
      const { status, data } = await api("GET", "/api/placed");
      if (status === 200) { placedList = data.placed; drawRemoveList(); } else toast(T("error"), { err: true });
    } catch (e) { if (e instanceof NetError) toast(T("error"), { err: true }); }
  };
  $("#removeFilter").oninput = drawRemoveList;

  $("#manualBtn").onclick = () => {
    $("#menuDlg").close();
    $("#manualForm").reset(); $("#manualForm").classList.remove("hidden"); $("#manualRot").classList.add("hidden");
    $("#manualErr").textContent = "";
    $("#manualDlg").showModal();
  };
  $("#manualForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const err = (code, d) => { const m = T("manualErr")[code]; $("#manualErr").textContent = m ? (typeof m === "function" ? m(d) : m) : T("error"); };
    const m = $("#manualPiece").value.trim().toUpperCase().match(/^([A-E])\s*0*(\d{1,3})$/);
    if (!m) { err("piece"); return; }
    const cell = `L${Number($("#manualRow").value)}-C${Number($("#manualCol").value)}`, piece = m[1] + m[2].padStart(3, "0");
    $("#manualErr").textContent = "";
    let r;
    try { r = await api("POST", "/api/manual", { event_id: uuid(), cell, piece, device: device() }); } catch (e) { if (e instanceof NetError) err("x"); return; }
    if (r.status !== 200 || !r.data.preview) { err(r.data.error, r.data); return; }
    const pv = r.data.preview, box = $("#manualChoices"); box.replaceChildren();
    pv.choices.forEach((ch, i) => {
      const b = document.createElement("button"); b.type = "button";
      const sv = document.createElementNS(NS, "svg"); sv.setAttribute("viewBox", "-80 -80 160 160");
      pieceSvg(sv, pv.shape, ch.rotation);                      // as it sits on the board, like the board zoom
      const t = document.createElement("small"); t.textContent = i === 0 && ch.rms !== null ? T("likely") : " ";
      b.append(sv, t);
      b.onclick = () => { $("#manualDlg").close(); send("/api/manual", { event_id: uuid(), cell, piece, rotation: ch.rotation, device: device() }); };
      box.appendChild(b);
    });
    $("#manualForm").classList.add("hidden"); $("#manualRot").classList.remove("hidden");
  };

  $("#logoutBtn").onclick = async () => { await api("POST", "/api/logout", {}).catch(() => {}); $("#menuDlg").close(); showLogin(); };
  $("#photoInput").onchange = async (ev) => {
    const f = ev.target.files[0]; ev.target.value = "";
    if (!f) return;
    $("#menuDlg").close();
    const mb = Math.round(f.size / 1048576);
    const long = { err: true, ms: 15000 };
    if (f.size > MAX_PHOTO_MB * 1048576) { toast(T("photoTooBig")(mb), long); return; }
    toast(T("photoSending")(mb), { ms: 600000 });                   // a big photo takes a while over a phone network
    try {
      const { status, data } = await api("POST", "/api/photo", null, f);
      if (status === 200) { toast(T("photoSent"), { ms: 8000 }); render(data); }
      else if (status === 409) { toast(T("photoBusy"), long); render(data); }
      else if (status === 413) toast(T("photoTooBig")(mb), long);
      else if (status === 415) toast(T("photoNotJpeg"), long);
      else toast(T("photoError"), long);
    } catch { toast(T("photoError"), long); }
  };

  // ------------------------------------------------------------------ login and passkeys (WebAuthn)
  const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const unb64u = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
  function showPassword(on) {
    $("#pw").type = on ? "text" : "password";
    $("#pwShow").setAttribute("aria-pressed", String(on));
    $("#pwShow").textContent = T(on ? "hidePw" : "showPw");
  }
  $("#pwShow").onclick = () => { showPassword($("#pw").type === "password"); $("#pw").focus(); };
  $("#pwForm").onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      const { status } = await api("POST", "/api/login", { password: $("#pw").value });
      if (status === 200) { $("#pw").value = ""; showPassword(false); afterLogin(); }
      else $("#loginErr").textContent = T(status === 429 ? "tooMany" : "wrongPassword");
    } catch { $("#loginErr").textContent = T("notSaved"); }
  };
  $("#pkLogin").onclick = async () => {
    try {
      const { data } = await api("POST", "/api/passkey/login/options", {});
      const pk = data.publicKey;
      pk.challenge = unb64u(pk.challenge);
      pk.allowCredentials = pk.allowCredentials.map((c) => ({ ...c, id: unb64u(c.id) }));
      const cred = await navigator.credentials.get({ publicKey: pk });
      const r = cred.response;
      const { status } = await api("POST", "/api/passkey/login/verify", { id: cred.id, rawId: b64u(cred.rawId), type: cred.type,
        response: { clientDataJSON: b64u(r.clientDataJSON), authenticatorData: b64u(r.authenticatorData), signature: b64u(r.signature),
                    userHandle: r.userHandle ? b64u(r.userHandle) : null } });
      if (status === 200) afterLogin(); else $("#loginErr").textContent = T("passkeyFail");
    } catch { $("#loginErr").textContent = T("passkeyFail"); }
  };
  async function registerPasskey() {
    try {
      const { data } = await api("POST", "/api/passkey/register/options", {});
      const pk = data.publicKey;
      pk.challenge = unb64u(pk.challenge);
      pk.user.id = unb64u(pk.user.id);
      pk.excludeCredentials = pk.excludeCredentials.map((c) => ({ ...c, id: unb64u(c.id) }));
      const cred = await navigator.credentials.create({ publicKey: pk });
      const { status } = await api("POST", "/api/passkey/register/verify", { id: cred.id, rawId: b64u(cred.rawId), type: cred.type,
        response: { clientDataJSON: b64u(cred.response.clientDataJSON), attestationObject: b64u(cred.response.attestationObject) } });
      toast(T(status === 200 ? "passkeyOn" : "passkeyFail"), { err: status !== 200 });
    } catch { toast(T("passkeyFail"), { err: true }); }
  }
  $("#pkRegister").onclick = () => { $("#menuDlg").close(); registerPasskey(); };
  $("#pkOfferGo").onclick = () => { $("#pkOffer").close(); registerPasskey(); };
  async function afterLogin() {
    refresh();
    try {
      const { data } = await api("GET", "/api/me");
      if (!data.passkeys && window.PublicKeyCredential && store.get("atelier.pkOffer") !== "no") {
        store.set("atelier.pkOffer", "no");
        $("#pkOffer").showModal();
      }
    } catch { /* not important */ }
  }

  // ------------------------------------------------------------------ keep in sync, keep the screen on
  let wake = null;
  async function keepAwake() {
    try { if ("wakeLock" in navigator && document.visibilityState === "visible" && !wake) { wake = await navigator.wakeLock.request("screen"); wake.addEventListener("release", () => (wake = null)); } } catch { /* refused */ }
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { refresh(); keepAwake(); } });
  setInterval(() => { if (document.visibilityState === "visible" && !inFlight && !$("#main").classList.contains("hidden")) refresh(); }, 15000);

  // ------------------------------------------------------------------ start
  applyLang();
  (async () => {
    try {
      const { data } = await api("GET", "/api/me");
      if (data.logged) { refresh(); keepAwake(); } else showLogin();
    } catch { setTimeout(() => location.reload(), 4000); }
  })();
})();
