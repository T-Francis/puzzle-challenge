"""Login of the workshop: one account, password (scrypt) + long-lived session cookie + passkeys (WebAuthn).

- Password hash in the environment (PUZZLE_PASSWORD_HASH), produced by: python3 -m server.auth hash
- Sessions: random 256-bit token in an HttpOnly, Secure, SameSite=Strict cookie; only its SHA-256 is stored
  (data/auth/sessions.json, atomic writes); valid 1 year, extended on use.
- Rate limit: 5 failed logins per 15 min and per address; 20 failures in an hour lock the PASSWORD login for 15 min
  (passkeys stay usable: they cannot be guessed).
- Passkeys: registration (attestation "none") and authentication, signature checked with the stored COSE key
  (ES256 or RS256). Challenges are single use and expire after 5 min.
"""
import base64, getpass, hashlib, hmac, json, os, secrets, sys, threading, time

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

SESSION_DAYS = 365
SESSION_TOUCH_S = 3600            # an active session is saved again at most once per hour
FAIL_WINDOW_S, FAIL_PER_IP, FAIL_GLOBAL, LOCK_S = 15 * 60, 5, 20, 15 * 60
CHALLENGE_S = 300


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- password
def hash_password(pw, n=2 ** 15, r=8, p=1):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{b64u(salt)}:{b64u(h)}"   # ":" separators: safe in .env files and compose


