from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional


HASH_ALGORITHM = "pbkdf2_sha256"
TOKEN_VERSION = "v1"
DEFAULT_HASH_ITERATIONS = 260_000


class AuthError(ValueError):
    """Raised when an auth token or credential cannot be verified."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def generate_password_hash(password: str, iterations: int = DEFAULT_HASH_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join([HASH_ALGORITHM, str(iterations), _b64encode(salt), _b64encode(digest)])


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, expected_digest = password_hash.split("$", 3)
        iterations = int(raw_iterations)
    except ValueError:
        return False

    if algorithm != HASH_ALGORITHM or iterations <= 0:
        return False

    try:
        salt = _b64decode(raw_salt)
    except Exception:
        return False

    actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(_b64encode(actual_digest), expected_digest)


def create_access_token(username: str, secret: str, ttl_seconds: int, issued_at: Optional[int] = None) -> str:
    now = int(issued_at if issued_at is not None else time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + ttl_seconds,
        "nonce": _b64encode(os.urandom(12)),
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _sign(encoded_payload, secret)
    return ".".join([TOKEN_VERSION, encoded_payload, signature])


def verify_access_token(token: str, secret: str, expected_username: str, now: Optional[int] = None) -> Dict[str, Any]:
    try:
        version, encoded_payload, signature = token.split(".", 2)
    except ValueError as exc:
        raise AuthError("Invalid token.") from exc

    if version != TOKEN_VERSION:
        raise AuthError("Invalid token.")

    expected_signature = _sign(encoded_payload, secret)
    if not hmac.compare_digest(signature, expected_signature):
        raise AuthError("Invalid token.")

    try:
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
    except Exception as exc:
        raise AuthError("Invalid token.") from exc

    if payload.get("sub") != expected_username:
        raise AuthError("Invalid token.")

    current_time = int(now if now is not None else time.time())
    try:
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Invalid token.") from exc

    if expires_at < current_time:
        raise AuthError("Token expired.")

    return payload


def _sign(encoded_payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def main() -> int:
    parser = argparse.ArgumentParser(description="PiDog auth helper commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hash_parser = subparsers.add_parser("hash-password", help="Generate a PIDOG_AUTH_PASSWORD_HASH value.")
    hash_parser.add_argument("password", nargs="?", help="Password to hash. If omitted, prompts securely.")

    args = parser.parse_args()
    if args.command == "hash-password":
        password = args.password or getpass.getpass("Password: ")
        print(generate_password_hash(password))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
