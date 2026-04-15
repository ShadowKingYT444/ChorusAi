from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class KeyPair:
    """Ed25519 keypair wrapper that loads or creates a PKCS8-unencrypted file."""

    def __init__(self, priv: Ed25519PrivateKey) -> None:
        self._priv = priv

    @classmethod
    def load_or_create(cls, path: str | Path) -> "KeyPair":
        p = Path(path).expanduser()
        if p.exists():
            with open(p, "rb") as f:
                data = f.read()
            priv = serialization.load_pem_private_key(data, password=None)
            if not isinstance(priv, Ed25519PrivateKey):
                raise ValueError(f"key at {p} is not Ed25519")
            return cls(priv)

        # Generate new keypair
        p.parent.mkdir(parents=True, exist_ok=True)
        priv = Ed25519PrivateKey.generate()
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with open(p, "wb") as f:
            f.write(pem)
        try:
            os.chmod(p, 0o600)
        except OSError:
            # Windows may not fully support chmod; ignore.
            pass
        return cls(priv)

    def pubkey_b64(self) -> str:
        pub = self._priv.public_key()
        raw = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def sign_b64(self, message: str | bytes) -> str:
        msg_bytes = message.encode("utf-8") if isinstance(message, str) else message
        sig = self._priv.sign(msg_bytes)
        return base64.b64encode(sig).decode("ascii")


def verify_b64(pubkey_b64: str, message: str | bytes, signature_b64: str) -> bool:
    try:
        pub_bytes = base64.b64decode(pubkey_b64)
        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
        msg_bytes = message.encode("utf-8") if isinstance(message, str) else message
        pub.verify(base64.b64decode(signature_b64), msg_bytes)
        return True
    except (InvalidSignature, ValueError, Exception):  # noqa: BLE001
        return False
