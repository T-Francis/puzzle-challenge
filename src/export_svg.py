"""SVG export of the reference catalog: one file per piece + one sheet overview per sheet.

Each piece is drawn in the READING DIRECTION OF ITS SHEET: as it lies on the sheet the right way up
(rounded to the quarter turn). This is the starting position of the rotations given in the waves.
Internally (engine, data), the piece stays in its canonical "standing" frame (side N on top):
display = canonical rotated by k quarter turns, k = display_turns(p).
"""
import os, sys
import cv2
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, CATALOG, SHEETS, load_json, save_json, load_raw

EPS_U = 0.15            # simplification tolerance (u) ≈ 0.33 px
TYPE_NAMES = {"T": "tab", "B": "blank", "F": "flat"}
DISP_NAMES = ["top", "right", "bottom", "left"]
CELL = 200              # grid step of the sheet overviews (u)


def simplify(P, eps):
    return cv2.approxPolyDP(P.reshape(-1, 1, 2).astype(np.float32), eps, True).reshape(-1, 2).astype(np.float64)


def path_d(P):
    return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in P) + " Z"


def rot90(P, r):
    """r clockwise quarter turns on screen (y down)."""
    for _ in range(r % 4):
        P = np.stack([-P[..., 1], P[..., 0]], -1)
    return P


def display_turns(p):
    """Clockwise quarter turns that bring the canonical frame to the orientation on the sheet."""
    return (-int(round(p["rotation_deg"] / 90))) % 4


def display_geometry(p, r):
    """Contour and corners in the sheet orientation; displayed side d (top, right, bottom, left) = canonical side
    (d - k) % 4 with k = display_turns."""
    k = display_turns(p)
    P, C = rot90(r["contour_u"], k), rot90(r["corners_u"], k)
    order = [(d - k) % 4 for d in range(4)]
    return P, C[order], order


def piece_svg(p, P, C, sides):
    x0, y0 = P.min(0) - 6
    x1, y1 = P.max(0) + 6
    types = " ".join(f"{n}:{TYPE_NAMES[s['type']]}" for n, s in zip(DISP_NAMES, sides))
    core = " ".join(f"{x:.2f},{y:.2f}" for x, y in C)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f}" width="{(x1 - x0) * 2:.0f}" height="{(y1 - y0) * 2:.0f}"
     data-id="{p['id']}" data-sheet="{p['sheet']}" data-number="{p['number']}" data-sides="{''.join(s['type'] for s in sides)}" data-unit="u (100 u = long side of the core)">
  <title>{p['id']} — sheet {p['sheet']}, no. {p['number']}, drawn as on its sheet — {types}</title>
  <path class="piece" d="{path_d(P)}" fill="#2b2626" stroke="#000" stroke-width="0.6" stroke-linejoin="round"/>
  <polygon class="core" points="{core}" fill="none" stroke="#8a8a8a" stroke-width="0.4" stroke-dasharray="2 2"/>
  <text x="0" y="5" text-anchor="middle" font-family="sans-serif" font-size="16" font-weight="bold" fill="#fff">{p['number']}</text>
</svg>
'''


def main():
    data = load_json(os.path.join(DATA, "pieces.json"))
    raw = load_raw()
    index, fidelity = [], []
    for sheet in SHEETS:
        os.makedirs(os.path.join(CATALOG, sheet), exist_ok=True)
        overview = []
        for p in sorted([q for q in data["pieces"] if q["sheet"] == sheet], key=lambda q: q["number"]):
            r = raw[p["id"]]
            Pd, Cd, order = display_geometry(p, r)
            sides = [p["sides"][j] for j in order]
            P = simplify(Pd, EPS_U)
            with open(os.path.join(CATALOG, sheet, f"{p['id']}.svg"), "w", encoding="utf-8") as f:
                f.write(piece_svg(p, P, Cd, sides))
            dev = max(abs(cv2.pointPolygonTest(P.reshape(-1, 1, 2).astype(np.float32), (float(x), float(y)), True))
                      for x, y in Pd[::2])
            fidelity.append((p["id"], len(P), float(dev)))
            overview.append((p, P))
            index.append(dict(id=p["id"], sheet=sheet, number=p["number"], row=p["row"], col=p["col"],
                              svg=f"{sheet}/{p['id']}.svg", path=path_d(P), core=Cd.round(2).tolist(),
                              types="".join(s["type"] for s in sides), flags=p["flags"],
                              display_turns=display_turns(p), core_w=round(p["core_w"], 1), core_h=round(p["core_h"], 1),
                              sides=[dict(name=n, type=s["type"], L=round(s["L"], 1), height=round(s.get("height", 0), 1),
                                          position=round(s.get("pos", 0), 3), neck=round(s.get("neck", 0), 1),
                                          head=round(s.get("head", 0), 1)) for n, s in zip(DISP_NAMES, sides)],
                              views=[dict(photo=v["file"], method=v["method"], weight=v["weight"],
                                          deviation=v["fusion_deviation_u"], kept=v["kept"]) for v in p["views"]]))
        # overview: row / column grid of the sheet
        nr = max(p["row"] for p, _ in overview); nc = max(p["col"] for p, _ in overview)
        W, H = nc * CELL, nr * CELL + 50
        body = "\n".join(
            f'  <g id="{p["id"]}" transform="translate({(p["col"] - 0.5) * CELL:.0f},{50 + (p["row"] - 0.5) * CELL:.0f})" '
            f'data-number="{p["number"]}"><title>{p["id"]}</title><path d="{path_d(P)}" fill="#2b2626" stroke="#000" '
            f'stroke-width="0.6"/><text x="0" y="6" text-anchor="middle" font-family="sans-serif" font-size="18" '
            f'font-weight="bold" fill="#fff">{p["number"]}</text></g>' for p, P in overview)
        with open(os.path.join(CATALOG, f"sheet_{sheet}.svg"), "w", encoding="utf-8") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W * 0.8:.0f}" height="{H * 0.8:.0f}" data-sheet="{sheet}">\n'
                    f'  <title>Sheet {sheet} — {len(overview)} pieces, as they lie on the sheet</title>\n'
                    f'  <rect x="0" y="0" width="{W}" height="{H}" fill="#fafafa"/>\n'
                    f'  <text x="12" y="36" font-family="sans-serif" font-size="28" fill="#2a7fff">{sheet}</text>\n'
                    f'{body}\n</svg>\n')
        print(sheet, len(overview), "pieces exported", flush=True)
    save_json(os.path.join(DATA, "catalog_index.json"), index)
    fv = np.array([f[2] for f in fidelity]); npts = np.array([f[1] for f in fidelity])
    save_json(os.path.join(DATA, "svg_fidelity.json"),
              dict(eps_u=EPS_U, points_median=float(np.median(npts)), dev_max_u=float(fv.max()),
                   dev_p95_u=float(np.percentile(fv, 95)), worst=sorted(fidelity, key=lambda f: -f[2])[:5]))
    print("fidelity: points/piece median %.0f, max deviation %.3f u" % (np.median(npts), fv.max()))


if __name__ == "__main__":
    main()
