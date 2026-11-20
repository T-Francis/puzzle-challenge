"""HTTP tests of the workshop server: cache headers, login, answers, undo, duplicates, stale answers, restart.
Run inside the server image: docker run --rm puzzle-server python3 -m unittest tests.test_server -v"""
import json, os, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    from starlette.testclient import TestClient
except ImportError:                                        # pipeline image: no web stack, these tests are skipped
    TestClient = None

PASSWORD = "test-password-123"


@unittest.skipIf(TestClient is None, "web stack not installed (run in the server image)")
class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server import auth as auth_mod, build_web
        cls.tmp = tempfile.TemporaryDirectory()
        web = os.path.join(cls.tmp.name, "web")
        build_web.main(web)
        os.environ.update(PUZZLE_STORE=os.path.join(cls.tmp.name, "store"), PUZZLE_WEB=web, PUZZLE_COOKIE_SECURE="0",
                          PUZZLE_ORIGIN="http://testserver", PUZZLE_PASSWORD_HASH=auth_mod.hash_password(PASSWORD, n=2 ** 12),
                          PUZZLE_MODEL=os.environ.get("PUZZLE_MODEL", os.path.join(ROOT, "data")))
        os.environ.pop("PUZZLE_DEV_AUTOLOGIN", None)
        from server import app as app_mod
        cls.app_mod = app_mod
        cls.client = TestClient(app_mod.app)
        t = time.time()
        while app_mod.workshop.status != "ready" and time.time() - t < 300:
            time.sleep(0.5)
        print(f"\n  workshop ready in {time.time() - t:.0f} s")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def login(self):
        r = self.client.post("/api/login", json=dict(password=PASSWORD))
        self.assertEqual(r.status_code, 200)

    def test_1_cache_and_security_headers(self):
        c = self.client
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-cache")
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        asset = next(x for x in os.listdir(os.path.join(os.environ["PUZZLE_WEB"], "assets")) if x.startswith("demo."))
        self.assertIn(f"/assets/{asset}", r.text)                       # the page references the hashed file
        r = c.get(f"/assets/{asset}")
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(c.get("/assets/nope.js").status_code, 404)
        r = c.get("/atelier", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (308, "/atelier/"))
        r = c.get("/atelier/")
        self.assertEqual(r.headers["cache-control"], "no-cache")
        self.assertIn(open(os.path.join(os.environ["PUZZLE_WEB"], "VERSION")).read().strip(), r.text)
        c.cookies.clear()
        r = c.get("/api/state")
        self.assertEqual((r.status_code, r.headers["cache-control"]), (401, "no-store"))
        self.assertEqual(c.post("/api/login", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_2_login_rate_limit(self):
        c = TestClient(self.app_mod.app, headers={"CF-Connecting-IP": "203.0.113.9"})
        codes = [c.post("/api/login", json=dict(password="wrong")).status_code for _ in range(6)]
        self.assertEqual(codes, [401] * 5 + [429])

    def test_3_answers_undo_duplicates(self):
        self.login()
        c = self.client
        s = c.get("/api/state").json()
        self.assertEqual(s["status"], "ready")
        p = s["proposal"]
        wave = {(x["cell"], x["candidates"][0]["piece"]) for x in json.load(open(os.path.join(ROOT, "reports", "wave_1.json")))["proposals"]} \
            if os.path.exists(os.path.join(ROOT, "reports", "wave_1.json")) else None
        if wave:
            self.assertIn((p["cell"], p["piece"]), wave)
        # stale proposal
        r = c.post("/api/answer", json=dict(event_id="e-stale", proposal_id="nope", verdict="fits"))
        self.assertEqual(r.status_code, 409)
        # does not fit -> put-back banner, next candidate
        r = c.post("/api/answer", json=dict(event_id="e1", proposal_id=p["id"], verdict="does_not_fit"))
        self.assertEqual(r.status_code, 200)
        s1 = r.json()
        self.assertEqual(s1["put_back"]["piece"], p["piece"])
        self.assertNotEqual((s1["proposal"]["cell"], s1["proposal"]["piece"]), (p["cell"], p["piece"]))
        # the same event sent again (network retry) is not counted twice
        r = c.post("/api/answer", json=dict(event_id="e1", proposal_id=p["id"], verdict="does_not_fit"))
        self.assertTrue(r.json().get("duplicate"))
        journal_path = os.path.join(os.environ["PUZZLE_STORE"], "journal.jsonl")
        self.assertEqual(len(open(journal_path).read().splitlines()), 1)
        # undo -> back to the first proposal
        r = c.post("/api/undo", json=dict(event_id="u1", target=s1["undo"]["seq"]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["proposal"]["id"], p["id"])
        # fits -> one more piece placed
        r = c.post("/api/answer", json=dict(event_id="e2", proposal_id=p["id"], verdict="fits"))
        s2 = r.json()
        self.assertEqual(s2["counts"]["black_placed"], 1)
        self.assertIn(p["piece"], s2["placed_pieces"])
        self.assertNotEqual(s2["proposal"]["piece"], p["piece"])
        self.assertLess(s2["compute_s"], 10)
        # a restarted server rebuilds exactly the same state from the journal
        from server.workshop import Workshop
        w = Workshop(os.environ["PUZZLE_MODEL"], os.environ["PUZZLE_STORE"], "test")
        t = time.time()
        while w.status != "ready" and time.time() - t < 300:
            time.sleep(0.5)
        self.assertEqual(w.payload()["proposal"]["id"], s2["proposal"]["id"])

    def test_4_photo_checks(self):
        self.login()
        r = self.client.post("/api/photo", content=b"not a jpeg", headers={"Content-Type": "image/jpeg"})
        self.assertEqual(r.status_code, 415)
        os.environ["PUZZLE_RECALIBRATE"] = os.path.join(ROOT, "tests", "stub_recalibrate.py")
        ws = self.app_mod.workshop

        def wait():
            t = time.time()
            while ws.recal and ws.recal["state"] == "running" and time.time() - t < 300:
                time.sleep(0.5)
            return self.client.get("/api/state").json()

        # a rejected photo: reported to the page with its reason, recorded in the journal, model unchanged
        r = self.client.post("/api/photo", content=b"\xff\xd8\xff\xe0REJECT", headers={"Content-Type": "image/jpeg"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["recalibration"]["state"], "running")
        self.assertEqual(self.client.post("/api/photo", content=b"\xff\xd8\xff\xe0x", headers={"Content-Type": "image/jpeg"}).status_code, 409)
        s = wait()
        self.assertEqual((s["recalibration"]["state"], s["recalibration"]["reason"]), ("rejected", "cells"))
        self.assertEqual(ws.events[-1]["type"], "recalibration_failed")
        model_before = ws.model_dir
        # an accepted photo: the workshop switches to the new model, the journal says so, proposals go on
        r = self.client.post("/api/photo", content=b"\xff\xd8\xff\xe0" + b"0" * 100, headers={"Content-Type": "image/jpeg"})
        self.assertEqual(r.status_code, 200)
        s = wait()
        self.assertEqual(s["recalibration"]["state"], "ok")
        self.assertEqual(ws.events[-1]["type"], "recalibration")
        self.assertNotEqual(ws.model_dir, model_before)
        self.assertTrue(ws.model_dir.startswith(os.environ["PUZZLE_STORE"]))
        self.assertIsNotNone(s["proposal"])
        self.assertEqual(ws._active_model(), ws.model_dir)          # what a restart would load

    def test_5_public_limits(self):
        """What an anonymous visitor can do: no oversized body is read, a password attack never locks passkeys out."""
        c = TestClient(self.app_mod.app, headers={"CF-Connecting-IP": "203.0.113.50"})
        big = b"{" + b" " * (70 * 1024) + b"}"
        self.assertEqual(c.post("/api/login", content=big, headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(c.post("/api/client-log", content=big).status_code, 413)
        chunked = iter([b"{", b" " * (70 * 1024), b"}"])                # no Content-Length: read and abandoned at 64 KB
        self.assertEqual(c.post("/api/login", content=chunked).status_code, 400)
        self.assertEqual(c.post("/api/photo", content=b"\xff\xd8\xff" + b"0" * 100).status_code, 401)
        for p in ("/api/state", "/api/diag"):
            self.assertEqual(c.get(p).status_code, 401)
        self.assertEqual(c.put("/api/state").status_code, 405)
        a = self.app_mod.auth
        a.locked_until = time.time() + 600                             # global password lock in force
        try:
            self.assertFalse(a.login_allowed("203.0.113.51"))
            self.assertTrue(a.login_allowed("203.0.113.51", passkey=True))
        finally:
            a.locked_until = 0.0


if __name__ == "__main__":
    unittest.main()
