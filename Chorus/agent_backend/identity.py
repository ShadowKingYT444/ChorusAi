"""Agent-side Ed25519 identity: keypair persisted at ~/.chorus/agent_ed25519.key."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_AGENT_KEY_PATH = os.path.expanduser("~/.chorus/agent_ed25519.key")


class AgentKeyPair:
    def __init__(self, priv: Ed25519PrivateKey) -> None:
        self._priv = priv

    def pubkey_b64(self) -> str:
        raw = self._priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def sign_b64(self, message: str | bytes) -> str:
        msg_bytes = message.encode("utf-8") if isinstance(message, str) else message
        return base64.b64encode(self._priv.sign(msg_bytes)).decode("ascii")


def load_or_create_agent_keypair(path: str | None = None) -> AgentKeyPair:
    p = Path(path or DEFAULT_AGENT_KEY_PATH).expanduser()
    if p.exists():
        with open(p, "rb") as f:
            data = f.read()
        priv = serialization.load_pem_private_key(data, password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            raise ValueError(f"key at {p} is not Ed25519")
        return AgentKeyPair(priv)

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
        pass
    return AgentKeyPair(priv)
