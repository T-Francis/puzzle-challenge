"""Builds catalog/catalog.html (self-contained: data and shapes embedded)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, CATALOG, load_json

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Piece catalog</title>
<style>
:root{--bg:#f6f5f3;--panel:#fff;--ink:#1d1b1a;--muted:#6b6663;--line:#e2dedb;--piece:#2b2626;--accent:#2a6fdb;--tab:#1f9d55;--blank:#d64545;--warn:#c47f00}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151413;--panel:#1f1d1c;--ink:#ece9e6;--muted:#a09a96;--line:#34302e;--piece:#b9b1ab;--accent:#6ea2ff;--tab:#4cc27f;--blank:#ff7a7a;--warn:#f0b040}}
:root[data-theme="dark"]{--bg:#151413;--panel:#1f1d1c;--ink:#ece9e6;--muted:#a09a96;--line:#34302e;--piece:#b9b1ab;--accent:#6ea2ff;--tab:#4cc27f;--blank:#ff7a7a;--warn:#f0b040}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px}
h1{font-size:18px;margin:0 0 8px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.bar select,.bar input{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit}
.bar label{display:flex;gap:6px;align-items:center;color:var(--muted)}
.count{color:var(--muted);margin-left:auto}
main{padding:16px}
.sheet{margin:0 0 24px}
.sheet h2{font-size:15px;margin:0 0 8px;color:var(--muted);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(10,minmax(0,1fr));gap:6px}
@media (max-width:900px){.grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px;cursor:pointer;position:relative}
.card:hover{border-color:var(--accent)}
.card svg{display:block;width:100%;height:auto}
.card .id{font-size:11px;color:var(--muted);text-align:center;font-variant-numeric:tabular-nums}
.card.warn::after{content:"!";position:absolute;top:3px;right:6px;color:var(--warn);font-weight:700}
.piece{fill:var(--piece)}
.num{fill:var(--bg);font-weight:700;font-family:system-ui,sans-serif}
.s-T{stroke:var(--tab)}.s-B{stroke:var(--blank)}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--ink);max-width:min(720px,94vw);width:100%;padding:16px}
dialog::backdrop{background:rgba(0,0,0,.45)}
.det{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
@media (max-width:600px){.det{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:right}
th:first-child,td:first-child{text-align:left}
.tag{display:inline-block;border-radius:4px;padding:0 6px;font-size:12px;border:1px solid var(--line);margin:2px 4px 2px 0}
.T{color:var(--tab)}.B{color:var(--blank)}
button{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font:inherit;cursor:pointer}
.legend{color:var(--muted);font-size:12px}
</style>
</head>
<body>
<header>
  <h1>Piece catalog — __N__ pieces (fused reference)</h1>
  <div class="bar">
    <select id="sheet"><option value="">All sheets</option></select>
    <select id="type"><option value="">All types</option></select>
    <input id="q" placeholder="Search (e.g. B58, 58)" size="18">
    <label><input type="checkbox" id="warn"> With alert</label>
    <label><input type="checkbox" id="sides"> Colour tabs / blanks</label>
    <span class="count" id="count"></span>
  </div>
  <div class="legend">Tab = <span class="T">green</span>, blank = <span class="B">red</span>. Each piece is drawn as it lies on its sheet (sheet the right way up): this is the starting point of the rotations in the waves. Click a piece for its details.</div>
</header>
<main id="main"></main>
<dialog id="dlg"><div id="dlgBody"></div><p style="text-align:right;margin:12px 0 0"><button onclick="dlg.close()">Close</button></p></dialog>
<script>
const DATA = __DATA__;
const TYPE_NAMES = {T:"tab", B:"blank", F:"flat"};
const FLAG_TEXT = {single_view:"only one usable view", side_unconfirmed:"a side is confirmed by one view only (the others deviate)",
  types_differ_between_views:"the views disagree on tab / blank for a side", core_angle_out_of_tolerance:"very trapezoidal core (checked)", flat_side_detected:"flat side detected"};
const canon = t => [0,1,2,3].map(i => t.slice(i)+t.slice(0,i)).sort()[0];
const sheets = [...new Set(DATA.map(d => d.sheet))];
const types = [...new Set(DATA.map(d => canon(d.types)))].sort();
const $ = id => document.getElementById(id);
sheets.forEach(p => $("sheet").add(new Option("Sheet " + p, p)));
types.forEach(t => $("type").add(new Option(t + " (" + DATA.filter(d => canon(d.types) === t).length + ")", t)));

function sideSegments(d){
  // contour segments between corners, to colour tabs / blanks
  const pts = d.path.slice(1, -2).split(" L").map(s => s.split(",").map(Number));
  const idx = d.core.map(c => { let b = 0, bd = 1e9; pts.forEach((p, i) => { const q = (p[0]-c[0])**2 + (p[1]-c[1])**2; if (q < bd) { bd = q; b = i; } }); return b; });
  let out = "";
  for (let k = 0; k < 4; k++) {
    const a = idx[k], b = idx[(k + 1) % 4]; const seg = [];
    for (let i = a; ; i = (i + 1) % pts.length) { seg.push(pts[i]); if (i === b) break; }
    out += `<polyline points="${seg.map(p => p.join(",")).join(" ")}" fill="none" stroke-width="2.2" class="s-${d.sides[k].type}"/>`;
  }
  return out;
}
function svgFor(d, big){
  const pts = d.path.slice(1, -2).split(" L").map(s => s.split(",").map(Number));
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(-85, ...xs) - 4, y0 = Math.min(-85, ...ys) - 4, w = Math.max(85, ...xs) + 4 - x0, h = Math.max(85, ...ys) + 4 - y0;
  const col = $("sides").checked || big;
  return `<svg viewBox="${x0} ${y0} ${w} ${h}" role="img" aria-label="Piece ${d.id}"><path class="piece" d="${d.path}"/>${col ? sideSegments(d) : ""}<text class="num" x="0" y="${big ? 7 : 9}" text-anchor="middle" font-size="${big ? 20 : 26}">${d.number}</text></svg>`;
}
function render(){
  const pl = $("sheet").value, ty = $("type").value, q = $("q").value.trim().toUpperCase(), w = $("warn").checked;
  const keep = DATA.filter(d => (!pl || d.sheet === pl) && (!ty || canon(d.types) === ty) && (!w || d.flags.length) &&
    (!q || d.id === q.replace(/^([A-E])(\d+)$/, (m, a, b) => a + b.padStart(3, "0")) || String(d.number) === q || d.id.includes(q)));
  $("count").textContent = keep.length + " piece(s)";
  $("main").innerHTML = sheets.filter(p => keep.some(d => d.sheet === p)).map(p => `<section class="sheet"><h2>Sheet ${p}</h2><div class="grid">` +
    keep.filter(d => d.sheet === p).map(d => `<div class="card${d.flags.length ? " warn" : ""}" data-id="${d.id}">${svgFor(d)}<div class="id">${d.id}</div></div>`).join("") +
    `</div></section>`).join("");
}
$("main").addEventListener("click", e => {
  const c = e.target.closest(".card"); if (!c) return;
  const d = DATA.find(x => x.id === c.dataset.id);
  $("dlgBody").innerHTML = `<h2 style="margin:0 0 8px">${d.id} — sheet ${d.sheet}, no. ${d.number}</h2>
  <div class="det"><div>${svgFor(d, true)}</div><div>
  <p>${d.sides.map(s => `<span class="tag ${s.type}">${s.name}: ${TYPE_NAMES[s.type]}</span>`).join("")}</p>
  <p>Sheet ${d.sheet}, row ${d.row}, column ${d.col} — core: ${d.core_w} × ${d.core_h} u (standing piece)</p>
  <table><tr><th>Side</th><th>L</th><th>Height</th><th>Position</th><th>Neck</th><th>Head</th></tr>
  ${d.sides.map(s => `<tr><td class="${s.type}">${s.name}</td><td>${s.L}</td><td>${s.height}</td><td>${s.position}</td><td>${s.neck}</td><td>${s.head}</td></tr>`).join("")}</table>
  ${d.flags.length ? `<p style="color:var(--warn)">${d.flags.map(f => FLAG_TEXT[f] || f).join("<br>")}</p>` : ""}
  <h3 style="font-size:14px;margin:12px 0 4px">Fused photo views</h3>
  <table><tr><th>Photo</th><th>Weight</th><th>Deviation (u)</th><th>Kept</th></tr>
  ${d.views.map(v => `<tr><td>${v.photo.replace("sheets/", "")}</td><td>${v.weight}</td><td>${v.deviation}</td><td>${v.kept ? "yes" : "no"}</td></tr>`).join("")}</table>
  <p class="legend">Unit u: 100 u = median long side of the core. File: ${d.svg}</p></div></div>`;
  $("dlg").showModal();
});
["sheet", "type", "q", "warn", "sides"].forEach(id => $(id).addEventListener("input", render));
render();
</script>
</body>
</html>
"""


def main():
    idx = load_json(os.path.join(DATA, "catalog_index.json"))
    html = TEMPLATE.replace("__DATA__", json.dumps(idx, ensure_ascii=False, separators=(",", ":"))).replace("__N__", str(len(idx)))
    out = os.path.join(CATALOG, "catalog.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(out, len(html) // 1024, "KB")


if __name__ == "__main__":
    main()
