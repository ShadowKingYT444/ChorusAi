from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator import main as orch_main
from orchestrator.main import app

AUTH_HEADERS = {
    "X-Chorus-Workspace": "local-dev",
    "Authorization": "Bearer chorus-local-dev-token",
}


def _reset_runtime() -> None:
    orch_main._job_response_buffer.clear()
    orch_main.registry._peers.clear()
    orch_main.registry._known_peers.clear()
    orch_main.job_store._jobs.clear()
    orch_main.job_store._event_log.clear()
    orch_main.job_store._subscribers.clear()
    orch_main.job_store._seq.clear()
    orch_main.job_store._workspace_usage.clear()


def _register(
    ws,
    *,
    peer_id: str,
    model: str,
    address: str | None = None,
    supported_models: list[str] | None = None,
) -> dict:
    payload = {
        "type": "register",
        "peer_id": peer_id,
        "model": model,
    }
    if address:
        payload["address"] = address
    if supported_models:
        payload["supported_models"] = supported_models
    ws.send_json(payload)
    return ws.receive_json()


def test_models_endpoint_and_auto_routes_honor_completion_model(monkeypatch, tmp_path) -> None:
    _reset_runtime()
    monkeypatch.setenv("CHORUS_DB_PATH", str(tmp_path / "chorus.db"))
    monkeypatch.setenv("ORC_ATTACHMENT_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ORC_ANCHOR_COMPLETION_BASE_URLS", "http://anchor-a:11434,http://anchor-b:11434")
    monkeypatch.setenv("ORC_ANCHOR_MODEL_IDS", "llama3.2,phi4-mini")

    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws:
            reg = _register(
                ws,
                peer_id="peer-qwen",
                model="qwen2.5:7b",
                address="http://10.0.0.2:11434",
                supported_models=["qwen2.5:7b", "llama3.2"],
            )
            assert reg["type"] == "registered"

            models = client.get("/models", headers=AUTH_HEADERS)
            assert models.status_code == 200
            model_map = {item["model_id"]: item for item in models.json()["models"]}
            assert "llama3.2" in model_map
            assert model_map["llama3.2"]["route_count"] >= 2
            assert "phi4-mini" in model_map

            created = client.post(
                "/jobs",
                headers=AUTH_HEADERS,
                json={
                    "context": "ctx",
                    "prompt": "Review the launch plan.",
                    "agent_count": 1,
                    "rounds": 1,
                    "payout": 0,
                    "completion_model": "llama3.2",
                },
            )
            assert created.status_code == 200
            job_id = created.json()["job_id"]

            registration = client.post(
                f"/jobs/{job_id}/agents",
                headers=AUTH_HEADERS,
                json={"slots": {}, "routing_mode": "auto"},
            )
            assert registration.status_code == 200

            job = orch_main.job_store._jobs[job_id]
            slot_runtime = next(iter(job.slots.values()))
            assert slot_runtime.registration.model_id == "llama3.2"


def test_upload_attachment_and_create_job_includes_extracted_context(monkeypatch, tmp_path) -> None:
    _reset_runtime()
    monkeypatch.setenv("CHORUS_DB_PATH", str(tmp_path / "chorus.db"))
    monkeypatch.setenv("ORC_ATTACHMENT_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("ORC_ANCHOR_COMPLETION_BASE_URLS", raising=False)
    monkeypatch.delenv("ORC_ANCHOR_MODEL_IDS", raising=False)

    with TestClient(app) as client:
        upload = client.post(
            "/attachments",
            headers=AUTH_HEADERS,
            files=[("files", ("brief.txt", b"Critical launch memo\nShip the pricing fix first.", "text/plain"))],
        )
        assert upload.status_code == 200
        attachment = upload.json()["attachments"][0]
        attachment_id = attachment["attachment_id"]
        assert attachment["kind"] == "text"

        created = client.post(
            "/jobs",
            headers=AUTH_HEADERS,
            json={
                "context": "Base review context",
                "prompt": "What should we do next?",
                "agent_count": 1,
                "rounds": 1,
                "payout": 0,
                "attachment_ids": [attachment_id],
                "completion_model": "qwen2.5:7b",
            },
        )
        assert created.status_code == 200
        job_id = created.json()["job_id"]

        job = orch_main.job_store._jobs[job_id]
        assert job.spec.attachment_ids == [attachment_id]
        assert job.spec.completion_model == "qwen2.5:7b"
        assert "### Attached Materials" in job.spec.context
        assert "Critical launch memo" in job.spec.context

        listed = client.get("/attachments", headers=AUTH_HEADERS)
        assert listed.status_code == 200
        assert any(item["attachment_id"] == attachment_id for item in listed.json()["attachments"])

        meta = client.get(f"/attachments/{attachment_id}", headers=AUTH_HEADERS)
        assert meta.status_code == 200
        assert meta.json()["filename"] == "brief.txt"

        content = client.get(f"/attachments/{attachment_id}/content", headers=AUTH_HEADERS)
        assert content.status_code == 200
        assert b"Critical launch memo" in content.content
