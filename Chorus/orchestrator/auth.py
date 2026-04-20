from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket

DEFAULT_BOOTSTRAP_WORKSPACE_ID = "local-dev"
DEFAULT_BOOTSTRAP_TOKEN = "chorus-local-dev-token"


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
    allowed_tokens = configured.get(workspace_id)
    if not allowed_tokens:
        raise HTTPException(status_code=403, detail="unknown_workspace")
    for allowed in allowed_tokens:
        if secrets.compare_digest(allowed, bearer_token):
            return WorkspacePrincipal(workspace_id=workspace_id)
    raise HTTPException(status_code=403, detail="invalid_token")


def require_workspace_http(request: Request) -> WorkspacePrincipal:
    workspace_id = request.headers.get("X-Chorus-Workspace")
    bearer_token = _extract_bearer_token(request.headers.get("Authorization"))
    return authenticate_workspace(workspace_id, bearer_token)


def require_workspace_websocket(websocket: WebSocket) -> WorkspacePrincipal:
    workspace_id = websocket.query_params.get("workspace_id") or websocket.headers.get("X-Chorus-Workspace")
    bearer_token = _extract_bearer_token(websocket.headers.get("Authorization")) or websocket.query_params.get("token")
    return authenticate_workspace(workspace_id, bearer_token)
