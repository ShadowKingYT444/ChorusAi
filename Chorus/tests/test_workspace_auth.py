from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orchestrator.main import app

DEFAULT_WORKSPACE_ID = "local-dev"
DEFAULT_WORKSPACE_TOKEN = "chorus-local-dev-token"


def _auth_headers(
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    token: str = DEFAULT_WORKSPACE_TOKEN,
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Chorus-Workspace": workspace_id,
    }


def _wait_for_job(
    client: TestClient,
    job_id: str,
    *,
    headers: dict[str, str],
    timeout_s: float = 10.0,
) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") in {"completed", "failed"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last!r}")


def test_jobs_require_workspace_auth_headers() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/jobs",
            json={
                "context": "ctx",
                "prompt": "prompt",
                "agent_count": 1,
                "rounds": 1,
                "payout": 0.0,
            },
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "missing_workspace"


def test_self_service_workspace_session_can_create_scoped_jobs() -> None:
    with TestClient(app) as client:
        session = client.post("/workspaces/session", json={"workspace_id": "workspace-browser-user"})
        assert session.status_code == 200, session.text
        workspace = session.json()
        assert workspace["workspace_id"] == "workspace-browser-user"
        assert workspace["workspace_token"].startswith("cw1.")

        create = client.post(
            "/jobs",
            headers=_auth_headers(workspace["workspace_id"], workspace["workspace_token"]),
            json={
                "context": "ctx",
                "prompt": "prompt",
                "agent_count": 1,
                "rounds": 1,
                "payout": 0.0,
            },
        )
        assert create.status_code == 200, create.text
        assert create.json()["workspace_id"] == "workspace-browser-user"

        wrong_workspace = client.post(
            "/jobs",
            headers=_auth_headers("other-workspace", workspace["workspace_token"]),
            json={
                "context": "ctx",
                "prompt": "prompt",
                "agent_count": 1,
                "rounds": 1,
                "payout": 0.0,
            },
        )
        assert wrong_workspace.status_code == 403


def test_job_reads_are_scoped_to_the_authenticated_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORC_WORKSPACE_TOKENS", "workspace-a=token-a,workspace-b=token-b")
    monkeypatch.setenv("ORC_ALLOW_BOOTSTRAP_WORKSPACE", "0")

    with TestClient(app) as client:
        create = client.post(
            "/jobs",
            headers=_auth_headers("workspace-a", "token-a"),
            json={
                "context": "ctx",
                "prompt": "prompt",
                "agent_count": 1,
                "rounds": 2,
                "payout": 0.0,
            },
        )
        assert create.status_code == 200, create.text
        body = create.json()
        assert body["workspace_id"] == "workspace-a"
        assert body["shadow_credit_cost"] == 2

        job_id = body["job_id"]
        wrong_workspace = client.get(
            f"/jobs/{job_id}",
            headers=_auth_headers("workspace-b", "token-b"),
        )
        assert wrong_workspace.status_code == 404

        right_workspace = client.get(
            f"/jobs/{job_id}",
            headers=_auth_headers("workspace-a", "token-a"),
        )
        assert right_workspace.status_code == 200
        assert right_workspace.json()["workspace_id"] == "workspace-a"


def test_register_agents_defaults_to_auto_routing_and_records_shadow_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORC_ANCHOR_COMPLETION_BASE_URLS", "demo://anchor-one")

    with TestClient(app) as client:
        create = client.post(
            "/jobs",
            headers=_auth_headers(),
            json={
                "context": "ctx",
                "prompt": "Describe a safer rollout plan.",
                "agent_count": 1,
                "rounds": 2,
                "payout": 0.0,
            },
        )
        assert create.status_code == 200, create.text
        job_id = create.json()["job_id"]

        register = client.post(
            f"/jobs/{job_id}/agents",
            headers=_auth_headers(),
            json={},
        )
        assert register.status_code == 200, register.text
        assert register.json()["registered_slots"] == ["anchor-1"]

        final_state = _wait_for_job(client, job_id, headers=_auth_headers())
        assert final_state["status"] == "completed"
        assert final_state["routing_mode"] == "auto"
        assert final_state["shadow_credit_cost"] == 2
        assert final_state["settlement_preview"]["shadow_credits"] == 2


def test_ws_job_stream_requires_auth_and_accepts_query_param_tokens() -> None:
    with TestClient(app) as client:
        create = client.post(
            "/jobs",
            headers=_auth_headers(),
            json={
                "context": "ctx",
                "prompt": "Explain how to stage a migration.",
                "agent_count": 1,
                "rounds": 1,
                "payout": 0.0,
            },
        )
        assert create.status_code == 200, create.text
        job_id = create.json()["job_id"]

        register = client.post(
            f"/jobs/{job_id}/agents",
            headers=_auth_headers(),
            json={"slots": {"slot-a": {"completion_base_url": "demo://slot-a"}}},
        )
        assert register.status_code == 200, register.text

        _wait_for_job(client, job_id, headers=_auth_headers())

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/jobs/{job_id}") as websocket:
                websocket.receive_json()

        with client.websocket_connect(
            f"/ws/jobs/{job_id}?workspace_id={DEFAULT_WORKSPACE_ID}&token={DEFAULT_WORKSPACE_TOKEN}"
        ) as websocket:
            event = websocket.receive_json()
            assert event["type"] in {"round_started", "agent_line", "final_answer", "job_done", "edge"}
