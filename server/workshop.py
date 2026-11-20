"""The workshop: live engine + journal file + current proposal (docs/ATELIER.md §3–§7).

Thread safety: one lock around every change; the engine computation (≈ 1.5 s) runs inside it, in a worker
thread (the web server stays responsive and answers "busy" meanwhile). Start-up (≈ 1 min) runs in a background
thread and reports its stages.
"""
import datetime, logging, os, sys, threading, time, uuid
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import numpy as np
import journal, live
from export_svg import display_turns

log = logging.getLogger("puzzle")
INITIAL_PLACED = 596                     # pieces of the frame and the moons, placed before the project
TOTAL_BLACK = 412
FIT_WARN_U = 3.0                         # a confirmed piece fitting worse than this (rms, u) gets a warning
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Paris"))


class Workshop:
    def __init__(self, data_dir, store_dir, app_version):
        self.data_dir, self.store_dir, self.app_version = data_dir, store_dir, app_version
        self.path = os.path.join(store_dir, "journal.jsonl")
        self.lock = threading.Lock()
        self.status, self.progress, self.error = "starting", dict(stage="start", done=0, total=1), None
        self.engine, self.events, self.current, self.cal, self.compute_s = None, [], None, {}, None
        self.recal, self.model_dir = None, data_dir
        self.started = time.time()
        threading.Thread(target=self._boot, name="boot", daemon=True).start()

    # ------------------------------------------------------------------ start-up
    def _progress(self, stage, done, total):
        self.progress = dict(stage=stage, done=done, total=total)

    def _boot(self):
        try:
            t = time.time()
            self.events = journal.load(self.path)
            self.model_dir = self._active_model()
            self.engine = live.LiveEngine(self.model_dir, progress=self._progress)
            last = next((x for x in reversed(self.events) if x["type"] in ("photo", "recalibration", "recalibration_failed")), None)
            if last and last["type"] == "photo":                      # the server stopped during the processing
                ev = self._event("recalibration_failed", None, photo_seq=last["seq"], reason="interrupted", detail={})
                journal.append(self.path, ev)
                self.events.append(ev)
            self._progress("journal", 0, max(1, len(self.events)))
            with self.lock:
                self._recompute()
            self.status = "ready"
            log.info("ready", extra=dict(ev="boot", seconds=round(time.time() - t, 1), events=len(self.events),
                                         model=self.engine.model_version))
            threading.Thread(target=self._backups, name="backups", daemon=True).start()
        except Exception as e:                                      # noqa: BLE001 — reported to the page and the log
            self.status, self.error = "error", repr(e)
            log.exception("boot failed")

    def _backups(self, keep=30):
        """Daily copy of the journal (backups/journal-YYYY-MM-DD.jsonl), the last `keep` days kept."""
        import shutil
        d = os.path.join(self.store_dir, "backups")
        while True:
            try:
                os.makedirs(d, exist_ok=True)
                name = os.path.join(d, f"journal-{datetime.datetime.now(TZ).date().isoformat()}.jsonl")
                if os.path.exists(self.path):
                    with self.lock:
                        shutil.copyfile(self.path, name + ".tmp")
                    os.replace(name + ".tmp", name)
                daily = sorted(f for f in os.listdir(d) if f.startswith("journal-20"))
                for f in daily[:-keep]:
                    os.remove(os.path.join(d, f))
            except OSError:
                log.exception("backup failed")
            time.sleep(3600)

    # ------------------------------------------------------------------ engine
    def _recompute(self):
        t = time.time()
        self.engine.apply(journal.placements(self.events))
        props, sigma = self.engine.proposals(progress=self._progress)
        self.current, self.cal = live.choose(self.engine, props, sigma, self.events)
        self.sigma = sigma
        self.compute_s = round(time.time() - t, 2)

    def _shape(self, pid, step=4):
        return np.round(self.engine.raw[pid]["contour_u"][::step], 1).ravel().tolist()

    def _piece_info(self, pid):
        p = self.engine.P[pid]
        return dict(piece=pid, sheet=p["sheet"], row=p["row"], col=p["col"], display_turns=display_turns(p), shape=self._shape(pid))

    # ------------------------------------------------------------------ state sent to the page
    def payload(self, busy=False):
        base = dict(status=self.status, app_version=self.app_version, busy=busy)
        if self.status != "ready":
            return dict(base, progress=self.progress, error=self.error)
        e = self.engine
        eff = journal.effective(self.events)
        placed = journal.placements(self.events)["placed"]
        today = datetime.datetime.now(TZ).date().isoformat()
        day = [x for x in eff if x.get("ts", "").startswith(today)]
        last_raw = self.events[-1] if self.events else None
        put_back = None
        if eff and eff[-1]["type"] == "does_not_fit" and last_raw is eff[-1]:
            put_back = dict(self._piece_info(eff[-1]["piece"]), cell=eff[-1]["cell"], remove=False)
        elif last_raw and last_raw["type"] == "undo":
            undone = next((x for x in self.events if x["seq"] == last_raw.get("target")), None)
            if undone and undone["type"] in ("fits", "fits_other_rotation"):
                put_back = dict(self._piece_info(undone["piece"]), cell=undone["cell"], remove=True)
        prop = None
        if self.current:
            c = self.current
            r0, c0 = c["r"], c["c"]
            near = [dict(cell=cid, piece=f["piece"], contour=np.round(f["contour"][::4], 1).ravel().tolist())
                    for cid, f in e.fitted.items() if abs(e.C[cid]["r"] - r0) <= 2 and abs(e.C[cid]["c"] - c0) <= 2]
            prop = dict(c, shape=self._shape(c["piece"]), near=near)
        undo = None
        if eff:
            u = eff[-1]
            undo = dict(seq=u["seq"], type=u["type"], cell=u.get("cell"), piece=u.get("piece"))
        b = e.b
        board = dict(cols=b["cols"], rows=b["rows"], cw=b["cell_w_u"], ch=b["cell_h_u"],
                     w=round(b["cols"] * b["cell_w_u"], 1), h=round(b["rows"] * b["cell_h_u"], 1))
        return dict(base, model_version=e.model_version, board=board, proposal=prop, put_back=put_back, undo=undo,
                    placed_pieces=[x["piece"] for x in placed],
                    counts=dict(placed=INITIAL_PLACED + len(placed), left=TOTAL_BLACK - len(placed), black_placed=len(placed),
                                total=INITIAL_PLACED + TOTAL_BLACK,
                                today_fits=sum(1 for x in day if x["type"] in ("fits", "fits_other_rotation")),
                                today_misses=sum(1 for x in day if x["type"] == "does_not_fit"),
                                since_photo=len(placed) - self._placed_at_last_photo()),
                    calibration=self.cal, compute_s=self.compute_s, recalibration=self._recal_payload())

    def _placed_at_last_photo(self):
        at = 0
        for x in self.events:
            if x["type"] == "recalibration":            # the reminder counts from the last photo taken into account
                at = len(journal.placements([y for y in self.events if y["seq"] <= x["seq"]])["placed"])
        return at

    def state(self):
        if self.lock.acquire(timeout=0.05):
            try:
                return self.payload()
            finally:
                self.lock.release()
        return dict(status=self.status, app_version=self.app_version, busy=True)

    # ------------------------------------------------------------------ answers
    def _event(self, typ, device, **fields):
        return dict(seq=journal.next_seq(self.events), id=fields.pop("id", None) or uuid.uuid4().hex, type=typ,
                    ts=datetime.datetime.now(TZ).isoformat(timespec="seconds"),
                    model=self.engine.model_version, app=self.app_version, device=(device or "")[:80], **fields)

    def answer(self, event_id, proposal_id, verdict, rotation=None, device=None):
        """Returns (http status, payload)."""
        if self.status != "ready":
            return 503, self.payload()
        if verdict not in journal.ANSWERS:
            return 400, dict(error="verdict")
        with self.lock:
            if any(x["id"] == event_id for x in self.events):
                return 200, dict(self.payload(), duplicate=True)            # resend after a network error
            c = self.current
            if not c or c["id"] != proposal_id:
                return 409, dict(self.payload(), stale=True)
            q = c["rotation"] // 90
            if verdict == "fits_other_rotation":
                if rotation not in (0, 90, 180, 270) or rotation == c["rotation"]:
                    return 400, dict(error="rotation")
                q = rotation // 90
            warning = None
            if verdict in ("fits", "fits_other_rotation"):
                rms = live.fit_quality(self.engine, c["cell"], c["piece"], q)
                if rms is not None and rms > FIT_WARN_U:
                    warning = dict(code="bad_fit", rms=round(rms, 2))
            snap = {k: c[k] for k in ("cell", "piece", "rotation", "rank", "chance", "bucket", "stability", "pct_cell", "quality", "weak")}
            ev = self._event(verdict, device, id=event_id, cell=c["cell"],
                             piece=None if verdict == "skip" else c["piece"], rotation=q * 90, proposal=snap)
            journal.append(self.path, ev)
            self.events.append(ev)
            self._recompute()
            log.info("answer", extra=dict(ev=verdict, cell=c["cell"], piece=c["piece"], seq=ev["seq"], compute_s=self.compute_s))
            return 200, dict(self.payload(), warning=warning)

    def undo(self, event_id, target, device=None):
        if self.status != "ready":
            return 503, self.payload()
        with self.lock:
            if any(x["id"] == event_id for x in self.events):
                return 200, dict(self.payload(), duplicate=True)
            eff = journal.effective(self.events)
            if not eff or eff[-1]["seq"] != target:
                return 409, dict(self.payload(), stale=True)
            ev = self._event("undo", device, id=event_id, target=target)
            journal.append(self.path, ev)
            self.events.append(ev)
            self._recompute()
            log.info("undo", extra=dict(ev="undo", seq=ev["seq"], target=target, compute_s=self.compute_s))
            return 200, self.payload()

    def photo(self, data, device=None):
        """Recalibration photo of the whole puzzle (JPEG): stored, then processed automatically (src/recalibrate.py,
        own process); the workshop keeps answering meanwhile and switches to the new model when the photo passes the
        checks. One photo at a time."""
        if self.status != "ready":
            return 503, self.payload()
        if self.recal and self.recal["state"] == "running":
            return 409, dict(self.payload(), error="recalibration_running")
        d = os.path.join(self.store_dir, "photos")
        os.makedirs(d, exist_ok=True)
        name = datetime.datetime.now(TZ).strftime("board_%Y-%m-%d_%H%M%S") + ".jpg"
        path = os.path.join(d, name)
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with self.lock:
            ev = self._event("photo", device, file=f"photos/{name}", size=len(data))
            journal.append(self.path, ev)
            self.events.append(ev)
            log.info("photo", extra=dict(ev="photo", file=name, size=len(data)))
            self._start_recalibration(path, ev["seq"])
            return 200, self.payload()

    # ------------------------------------------------------------------ recalibration (docs/ATELIER.md L6)
    def _active_model(self):
        """Model directory in force: the last successful recalibration of the journal, else the shipped model."""
        for x in reversed(self.events):
            if x["type"] == "recalibration":
                p = os.path.join(self.store_dir, x["model_dir"])
                if os.path.exists(os.path.join(p, "board.json")):
                    return p
                log.error("recalibrated model missing, shipped model used", extra=dict(ev="recalibration", dir=p))
                break
        return self.data_dir

    def _start_recalibration(self, photo_path, photo_seq):
        """Called with the lock held, right after the photo event."""
        import json, subprocess
        name = os.path.splitext(os.path.basename(photo_path))[0]
        work = os.path.join(self.store_dir, "recalibration", name)
        os.makedirs(work, exist_ok=True)
        pl = journal.placements(self.events)
        params = json.load(open(os.path.join(self.model_dir, "params.json")))
        placed = {x["cell"] for x in pl["placed"]}
        with open(os.path.join(work, "expected.json"), "w") as f:
            json.dump(dict(expected=[c for c in params["empty_cells"] if c not in placed], placements=pl), f)
        script = os.environ.get("PUZZLE_RECALIBRATE", os.path.join(ROOT, "src", "recalibrate.py"))
        proc = subprocess.Popen([sys.executable, "-I", script, photo_path, self.model_dir, work,
                                 os.path.join(work, "expected.json")],
                                stdout=open(os.path.join(work, "log.txt"), "w"), stderr=subprocess.STDOUT)
        self.recal = dict(state="running", stage="start", photo=name, photo_seq=photo_seq, work=work, proc=proc,
                          started=time.time())
        threading.Thread(target=self._watch_recalibration, name="recalibration", daemon=True).start()

    def _watch_recalibration(self):
        import json
        r = self.recal
        sp = os.path.join(r["work"], "status.json")
        st = {}
        while True:
            time.sleep(2)
            if os.path.exists(sp):
                try:
                    st = json.load(open(sp))
                except ValueError:
                    continue
                r["stage"] = st.get("stage", r["stage"])
            if r["proc"].poll() is not None:
                break
        st = json.load(open(sp)) if os.path.exists(sp) else dict(state="error", reason="error",
                                                                 detail=dict(error=f"exit {r['proc'].returncode}"))
        try:
            if st.get("state") == "ok":
                r["stage"] = "switch"
                eng = live.LiveEngine(st["model"])
                with self.lock:
                    pl = journal.placements(self.events)
                eng.apply(pl)
                eng.proposals()                                       # warm the caches outside the lock
                with self.lock:
                    old, self.engine, self.model_dir = self.engine, eng, st["model"]
                    ev = self._event("recalibration", None, photo_seq=r["photo_seq"],
                                     model_dir=os.path.relpath(st["model"], self.store_dir), version=eng.model_version,
                                     covered=len(pl["placed"]), detail=st.get("detail"))
                    journal.append(self.path, ev)
                    self.events.append(ev)
                    self._recompute()
                r.update(state="ok", stage="done", detail=st.get("detail"))
                log.info("recalibration", extra=dict(ev="recalibration", model=eng.model_version, photo=r["photo"]))
            else:
                raise RuntimeError(st)
        except Exception as e:                                        # noqa: BLE001 — rejected or failed: reported
            reason = st.get("reason", "error") if st.get("state") in ("rejected", "error") else "error"
            detail = st.get("detail") if st.get("state") == "rejected" else dict(error=repr(e)[:500])
            with self.lock:
                ev = self._event("recalibration_failed", None, photo_seq=r["photo_seq"], reason=reason, detail=detail)
                journal.append(self.path, ev)
                self.events.append(ev)
            r.update(state="rejected" if reason != "error" else "error", stage="done", reason=reason, detail=detail)
            log.warning("recalibration failed", extra=dict(ev="recalibration_failed", reason=reason, photo=r["photo"]))

    def _recal_payload(self):
        """Running recalibration, or the outcome of the last one while no answer has been given since."""
        if self.recal and self.recal["state"] == "running":
            return dict(state="running", stage=self.recal["stage"], seconds=round(time.time() - self.recal["started"]))
        for x in reversed(self.events):
            if x["type"] in journal.ANSWERS:
                return None
            if x["type"] == "recalibration":
                return dict(state="ok", covered=x.get("covered"))
            if x["type"] == "recalibration_failed":
                return dict(state="rejected", reason=x.get("reason"), detail=x.get("detail"))
        return None

    def diag(self):
        import resource
        return dict(status=self.status, app_version=self.app_version,
                    model_version=self.engine.model_version if self.engine else None, model_dir=self.model_dir,
                    recalibration=self._recal_payload() if self.events else None,
                    uptime_s=round(time.time() - self.started), events=len(self.events), compute_s=self.compute_s,
                    engine_load_s=round(self.engine.load_time, 1) if self.engine else None,
                    max_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
                    calibration=self.cal, progress=self.progress, error=self.error)
