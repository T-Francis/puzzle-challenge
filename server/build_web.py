"""Builds server/web-dist/: pages with content-hashed assets (the cache can never serve an old file).

  web-dist/demo/index.html, web-dist/atelier/index.html, web-dist/atelier/manifest.webmanifest   (no-cache)
  web-dist/assets/<name>.<hash>.<ext>                                                             (immutable)
  web-dist/VERSION                                                                                (app version)
References between files ("demo.js", 'board_clean.jpg', url(icon.svg)) are rewritten to /assets/<hashed name>.
The app version (hash of everything built) is injected where a page says __APP_VERSION__.
Usage: python3 -m server.build_web [out_dir]
"""
import hashlib, os, re, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = {"demo": os.path.join(ROOT, "demo"), "atelier": os.path.join(ROOT, "web", "atelier")}
TEXT = (".js", ".css", ".svg", ".webmanifest", ".html")
SPECIAL = ("index.html", "manifest.webmanifest")


def h(data):
    return hashlib.sha256(data).hexdigest()[:10]


def rewrite(text, names):
    for base, hashed in names.items():
        text = re.sub(r"""(["'(])""" + re.escape(base) + r"""(["')])""", lambda m: f"{m.group(1)}/assets/{hashed}{m.group(2)}", text)
    return text


def main(out=os.path.join(ROOT, "server", "web-dist")):
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "assets"))
    files = {}
    for page, d in PAGES.items():
        for f in sorted(os.listdir(d)):
            if f in SPECIAL or f.startswith("."):
                continue
            if f in files:
                raise SystemExit(f"duplicate asset name {f} ({files[f]} and {d})")
            files[f] = os.path.join(d, f)
    names, everything = {}, hashlib.sha256()
    # binary files first, then text files (which may reference binaries), in a fixed order
    for f in sorted(files, key=lambda f: (f.endswith(TEXT), f)):
        data = open(files[f], "rb").read()
        if f.endswith(TEXT):
            data = rewrite(data.decode("utf-8"), names).encode("utf-8")
        stem, ext = os.path.splitext(f)
        names[f] = f"{stem}.{h(data)}{ext}"
        with open(os.path.join(out, "assets", names[f]), "wb") as fh:
            fh.write(data)
        everything.update(names[f].encode())
    pages = {}
    for page, d in PAGES.items():
        for f in SPECIAL:
            p = os.path.join(d, f)
            if os.path.exists(p):
                pages[(page, f)] = rewrite(open(p, encoding="utf-8").read(), names)
                everything.update(pages[(page, f)].encode())
    version = everything.hexdigest()[:10]
    for (page, f), text in pages.items():
        os.makedirs(os.path.join(out, page), exist_ok=True)
        with open(os.path.join(out, page, f), "w", encoding="utf-8") as fh:
            fh.write(text.replace("__APP_VERSION__", version))
    with open(os.path.join(out, "VERSION"), "w") as fh:
        fh.write(version + "\n")
    print(f"web-dist: {len(names)} assets, {len(pages)} pages, version {version}")


if __name__ == "__main__":
    main(*sys.argv[1:])