def check_password(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split(":")
        got = hashlib.scrypt(pw.encode(), salt=b64u_dec(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, b64u_dec(h))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- storage
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Auth:
    def __init__(self, store_dir, password_hash, rp_id, origin):
        self.dir = os.path.join(store_dir, "auth")
        self.password_hash = password_hash or ""
        self.rp_id, self.origin = rp_id, origin
        self.lock = threading.Lock()
        self.sessions = _read(os.path.join(self.dir, "sessions.json"), {})
        self.passkeys = _read(os.path.join(self.dir, "passkeys.json"), [])
        self.fails = {}                    # ip -> [timestamps]
        self.locked_until = 0.0
        self.challenges = {}               # challenge (b64u) -> (expiry, kind, session hash or None)

    # ------------------------------------------------------------ sessions
    def _save_sessions(self):
        _write(os.path.join(self.dir, "sessions.json"), self.sessions)

    def new_session(self):
        token = secrets.token_urlsafe(32)
        h = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            self.sessions[h] = dict(created=now, seen=now, expires=now + SESSION_DAYS * 86400)
            self._save_sessions()
        return token

    def check_session(self, token):
        """Session hash if the token is valid (and extends it), else None."""
        if not token:
            return None
        h = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self.lock:
            s = self.sessions.get(h)
            if not s or s["expires"] < now:
                return None
            if now - s["seen"] > SESSION_TOUCH_S:
                s.update(seen=now, expires=now + SESSION_DAYS * 86400)
                self._save_sessions()
        return h

    def end_session(self, token):
        if not token:
            return
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            if self.sessions.pop(h, None):
                self._save_sessions()

    # ------------------------------------------------------------ password login with rate limit
    def login_allowed(self, ip, passkey=False):
        """Per address: FAIL_PER_IP failures per FAIL_WINDOW_S. The global lock (many failures from many addresses)
        only stops passwords: a passkey cannot be guessed, so a password attack never locks the owner of the
        fingerprint out."""
        now = time.time()
        if len(self.fails) > 10000:                     # forget old addresses (memory bound under a spread attack)
            self.fails = {k: v for k, v in self.fails.items() if v and now - v[-1] < FAIL_WINDOW_S}
        recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW_S]
        self.fails[ip] = recent
        return (passkey or now >= self.locked_until) and len(recent) < FAIL_PER_IP

    def login(self, ip, password):
        """Returns a session token, None on a wrong password, or 'limited'."""
        if not self.login_allowed(ip):
            return "limited"
        ok = bool(self.password_hash) and check_password(password or "", self.password_hash)
        if ok:
            self.fails.pop(ip, None)
            return self.new_session()
        now = time.time()
        self.fails.setdefault(ip, []).append(now)
        if sum(1 for ts in self.fails.values() for t in ts if now - t < 3600) >= FAIL_GLOBAL:
            self.locked_until = now + LOCK_S
        return None

    # ------------------------------------------------------------ passkeys (WebAuthn)
    def _challenge(self, kind, session=None):
        now = time.time()
        self.challenges = {k: v for k, v in self.challenges.items() if v[0] > now}
        c = b64u(secrets.token_bytes(32))
        self.challenges[c] = (now + CHALLENGE_S, kind, session)
        return c

    def _take_challenge(self, c, kind, session=None):
        v = self.challenges.pop(c, None)
        return bool(v) and v[0] > time.time() and v[1] == kind and v[2] == session

    def _client_data(self, cdj_b64, kind, session=None):
        cdj = b64u_dec(cdj_b64)
        cd = json.loads(cdj)
        if cd.get("type") != kind:
            raise ValueError("type")
        if cd.get("origin") != self.origin:
            raise ValueError("origin")
        if not self._take_challenge(cd.get("challenge", ""), kind, session):
            raise ValueError("challenge")
        return cdj

    def _auth_data(self, ad):
        if ad[:32] != hashlib.sha256(self.rp_id.encode()).digest():
            raise ValueError("rp id")
        flags = ad[32]
        if not flags & 0x01:
            raise ValueError("user presence")
        return flags, int.from_bytes(ad[33:37], "big")

    def register_options(self, session):
        return dict(publicKey=dict(
            rp=dict(id=self.rp_id, name="Puzzle"),
            user=dict(id=b64u(b"puzzle-user"), name="puzzle", displayName="Puzzle"),
            challenge=self._challenge("webauthn.create", session),
            pubKeyCredParams=[dict(type="public-key", alg=-7), dict(type="public-key", alg=-257)],
            timeout=CHALLENGE_S * 1000, attestation="none",
            authenticatorSelection=dict(residentKey="preferred", userVerification="preferred"),
            excludeCredentials=[dict(type="public-key", id=k["id"]) for k in self.passkeys]))

    def register_verify(self, session, cred, name="phone"):
        self._client_data(cred["response"]["clientDataJSON"], "webauthn.create", session)
        att = cbor2.loads(b64u_dec(cred["response"]["attestationObject"]))
        ad = att["authData"]
        flags, count = self._auth_data(ad)
        if not flags & 0x40:
            raise ValueError("no credential data")
        n = int.from_bytes(ad[53:55], "big")
        cred_id = ad[55:55 + n]
        cose = cbor2.loads(ad[55 + n:])
        _public_key(cose)                         # refuse an unsupported key now rather than at login
        with self.lock:
            self.passkeys = [k for k in self.passkeys if k["id"] != b64u(cred_id)]
            self.passkeys.append(dict(id=b64u(cred_id), key=b64u(cbor2.dumps(cose)), count=count, name=name, added=time.time()))
            _write(os.path.join(self.dir, "passkeys.json"), self.passkeys)
        return True

    def login_options(self):
        return dict(publicKey=dict(challenge=self._challenge("webauthn.get"), rpId=self.rp_id, timeout=CHALLENGE_S * 1000,
                                   userVerification="preferred",
                                   allowCredentials=[dict(type="public-key", id=k["id"]) for k in self.passkeys]))

    def login_verify(self, cred):
        """Returns a session token or raises ValueError."""
        k = next((k for k in self.passkeys if k["id"] == cred.get("id")), None)
        if not k:
            raise ValueError("unknown credential")
        cdj = self._client_data(cred["response"]["clientDataJSON"], "webauthn.get")
        ad = b64u_dec(cred["response"]["authenticatorData"])
        flags, count = self._auth_data(ad)
        sig = b64u_dec(cred["response"]["signature"])
        key, alg = _public_key(cbor2.loads(b64u_dec(k["key"])))
        signed = ad + hashlib.sha256(cdj).digest()
        if alg == -7:
            key.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
        else:
            key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
        if count and k["count"] and count <= k["count"]:
            raise ValueError("signature counter went back (cloned key?)")
        with self.lock:
            k["count"] = count
            _write(os.path.join(self.dir, "passkeys.json"), self.passkeys)
        return self.new_session()


def _public_key(cose):
    """COSE key -> (cryptography public key, alg). Raises ValueError (cryptography raises InvalidSignature on verify)."""
    kty, alg = cose.get(1), cose.get(3)
    if kty == 2 and alg == -7 and cose.get(-1) == 1:
        x, y = int.from_bytes(cose[-2], "big"), int.from_bytes(cose[-3], "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key(), -7
    if kty == 3 and alg == -257:
        n, e = int.from_bytes(cose[-1], "big"), int.from_bytes(cose[-2], "big")
        return rsa.RSAPublicNumbers(e, n).public_key(), -257
    raise ValueError("unsupported key")


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        pw = getpass.getpass("Password (at least 10 characters): ")
        if len(pw) < 10 or pw != getpass.getpass("Again: "):
            sys.exit("Too short, or the two entries differ.")
        print(hash_password(pw))
    else:
        print("usage: python3 -m server.auth hash")
