"""Web server: public demo (/), workshop (/atelier/), API (/api/…). Run: uvicorn server.app:app

Cache rules (docs/ATELIER.md §8): HTML no-cache; /assets/* content-hashed and immutable; API no-store.
Security: strict CSP, no inline script, same-origin POST only, session cookie HttpOnly / Secure / SameSite=Strict.
Configuration (environment): PUZZLE_ORIGIN (https://your.domain), PUZZLE_PASSWORD_HASH, PUZZLE_STORE (/data),
PUZZLE_MODEL (data dir of the model), PUZZLE_WEB (built web files), PUZZLE_COOKIE_SECURE (1, or 0 in local dev).
"""
import json, logging, os, sys, time
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server.auth import Auth
from server.workshop import Workshop

ORIGIN = os.environ.get("PUZZLE_ORIGIN", "http://localhost:8080").rstrip("/")
RP_ID = urlparse(ORIGIN).hostname
STORE = os.environ.get("PUZZLE_STORE", "/data")
MODEL = os.environ.get("PUZZLE_MODEL", os.path.join(ROOT, "data"))
WEB = os.environ.get("PUZZLE_WEB", os.path.join(ROOT, "server", "web-dist"))
SECURE = os.environ.get("PUZZLE_COOKIE_SECURE", "1") == "1"
COOKIE = "__Host-puzzle" if SECURE else "puzzle"
MAX_PHOTO = 25 * 1024 * 1024    # 64 Mpx phone photos weigh 10–15 MB; stored as is, never decoded here
MAX_JSON = 64 * 1024
APP_VERSION = open(os.path.join(WEB, "VERSION")).read().strip() if os.path.exists(os.path.join(WEB, "VERSION")) else "dev"

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


# ---------------------------------------------------------------- logging: one JSON line per event on stdout
class JsonFormatter(logging.Formatter):
    SKIP = set(vars(logging.makeLogRecord({})))

    def format(self, r):
        d = dict(t=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r.created)), level=r.levelname.lower(), msg=r.getMessage())
        d.update({k: v for k, v in vars(r).items() if k not in self.SKIP and k not in ("message", "asctime")})
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(JsonFormatter())
log = logging.getLogger("puzzle")
log.handlers[:] = [_h]
log.setLevel(logging.INFO)
log.propagate = False

auth = Auth(STORE, os.environ.get("PUZZLE_PASSWORD_HASH", ""), RP_ID, ORIGIN)
workshop = Workshop(MODEL, STORE, APP_VERSION)


# ---------------------------------------------------------------- helpers
def client_ip(request):
    # behind Traefik, which only accepts Cloudflare addresses: CF-Connecting-IP is the real client
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def api(data, status=200):
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


DEV_AUTOLOGIN = os.environ.get("PUZZLE_DEV_AUTOLOGIN") == "1" and not SECURE   # local review only, never with HTTPS cookies


def session(request):
    if DEV_AUTOLOGIN:
        return "dev"
    return auth.check_session(request.cookies.get(COOKIE))


def set_cookie(resp, token):
    resp.set_cookie(COOKIE, token, max_age=365 * 86400, path="/", secure=SECURE, httponly=True, samesite="strict")


async def body_json(request):
    raw = await request.body()
    if len(raw) > MAX_JSON:
        raise ValueError("too large")
    return json.loads(raw or b"{}")


def page(name):
    async def handler(request):
        return FileResponse(os.path.join(WEB, name, "index.html"), headers={"Cache-Control": "no-cache"},
                            media_type="text/html; charset=utf-8")
    return handler


