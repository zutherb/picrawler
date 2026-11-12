"""Passwords (scrypt), sessions and login rate limiting."""
import base64
import hashlib
import hmac
import secrets
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
ROLES = ("viewer", "admin")


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, _b64(salt), _b64(digest))


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class Users:
    def __init__(self, users_cfg):
        self._users = {}
        for name, spec in (users_cfg or {}).items():
            role = spec.get("role", "viewer")
            if role not in ROLES:
                raise ValueError("user %s: role must be one of %s" % (name, ", ".join(ROLES)))
            if not str(spec.get("password_hash", "")).startswith("scrypt$"):
                raise ValueError("user %s: password_hash must be a scrypt hash (python -m services.web.hashpw)" % name)
            self._users[name.strip().lower()] = (role, spec["password_hash"])
        # verify against this when the user does not exist, so timing does not tell
        self._dummy = hash_password(secrets.token_hex(8))

    def __len__(self):
        return len(self._users)

    def check(self, name, password):
        """Returns the role, or None. User names ignore case and surrounding spaces
        (phones capitalize the first letter); passwords are compared exactly."""
        role, stored = self._users.get((name or "").strip().lower(), (None, self._dummy))
        ok = verify_password(password, stored)
        return role if ok and role else None


class Sessions:
    def __init__(self, idle_s=12 * 3600, clock=time.monotonic):
        self.idle_s = idle_s
        self._clock = clock
        self._sessions = {}      # token -> dict(user, role, csrf, seen)

    def create(self, user, role):
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {"user": user, "role": role, "csrf": secrets.token_urlsafe(24),
                                 "seen": self._clock()}
        return token

    def get(self, token):
        session = self._sessions.get(token or "")
        if session is None:
            return None
        now = self._clock()
        if now - session["seen"] > self.idle_s:
            del self._sessions[token]
            return None
        session["seen"] = now
        return session

    def drop(self, token):
        self._sessions.pop(token or "", None)


class RateLimiter:
    """At most `limit` attempts per `window_s` per key (e.g. client IP)."""

    def __init__(self, limit=5, window_s=60.0, clock=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits = {}

    def allow(self, key):
        now = self._clock()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window_s]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
