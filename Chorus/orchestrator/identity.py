from __future__ import annotations

import os
import threading

from orchestrator.crypto import KeyPair

_lock = threading.Lock()
_keypair: KeyPair | None = None


def get_orchestrator_keypair() -> KeyPair:
    """Load (or create) the orchestrator's Ed25519 keypair singleton.

    Path overridable via ORC_KEY_PATH. Defaults to ./orchestrator_ed25519.key.
    """
    global _keypair
    with _lock:
        if _keypair is None:
            path = os.getenv("ORC_KEY_PATH", "./orchestrator_ed25519.key")
            _keypair = KeyPair.load_or_create(path)
        return _keypair
