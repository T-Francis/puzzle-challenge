"""Stand-in for src/recalibrate.py in the server tests (the server image has no board photo): same arguments, same
status.json. A photo containing b"REJECT" is rejected (cells); any other one is accepted with a copy of the current model."""
import json, os, shutil, sys, time

photo, model_dir, work, expected = sys.argv[-4:]


def status(**kw):
    with open(os.path.join(work, "status.json.tmp"), "w") as f:
        json.dump(dict(kw, updated=time.time()), f)
    os.replace(os.path.join(work, "status.json.tmp"), os.path.join(work, "status.json"))


status(state="running", stage="board")
time.sleep(1)
if b"REJECT" in open(photo, "rb").read():
    status(state="rejected", stage="done", reason="cells", detail=dict(empty_on_photo=["L18-C23"], filled_on_photo=[]))
else:
    new = os.path.join(work, "model")
    os.makedirs(new, exist_ok=True)
    for f in ("pieces.json", "pieces_raw.npz", "params.json", "board.json", "board_sockets.npz"):
        shutil.copyfile(os.path.join(model_dir, f), os.path.join(new, f))
    if os.path.isdir(os.path.join(model_dir, "model")):
        shutil.copytree(os.path.join(model_dir, "model"), os.path.join(new, "model"), dirs_exist_ok=True)
    status(state="ok", stage="done", model=new, detail=dict(rotation=0, n_empty=412))
