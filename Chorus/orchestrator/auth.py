from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket

DEFAULT_BOOTSTRAP_WORKSPACE_ID = "local-dev"
DEFAULT_BOOTSTRAP_TOKEN = "chorus-local-dev-token"
GUEST_WORKSPACE_TOKEN_PREFIX = "cw1"
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,80}$")


@dataclass(frozen=True)
class WorkspacePrincipal:
    workspace_id: str


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _parse_workspace_tokens(raw: str) -> dict[str, set[str]]:
    tokens_by_workspace: dict[str, set[str]] = {}
    for chunk in raw.replace("\n", ",").split(","):
        entry = chunk.strip()
        if not entry or "=" not in entry:
            continue
        workspace_id, token_blob = entry.split("=", 1)
        workspace_id = workspace_id.strip()
        tokens = {tok.strip() for tok in token_blob.split("|") if tok.strip()}
        if workspace_id and tokens:
            tokens_by_workspace.setdefault(workspace_id, set()).update(tokens)
    return tokens_by_workspace


def configured_workspace_tokens() -> dict[str, set[str]]:
    mapping = _parse_workspace_tokens(os.getenv("ORC_WORKSPACE_TOKENS", ""))
    if mapping:
        return mapping
    if not _env_flag("ORC_ALLOW_BOOTSTRAP_WORKSPACE", "1"):
        return {}
    workspace_id = os.getenv("ORC_BOOTSTRAP_WORKSPACE_ID", DEFAULT_BOOTSTRAP_WORKSPACE_ID).strip()
    token = os.getenv("ORC_BOOTSTRAP_TOKEN", DEFAULT_BOOTSTRAP_TOKEN).strip()
    if not workspace_id or not token:
        return {}
    return {workspace_id: {token}}


def workspace_auth_enabled() -> bool:
    return _env_flag("ORC_REQUIRE_WORKSPACE_AUTH", "1")


def guest_workspaces_enabled() -> bool:
    return _env_flag("ORC_ALLOW_GUEST_WORKSPACES", "1")


def _workspace_signing_secret() -> bytes:
    raw = (
        os.getenv("ORC_WORKSPACE_SIGNING_SECRET", "").strip()
        or os.getenv("ORC_BOOTSTRAP_TOKEN", DEFAULT_BOOTSTRAP_TOKEN).strip()
    )
    return raw.encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def normalize_workspace_id(workspace_id: str | None) -> str:
    value = (workspace_id or "").strip()
    if not value:
        value = f"workspace-{secrets.token_hex(4)}"
    if not _WORKSPACE_ID_RE.match(value):
        raise HTTPException(status_code=400, detail="invalid_workspace_id")
    return value


def issue_guest_workspace_token(workspace_id: str) -> str:
    workspace_id = normalize_workspace_id(workspace_id)
    nonce = secrets.token_urlsafe(12)
    payload = f"{workspace_id}.{nonce}"
    sig = hmac.new(_workspace_signing_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{GUEST_WORKSPACE_TOKEN_PREFIX}.{nonce}.{_b64url(sig)}"


def _valid_guest_workspace_token(workspace_id: str, token: str | None) -> bool:
    if not guest_workspaces_enabled() or not token:
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != GUEST_WORKSPACE_TOKEN_PREFIX or not parts[1] or not parts[2]:
        return False
    payload = f"{workspace_id}.{parts[1]}"
    expected = _b64url(hmac.new(_workspace_signing_secret(), payload.encode("utf-8"), hashlib.sha256).digest())
    return secrets.compare_digest(expected, parts[2])


def _extract_bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    prefix = "bearer "
    if not stripped.lower().startswith(prefix):
        raise HTTPException(status_code=401, detail="invalid_authorization_scheme")
    token = stripped[len(prefix):].strip()
    return token or None


def authenticate_workspace(workspace_id: str | None, bearer_token: str | None) -> WorkspacePrincipal:
    configured = configured_workspace_tokens()
    if not workspace_auth_enabled():
        fallback_workspace = workspace_id or next(iter(configured), DEFAULT_BOOTSTRAP_WORKSPACE_ID)
        return WorkspacePrincipal(workspace_id=fallback_workspace)
    if not workspace_id:
        raise HTTPException(status_code=401, detail="missing_workspace")
    if not bearer_token:
        raise HTTPException(status_code=401, detail="missing_token")
    workspace_id = normalize_workspace_id(workspace_id)
    allowed_tokens = configured.get(workspace_id)
    if allowed_tokens:
        for allowed in allowed_tokens:
            if secrets.compare_digest(allowed, bearer_token):
                return WorkspacePrincipal(workspace_id=workspace_id)
        if _valid_guest_workspace_token(workspace_id, bearer_token):
            return WorkspacePrincipal(workspace_id=workspace_id)
        raise HTTPException(status_code=403, detail="invalid_token")
    if _valid_guest_workspace_token(workspace_id, bearer_token):
        return WorkspacePrincipal(workspace_id=workspace_id)
    raise HTTPException(status_code=403, detail="unknown_workspace")


def require_workspace_http(request: Request) -> WorkspacePrincipal:
    workspace_id = request.headers.get("X-Chorus-Workspace")
    bearer_token = _extract_bearer_token(request.headers.get("Authorization"))
    return authenticate_workspace(workspace_id, bearer_token)


def require_workspace_websocket(websocket: WebSocket) -> WorkspacePrincipal:
    workspace_id = websocket.query_params.get("workspace_id") or websocket.headers.get("X-Chorus-Workspace")
    bearer_token = _extract_bearer_token(websocket.headers.get("Authorization")) or websocket.query_params.get("token")
    return authenticate_workspace(workspace_id, bearer_token)
