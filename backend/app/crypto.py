"""Encryption at rest (NFR-1, NFR-4): AES-256-GCM with envelope keys.

* A master key lives outside the data directory: ``AI_ARTICLE_MASTER_KEY``
  (base64, 32 bytes) or the key file ``AI_ARTICLE_KEY_FILE``
  (default ``~/.ai-article/master.key``, created with mode 0600 on first use).
* Every project has its own data key, stored only wrapped by the master key
  in ``projects/<id>/.key``. Deleting that file makes the project's data
  unreadable (crypto-shredding for deletion on request).
* Ciphertext format: ``AIA1 | nonce(12) | ciphertext+tag``; the file's relative
  path is authenticated as associated data, so files cannot be swapped.
"""
import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AIA1"
_master = None


class CryptoError(RuntimeError):
    pass


def _key_file():
    return Path(os.environ.get("AI_ARTICLE_KEY_FILE", Path.home() / ".ai-article" / "master.key"))


def master_key() -> bytes:
    global _master
    if _master is not None:
        return _master
    env = os.environ.get("AI_ARTICLE_MASTER_KEY")
    if env:
        key = base64.b64decode(env)
    else:
        path = _key_file()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(base64.b64encode(secrets.token_bytes(32)))
            path.chmod(0o600)
        key = base64.b64decode(path.read_bytes())
    if len(key) != 32:
        raise CryptoError("мастер-ключ должен быть 32 байта (AES-256)")
    _master = key
    return key


def reset_master_cache():
    global _master
    _master = None


def encrypt(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if not blob.startswith(MAGIC):
        raise CryptoError("не зашифровано")
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], aad)
    except Exception as exc:
        raise CryptoError("не удалось расшифровать: неверный ключ или файл повреждён") from exc


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def new_wrapped_key() -> bytes:
    return encrypt(master_key(), secrets.token_bytes(32), b"project-key")


def unwrap(wrapped: bytes) -> bytes:
    return decrypt(master_key(), wrapped, b"project-key")


def seal(text: str, purpose: str) -> str:
    """Small secrets in the database (e.g. TOTP secrets), sealed with the master key."""
    return base64.b64encode(encrypt(master_key(), text.encode(), purpose.encode())).decode()


def unseal(token: str, purpose: str) -> str:
    return decrypt(master_key(), base64.b64decode(token), purpose.encode()).decode()
