"""Password hashing, password policy, session tokens and the session cookie.

Nothing here touches the database — that is the job of ``app/services/auth.py``.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from ipaddress import ip_address
from math import ceil
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import Response

from app.config import Settings, TlsMode
from app.errors import AppError, ErrorCode

#: Shortest password accepted. Long enough to matter, short enough that people do
#: not write it on the fridge.
MIN_PASSWORD_LENGTH = 10

SESSION_COOKIE_NAME = "kehrwoche_session"
#: 32 random bytes, url-safe encoded; only its SHA-256 digest reaches the database.
SESSION_TOKEN_BYTES = 32

#: Double-submit CSRF: this cookie is readable by JavaScript on purpose — the client
#: mirrors its value into the header below, which a cross-site request cannot do.
CSRF_COOKIE_NAME = "kehrwoche_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_BYTES = 32
#: OPTIONS never changes state, GET and HEAD must not either.
CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

COMMON_PASSWORDS_FILE = Path(__file__).parent / "data" / "common_passwords.txt"

# Argon2id with the library defaults, which follow the current OWASP recommendation.
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash uses outdated parameters and should be refreshed."""
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@lru_cache
def common_passwords() -> frozenset[str]:
    lines = COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(
        stripped.lower()
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def validate_password(password: str, field: str = "password") -> None:
    """Enforce the password policy; raises :class:`AppError` with a 400 on failure."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AppError(
            400,
            ErrorCode.WEAK_PASSWORD,
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
            field,
            message_key="error.password.too_short",
            params={"count": MIN_PASSWORD_LENGTH},
        )
    if password.lower() in common_passwords():
        raise AppError(
            400,
            ErrorCode.WEAK_PASSWORD,
            "This password is among the most common ones and must not be used.",
            field,
            message_key="error.password.too_common",
        )


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """HttpOnly keeps JavaScript out, SameSite=Lax is the CSRF base line.

    ``Secure`` is dropped when TLS is off, otherwise the browser would discard the
    cookie on a plain HTTP instance.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_max_age_days * 24 * 3600,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.tls_enabled,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.tls_enabled,
    )


@dataclass(frozen=True)
class RateLimit:
    """How often something may be attempted before the brakes go on."""

    max_attempts: int
    window_seconds: float
    #: First lock-out; it doubles with every further violation of the same key.
    block_seconds: float
    max_block_seconds: float


#: Brute-force protection for sign-in, per IP address and per account.
LOGIN_RATE_LIMIT = RateLimit(
    max_attempts=5, window_seconds=300, block_seconds=60, max_block_seconds=3600
)
#: Keeps a single source from creating accounts in bulk.
REGISTER_RATE_LIMIT = RateLimit(
    max_attempts=5, window_seconds=3600, block_seconds=300, max_block_seconds=3600
)
#: Guessing a 12 character join code has to stay hopeless.
JOIN_RATE_LIMIT = RateLimit(
    max_attempts=5, window_seconds=900, block_seconds=60, max_block_seconds=3600
)
#: One reminder per chore and day — more would be nagging, not reminding.
REMINDER_RATE_LIMIT = RateLimit(
    max_attempts=1, window_seconds=86400, block_seconds=86400, max_block_seconds=86400
)


@dataclass
class _KeyState:
    attempts: list[float] = field(default_factory=list)
    blocked_until: float = 0.0
    violations: int = 0


