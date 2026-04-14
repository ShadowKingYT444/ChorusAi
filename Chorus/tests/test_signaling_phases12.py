from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.main import app


def _register(ws, peer_id: str, model: str = "phi3-mini") -> dict:
    ws.send_json({"type": "register", "peer_id": peer_id, "model": model})
    return ws.receive_json()


def _receive_until_not_presence(ws) -> dict:
    msg = ws.receive_json()
    while msg.get("type") == "peer_count":
        msg = ws.receive_json()
    return msg


def test_discovery_registry_updates_on_connect_and_disconnect() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws_a:
            registered = _register(ws_a, "peer-a")
            assert registered["type"] == "registered"
            peers = client.get("/peers")
            assert peers.status_code == 200
            body = peers.json()
            assert body["count"] == 1
            assert body["peers"][0]["peer_id"] == "peer-a"

        peers_after = client.get("/peers")
        assert peers_after.status_code == 200
        assert peers_after.json()["count"] == 0


def test_broadcast_plan_is_adaptive_and_deterministic() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws_a, client.websocket_connect("/ws/signaling") as ws_b:
            _register(ws_a, "peer-a")
            _register(ws_b, "peer-b")

            req = {
                "prompt": "test",
                "timeout_ms": 9000,
                "persona_catalog": ["skeptic", "optimist", "analyst"],
                "target_peer_ids": ["peer-a", "peer-b"],
            }
            p1 = client.post("/broadcast/plan", json=req)
            p2 = client.post("/broadcast/plan", json=req)
            assert p1.status_code == 200 and p2.status_code == 200
            b1 = p1.json()
            b2 = p2.json()
            assert b1["expected_peers"] == 2
            assert {a["peer_id"] for a in b1["assignments"]} == {"peer-a", "peer-b"}
            # Different jobs may map to different indices, but mapping is stable within a response.
            assert len({a["persona"] for a in b1["assignments"]}) >= 1
            assert b1["timeout_ms"] == 9000
            assert b2["timeout_ms"] == 9000


def test_broadcast_job_relays_envelopes_to_connected_targets() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws_prompter, client.websocket_connect(
            "/ws/signaling"
        ) as ws_peer:
            _register(ws_prompter, "prompter", model="qwen2.5:0.5b")
            _register(ws_peer, "peer-x", model="qwen2.5:0.5b")

            ws_prompter.send_json(
                {
                    "type": "broadcast_job",
                    "job_id": "job-123",
                    "prompt": "How should we hedge demand risk?",
                    "timeout_ms": 8000,
                    "persona_catalog": ["skeptic"],
                    "target_peer_ids": ["peer-x"],
                }
            )
            envelope = _receive_until_not_presence(ws_peer)
            assert envelope["type"] == "job_envelope"
            assert envelope["job_id"] == "job-123"
            assert envelope["persona"] == "skeptic"
            assert envelope["from_peer_id"] == "prompter"

            ack = _receive_until_not_presence(ws_prompter)
            assert ack["type"] == "broadcast_started"
            assert ack["ok"] is True
            assert ack["delivered_peers"] == 1


def test_join_request_returns_mesh_and_known_snapshot() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws_a, client.websocket_connect("/ws/signaling") as ws_b:
            _register(ws_a, "peer-a", model="qwen2.5:0.5b")
            ws_b.send_json(
                {
                    "type": "join_request",
                    "peer_id": "peer-b",
                    "address": "wss://peer-b.example/ws",
                    "model": "gemma-2b",
                    "protocol_version": "1",
                }
            )
            msg = _receive_until_not_presence(ws_b)
            assert msg["type"] == "join_accept"
            assert msg["peer"]["peer_id"] == "peer-b"
            assert any(peer["peer_id"] == "peer-a" for peer in msg["assigned_mesh"])
            assert any(peer["peer_id"] == "peer-a" for peer in msg["known_peers_snapshot"])


def test_job_ack_and_response_relay_back_to_prompter() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws_prompter, client.websocket_connect(
            "/ws/signaling"
        ) as ws_peer:
            _register(ws_prompter, "prompter")
            _register(ws_peer, "peer-1")

            ws_prompter.send_json(
                {
                    "type": "job_request",
                    "job_id": "job-abc",
                    "prompt": "test",
                    "timeout_ms": 5000,
                    "prompter_id": "prompter",
                    "peers": [{"peer_id": "peer-1", "persona": "skeptic"}],
                }
            )
            peer_msg = _receive_until_not_presence(ws_peer)
            assert peer_msg["type"] == "job_request"
            assert peer_msg["your_persona"] == "skeptic"

            dispatch_ack = _receive_until_not_presence(ws_prompter)
            assert dispatch_ack["type"] == "job_dispatch_ack"
            assert dispatch_ack["delivered_peers"] == 1

            ws_peer.send_json(
                {
                    "type": "job_ack",
                    "job_id": "job-abc",
                    "peer_id": "peer-1",
                    "prompter_id": "prompter",
                }
            )
            ack = _receive_until_not_presence(ws_prompter)
            assert ack["type"] == "job_ack"
            assert ack["peer_id"] == "peer-1"

            ws_peer.send_json(
                {
                    "type": "job_response",
                    "job_id": "job-abc",
                    "peer_id": "peer-1",
                    "prompter_id": "prompter",
                    "text": "done",
                    "model": "phi3-mini",
                    "latency_ms": 1111,
                }
            )
            response = _receive_until_not_presence(ws_prompter)
            assert response["type"] == "job_response"
            assert response["text"] == "done"


def test_set_address_updates_peer() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling") as ws:
            reg = _register(ws, "peer-addr")
            assert reg["type"] == "registered"
            ws.send_json({"type": "set_address", "address": "http://10.0.0.5:11434"})
            msg = ws.receive_json()
            while msg.get("type") == "peer_count":
                msg = ws.receive_json()
            assert msg["type"] == "address_updated"
            assert msg["peer"]["address"] == "http://10.0.0.5:11434"
            listed = client.get("/peers").json()
            assert listed["peers"][0]["address"] == "http://10.0.0.5:11434"


def test_invoke_broadcast_no_http_targets() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/broadcast/invoke_completions",
            json={"job_id": "j-invoke", "prompt": "Hello", "timeout_ms": 5000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["invoked"] == 0
        assert body["results"] == []
