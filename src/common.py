"""Paths, photo inventory and shared helpers."""
import os, json
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repository root
PHOTOS = os.path.join(ROOT, "photos")
DATA = os.path.join(ROOT, "data")
CATALOG = os.path.join(ROOT, "catalog")
BOARD_DIR = os.path.join(ROOT, "board")
REPORTS = os.path.join(ROOT, "reports")
SHEETS = ["A", "B", "C", "D", "E"]
SIDE_NAMES = ["N", "E", "S", "W"]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    def conv(o):
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        raise TypeError(type(o))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, default=conv, ensure_ascii=False)


MANIFEST = load_json(os.path.join(PHOTOS, "manifest.json"))
SHEET_PHOTOS = MANIFEST["sheets"]["photos"]
BOARD_PHOTO = MANIFEST["board"]["reference"]


def photo_path(rel):
    return os.path.join(PHOTOS, rel)


def photo_key(rel):
    """Short photo identifier: file name without extension (e.g. A_col01-05_2026-11-19)."""
    return os.path.splitext(os.path.basename(rel))[0]


def load_photo(rel):
    """Photo as displayed (EXIF orientation applied), BGR."""
    import cv2
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(photo_path(rel))).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def piece_id(sheet, number):
    return f"{sheet}{number:03d}"


def load_raw(path=None):
    """npz '<ID>__<field>' -> {ID: {field: array}}."""
    z = np.load(path or os.path.join(DATA, "pieces_raw.npz"))
    raw = {}
    for k in z.files:
        pid, f = k.split("__")
        raw.setdefault(pid, {})[f] = z[k]
    return raw


def cell_rc(cell_id):
    """'L18-C23' -> (row, col), 0-based."""
    r, c = cell_id[1:].split("-C")
    return int(r) - 1, int(c) - 1
