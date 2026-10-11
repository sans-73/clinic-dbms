"""Password hashing using only the Python standard library (scrypt).
Stored format:  scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>

ASSUMPTION A-02: the main track kit has its own app/security.py. If it exposes verify_password(password, stored)
we use it first, so hashes created by tools/bootstrap_owner.py keep working. Otherwise the format above is used.
"""
from __future__ import annotations
import hashlib
import hmac
import os

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    try:
        from app import security as ext                      # type: ignore
        if hasattr(ext, "hash_password"):
            return ext.hash_password(password)
    except Exception:
        pass
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        from app import security as ext                      # type: ignore
        if hasattr(ext, "verify_password"):
            return bool(ext.verify_password(password, stored))
    except Exception:
        pass
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
                           dklen=len(hash_hex) // 2, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(h.hex(), hash_hex)
    except Exception:
        return False