class RateLimiter:
    """Sliding window limiter, in process and thread safe.

    Deliberately without Redis or any other service: an instance serves a handful of
    people, and a counter that is lost on restart is an acceptable trade for having no
    extra moving part. Sync routes run in a thread pool, hence the lock.
    """

    #: Above this many tracked keys a full sweep runs, so memory cannot grow unbounded.
    _SWEEP_THRESHOLD = 10_000

    def __init__(self, limit: RateLimit, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, _KeyState] = {}

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` may try again; 0 when it is not blocked."""
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return 0
            return max(0, ceil(state.blocked_until - self._clock()))

    def check(self, key: str) -> None:
        """Raise 429 with ``Retry-After`` while the key is locked out."""
        seconds = self.retry_after(key)
        if seconds > 0:
            raise AppError(
                429,
                ErrorCode.RATE_LIMITED,
                "Too many attempts. Please wait before trying again.",
                headers={"Retry-After": str(seconds)},
            )

    def record(self, key: str) -> None:
        """Count one attempt and lock the key out once the window is full."""
        with self._lock:
            now = self._clock()
            state = self._states.setdefault(key, _KeyState())
            window_start = now - self.limit.window_seconds
            state.attempts = [at for at in state.attempts if at > window_start]
            state.attempts.append(now)

            if len(state.attempts) >= self.limit.max_attempts:
                block = min(
                    self.limit.block_seconds * (2**state.violations),
                    self.limit.max_block_seconds,
                )
                state.blocked_until = now + block
                state.violations += 1
                state.attempts.clear()

            self._sweep(now)

    def reset(self, key: str) -> None:
        """Forget a key — used after a successful sign-in or join."""
        with self._lock:
            self._states.pop(key, None)

    def _sweep(self, now: float) -> None:
        """Drop keys that are neither blocked nor have attempts inside the window."""
        if len(self._states) < self._SWEEP_THRESHOLD:
            return
        window_start = now - self.limit.window_seconds
        self._states = {
            key: state
            for key, state in self._states.items()
            if state.blocked_until > now or any(at > window_start for at in state.attempts)
        }


@dataclass
class RateLimiters:
    """The limiters of one application instance."""

    login: RateLimiter
    register: RateLimiter
    join: RateLimiter
    reminder: RateLimiter

    @classmethod
    def create(cls, clock: Callable[[], float] = time.monotonic) -> RateLimiters:
        return cls(
            login=RateLimiter(LOGIN_RATE_LIMIT, clock),
            register=RateLimiter(REGISTER_RATE_LIMIT, clock),
            join=RateLimiter(JOIN_RATE_LIMIT, clock),
            reminder=RateLimiter(REMINDER_RATE_LIMIT, clock),
        )


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    """Readable by the client — that is the whole point of the double-submit pattern."""
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=settings.session_max_age_days * 24 * 3600,
        path="/",
        httponly=False,
        samesite="lax",
        secure=settings.tls_enabled,
    )


def clear_csrf_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        httponly=False,
        samesite="lax",
        secure=settings.tls_enabled,
    )


def csrf_token_matches(cookie_token: str | None, header_token: str | None) -> bool:
    if not cookie_token or not header_token:
        return False
    return secrets.compare_digest(cookie_token, header_token)


def session_cookie_removal_header(settings: Settings) -> dict[str, str]:
    """``Set-Cookie`` that deletes the session cookie, for use on error responses."""
    parts = [
        f"{SESSION_COOKIE_NAME}=",
        "Path=/",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if settings.tls_enabled:
        parts.append("Secure")
    return {"set-cookie": "; ".join(parts)}


# --- Transport security ---------------------------------------------------------------


#: Where a self-signed certificate lives inside the data volume.
TLS_DIR_NAME = "tls"
CERTIFICATE_NAME = "kehrwoche.crt"
PRIVATE_KEY_NAME = "kehrwoche.key"
#: Ten years: the warning is confirmed once per device,
#: and nobody wants to repeat that every year in their own home network.
CERTIFICATE_DAYS = 3650
#: How often the custom certificate is checked for a replacement.
CERTIFICATE_WATCH_SECONDS = 300


class TlsError(Exception):
    """Configuration the operator has to fix before the server can serve TLS."""


def certificate_paths(settings: Settings) -> tuple[Path, Path]:
    """Certificate and key of this instance — configured ones, or our own."""
    if settings.tls_mode is TlsMode.CUSTOM:
        if settings.tls_cert_file is None or settings.tls_key_file is None:
            raise TlsError("TLS_MODE=custom needs TLS_CERT_FILE and TLS_KEY_FILE.")
        return settings.tls_cert_file, settings.tls_key_file

    directory = settings.data_dir / TLS_DIR_NAME
    return directory / CERTIFICATE_NAME, directory / PRIVATE_KEY_NAME


def _subject_alternatives(hostnames: list[str]) -> x509.SubjectAlternativeName:
    """Names the certificate is valid for — host names and plain IP addresses.

    Home networks are reached by address as often as by name ("https://192.168.1.20"),
    and a browser only accepts that if the address is in the certificate itself.
    """
    entries: list[x509.GeneralName] = []
    for hostname in hostnames:
        try:
            entries.append(x509.IPAddress(ip_address(hostname)))
        except ValueError:
            entries.append(x509.DNSName(hostname))
    return x509.SubjectAlternativeName(entries)


def create_self_signed_certificate(settings: Settings) -> tuple[Path, Path]:
    """Write key and certificate into the data volume and return their paths."""
    hostnames = settings.hostnames or ["localhost"]
    certificate_file, key_file = certificate_paths(settings)
    certificate_file.parent.mkdir(parents=True, exist_ok=True)

    # An elliptic curve key: as strong as RSA-3072, a fraction of the work to generate —
    # which matters on the small machines these instances run on.
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # A minute of slack: a phone whose clock runs slightly behind must not see a
        # certificate from the future.
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=CERTIFICATE_DAYS))
        .add_extension(_subject_alternatives(hostnames), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # The key is a secret even inside the volume.
    key_file.chmod(0o600)
    return certificate_file, key_file


def ensure_certificate(settings: Settings) -> tuple[Path, Path] | None:
    """What the server needs to serve TLS, or ``None`` when TLS is off.

    A certificate that is already there is kept: regenerating on every start would ask
    every household member to confirm a new warning after every restart.
    """
    if settings.tls_mode is TlsMode.OFF:
        return None

    certificate_file, key_file = certificate_paths(settings)
    if settings.tls_mode is TlsMode.CUSTOM:
        missing = [str(path) for path in (certificate_file, key_file) if not path.is_file()]
        if missing:
            raise TlsError(f"Certificate files not found: {', '.join(missing)}")
        return certificate_file, key_file

    if certificate_file.is_file() and key_file.is_file():
        return certificate_file, key_file
    return create_self_signed_certificate(settings)


def certificate_fingerprint(paths: tuple[Path, Path]) -> tuple[tuple[float, int], ...]:
    """Cheap marker of "these are still the same files" — no parsing needed."""
    marks = []
    for path in paths:
        try:
            stat = path.stat()
            marks.append((stat.st_mtime, stat.st_size))
        except OSError:
            marks.append((0.0, 0))
    return tuple(marks)
