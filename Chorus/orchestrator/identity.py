from __future__ import annotations

import logging
import os
import tempfile
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.crypto import KeyPair

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_keypair: KeyPair | None = None


def get_orchestrator_keypair() -> KeyPair:
    """Load (or create) the orchestrator's Ed25519 keypair singleton.

    Path overridable via ORC_KEY_PATH. Defaults to ./orchestrator_ed25519.key.

    If the configured path is not writable (read-only volume, missing parent,
    permission-denied on hosted platforms), falls back to a fresh in-memory
    keypair so the service still boots. Signed-receipt durability across
    restarts is lost in that mode, but signaling/health still work.
    """
    global _keypair
    with _lock:
        if _keypair is not None:
            return _keypair
        path = os.getenv("ORC_KEY_PATH", "./orchestrator_ed25519.key")
        try:
            _keypair = KeyPair.load_or_create(path)
            return _keypair
        except (OSError, PermissionError, ValueError) as exc:
            logger.warning(
                "keypair load_or_create failed at %s (%s); trying tempdir fallback",
                path, exc,
            )
        try:
            fallback = os.path.join(tempfile.gettempdir(), "orchestrator_ed25519.key")
            _keypair = KeyPair.load_or_create(fallback)
            logger.warning("keypair using tempdir fallback at %s", fallback)
            return _keypair
        except (OSError, PermissionError, ValueError) as exc:
            logger.warning(
                "tempdir keypair fallback failed (%s); using in-memory ephemeral key",
                exc,
            )
        _keypair = KeyPair(Ed25519PrivateKey.generate())
        return _keypair