# ---------------------------------------------------------------- static
async def asset(request):
    name = request.path_params["name"]
    path = os.path.join(WEB, "assets", name)
    if "/" in name or name.startswith(".") or not os.path.isfile(path):
        return PlainTextResponse("not found", 404, headers={"Cache-Control": "no-store"})
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def manifest(request):
    return FileResponse(os.path.join(WEB, "atelier", "manifest.webmanifest"), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


async def robots(request):
    return PlainTextResponse("User-agent: *\nDisallow: /atelier/\nDisallow: /api/\n", headers={"Cache-Control": "no-cache"})


async def atelier_redirect(request):
    return RedirectResponse("/atelier/", 308)


# ---------------------------------------------------------------- API
async def health(request):
    return api(dict(ok=True, status=workshop.status, app_version=APP_VERSION))


async def me(request):
    return api(dict(logged=bool(session(request)), passkeys=len(auth.passkeys), app_version=APP_VERSION))


async def login(request):
    try:
        data = await body_json(request)
    except ValueError:
        return api(dict(error="bad_request"), 400)
    ip = client_ip(request)
    token = await run_in_threadpool(auth.login, ip, str(data.get("password", "")))
    if token == "limited":
        log.warning("login limited", extra=dict(ev="login_limited", ip=ip))
        return api(dict(error="too_many_attempts"), 429)
    if not token:
        log.warning("login failed", extra=dict(ev="login_failed", ip=ip))
        return api(dict(error="wrong_password"), 401)
    log.info("login", extra=dict(ev="login", ip=ip, method="password"))
    resp = api(dict(ok=True))
    set_cookie(resp, token)
    return resp


async def logout(request):
    auth.end_session(request.cookies.get(COOKIE))
    resp = api(dict(ok=True))
    resp.delete_cookie(COOKIE, path="/", secure=SECURE, httponly=True, samesite="strict")
    return resp


async def passkey_register_options(request):
    s = session(request)
    return api(auth.register_options(s)) if s else api(dict(error="login"), 401)


async def passkey_register_verify(request):
    s = session(request)
    if not s:
        return api(dict(error="login"), 401)
    try:
        data = await body_json(request)
        auth.register_verify(s, data)
    except Exception as e:                                          # noqa: BLE001 — any failure = refused
        log.warning("passkey registration refused", extra=dict(ev="passkey_register_refused", why=str(e)[:80]))
        return api(dict(error="passkey_refused"), 400)
    log.info("passkey registered", extra=dict(ev="passkey_registered", ip=client_ip(request)))
    return api(dict(ok=True, passkeys=len(auth.passkeys)))


async def passkey_login_options(request):
    if not auth.passkeys:
        return api(dict(error="no_passkey"), 404)
    return api(auth.login_options())


async def passkey_login_verify(request):
    ip = client_ip(request)
    if not auth.login_allowed(ip):
        return api(dict(error="too_many_attempts"), 429)
    try:
        token = auth.login_verify(await body_json(request))
    except Exception as e:                                          # noqa: BLE001
        auth.fails.setdefault(ip, []).append(time.time())
        log.warning("passkey login refused", extra=dict(ev="login_failed", ip=ip, method="passkey", why=str(e)[:80]))
        return api(dict(error="passkey_refused"), 401)
    log.info("login", extra=dict(ev="login", ip=ip, method="passkey"))
    resp = api(dict(ok=True))
    set_cookie(resp, token)
    return resp


async def state(request):
    if not session(request):
        return api(dict(error="login"), 401)
    return api(workshop.state())


async def answer(request):
    if not session(request):
        return api(dict(error="login"), 401)
    try:
        d = await body_json(request)
        args = (str(d["event_id"])[:64], str(d["proposal_id"])[:64], str(d["verdict"]), d.get("rotation"), d.get("device"))
    except (ValueError, KeyError):
        return api(dict(error="bad_request"), 400)
    status, payload = await run_in_threadpool(workshop.answer, *args)
    return api(payload, status)


async def undo(request):
    if not session(request):
        return api(dict(error="login"), 401)
    try:
        d = await body_json(request)
        args = (str(d["event_id"])[:64], int(d["target"]), d.get("device"))
    except (ValueError, KeyError, TypeError):
        return api(dict(error="bad_request"), 400)
    status, payload = await run_in_threadpool(workshop.undo, *args)
    return api(payload, status)


async def photo(request):
    if not session(request):
        return api(dict(error="login"), 401)
    if int(request.headers.get("content-length") or 0) > MAX_PHOTO:
        return api(dict(error="too_large"), 413)
    data = await request.body()
    if len(data) > MAX_PHOTO:
        return api(dict(error="too_large"), 413)
    if not data.startswith(b"\xff\xd8\xff"):
        return api(dict(error="not_jpeg"), 415)
    status, payload = await run_in_threadpool(workshop.photo, data, request.headers.get("x-device"))
    return api(payload, status)


_client_logs = []


async def client_log(request):
    now = time.time()
    _client_logs[:] = [t for t in _client_logs if now - t < 60]
    if len(_client_logs) >= 30:
        return api(dict(ok=False), 429)
    _client_logs.append(now)
    try:
        d = await body_json(request)
    except ValueError:
        return api(dict(ok=False), 400)
    log.warning("browser error", extra=dict(ev="client_error", message=str(d.get("message", ""))[:300],
                                            where=str(d.get("where", ""))[:200], page=str(d.get("page", ""))[:80]))
    return api(dict(ok=True))


async def diag(request):
    if not session(request):
        return api(dict(error="login"), 401)
    return api(workshop.diag())


# ---------------------------------------------------------------- middleware: headers, same-origin POST, access log
class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t = time.time()
        if request.method not in ("GET", "HEAD", "POST"):
            return PlainTextResponse("method not allowed", 405)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != ORIGIN:
                log.warning("cross-origin post refused", extra=dict(ev="origin_refused", origin=origin[:80]))
                return api(dict(error="origin"), 403)
        resp: Response = await call_next(request)
        h = resp.headers
        h.setdefault("Cache-Control", "no-store")
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "same-origin"
        h["X-Frame-Options"] = "DENY"
        h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=()"
        h["X-App-Version"] = APP_VERSION
        if SECURE:
            h["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(("/atelier", "/api")):
            h["X-Robots-Tag"] = "noindex, nofollow"
        if not request.url.path.startswith(("/assets/", "/api/health")):
            log.info("request", extra=dict(ev="http", method=request.method, path=request.url.path, status=resp.status_code,
                                           ms=round(1000 * (time.time() - t))))
        return resp


app = Starlette(routes=[
    Route("/", page("demo")),
    Route("/atelier", atelier_redirect),
    Route("/atelier/", page("atelier")),
    Route("/atelier/manifest.webmanifest", manifest),
    Route("/assets/{name}", asset),
    Route("/robots.txt", robots),
    Route("/api/health", health),
    Route("/api/me", me),
    Route("/api/login", login, methods=["POST"]),
    Route("/api/logout", logout, methods=["POST"]),
    Route("/api/passkey/register/options", passkey_register_options, methods=["POST"]),
    Route("/api/passkey/register/verify", passkey_register_verify, methods=["POST"]),
    Route("/api/passkey/login/options", passkey_login_options, methods=["POST"]),
    Route("/api/passkey/login/verify", passkey_login_verify, methods=["POST"]),
    Route("/api/state", state),
    Route("/api/answer", answer, methods=["POST"]),
    Route("/api/undo", undo, methods=["POST"]),
    Route("/api/photo", photo, methods=["POST"]),
    Route("/api/client-log", client_log, methods=["POST"]),
    Route("/api/diag", diag),
], middleware=[Middleware(Guard)])
