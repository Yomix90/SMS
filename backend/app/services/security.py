"""Hachage des mots de passe et tokens, chiffrement symétrique, limiteur de débit."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app

_hasher = PasswordHasher()  # argon2id, paramètres OWASP par défaut


# --- Mots de passe -----------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> tuple[bool, str | None]:
    """Retourne (valide, nouveau_hash_si_re-hachage_nécessaire)."""
    try:
        _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if _hasher.check_needs_rehash(stored_hash):
        return True, _hasher.hash(password)
    return True, None


def password_problems(password: str) -> list[str]:
    """Règles minimales de robustesse (messages traduits côté interface)."""
    problems = []
    if len(password) < 10:
        problems.append("min_length")
    if password.lower() == password or password.upper() == password:
        problems.append("mixed_case")
    if not any(c.isdigit() for c in password):
        problems.append("digit")
    return problems


# --- Tokens (appareils, clés API, codes d'appairage) ---------------------------

def generate_token(prefix: str, nbytes: int = 30) -> str:
    return f"{prefix}{secrets.token_urlsafe(nbytes)}"


def hash_token(token: str) -> str:
    """SHA-256 : suffisant pour des secrets aléatoires à haute entropie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --- Chiffrement symétrique (secrets de webhooks) -----------------------------

def _fernet() -> MultiFernet:
    raw = current_app.config.get("APP_ENCRYPTION_KEYS") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        if not current_app.debug and not current_app.testing:
            raise RuntimeError("APP_ENCRYPTION_KEYS doit être défini en production")
        # Dev : clé dérivée de SECRET_KEY (stable entre redémarrages)
        import base64
        digest = hashlib.sha256(current_app.config["SECRET_KEY"].encode()).digest()
        keys = [base64.urlsafe_b64encode(digest).decode()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Impossible de déchiffrer : clé APP_ENCRYPTION_KEYS invalide") from exc


def rotate(ciphertext: str) -> str:
    """Re-chiffre avec la clé primaire (première de la liste)."""
    return _fernet().rotate(ciphertext.encode("ascii")).decode("ascii")


def hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


# --- Limiteur de débit en mémoire (fenêtre fixe) --------------------------------

class RateLimiter:
    """Compteur par clé et par fenêtre. Suffisant pour un process unique ;
    remplacer par Redis si l'on passe à plusieurs workers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, int], int] = defaultdict(int)
        self._last_gc = time.time()

    def hit(self, key: str, limit: int, window_sec: int = 60) -> tuple[bool, int, int]:
        """Retourne (autorisé, restant, secondes_avant_reset)."""
        now = time.time()
        bucket = int(now // window_sec)
        reset_in = int(window_sec - (now % window_sec)) or window_sec
        with self._lock:
            self._gc(now)
            count = self._counters[(key, bucket)] + 1
            if count > limit:
                return False, 0, reset_in
            self._counters[(key, bucket)] = count
            return True, max(limit - count, 0), reset_in

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()

    def _gc(self, now: float) -> None:
        if now - self._last_gc < 300:
            return
        self._last_gc = now
        current = int(now // 60)
        for k in [k for k in self._counters if k[1] < current - 1440]:
            del self._counters[k]


rate_limiter = RateLimiter()
