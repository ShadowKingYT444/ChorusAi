from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator import main as orch_main
from orchestrator.main import app


def _register(ws, peer_id: str, model: str = "phi3-mini") -> dict:
    ws.send_json({"type": "register", "peer_id": peer_id, "model": model})
    return ws.receive_json()


def _clear_buffer() -> None:
    orch_main._job_response_buffer.clear()


def test_clusters_empty_registry() -> None:
    _clear_buffer()
    with TestClient(app) as client:
        r = client.get("/clusters")
        assert r.status_code == 200
        body = r.json()
        assert body["clusters"] == []
        assert body["edges"] == []
        assert body["stats"] == {
            "total_peers": 0,
            "total_clusters": 0,
            "total_edges": 0,
            "total_jobs_observed": 0,
        }


def test_clusters_group_by_model() -> None:
    _clear_buffer()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_a, \
             client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_b, \
             client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_c:
            _register(ws_a, "peer-a", model="llama3.1")
            _register(ws_b, "peer-b", model="llama3.1")
            _register(ws_c, "peer-c", model="phi3-mini")

            r = client.get("/clusters")
            assert r.status_code == 200
            body = r.json()
            clusters = body["clusters"]
            assert len(clusters) == 2

            # Sorted by size desc, then label asc.
            assert clusters[0]["id"] == "model:llama3.1"
            assert clusters[0]["label"] == "llama3.1"
            assert clusters[0]["kind"] == "model"
            assert clusters[0]["size"] == 2
            assert clusters[0]["peer_ids"] == ["peer-a", "peer-b"]

            assert clusters[1]["id"] == "model:phi3-mini"
            assert clusters[1]["label"] == "phi3-mini"
            assert clusters[1]["size"] == 1
            assert clusters[1]["peer_ids"] == ["peer-c"]

            assert body["stats"]["total_peers"] == 3
            assert body["stats"]["total_clusters"] == 2
            assert body["stats"]["total_edges"] == 0
            assert body["stats"]["total_jobs_observed"] == 0


def test_clusters_edges_from_injected_jobs() -> None:
    _clear_buffer()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_a, \
             client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_b, \
             client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_c:
            _register(ws_a, "peer-a", model="llama3.1")
            _register(ws_b, "peer-b", model="llama3.1")
            _register(ws_c, "peer-c", model="phi3-mini")

            # Inject fake jobs into the buffer with overlapping peers.
            orch_main._job_response_buffer["job-1"] = [
                {"peer_id": "peer-a", "text": "x"},
                {"peer_id": "peer-b", "text": "y"},
                {"peer_id": "peer-c", "text": "z"},
            ]
            orch_main._job_response_buffer["job-2"] = [
                {"peer_id": "peer-a", "text": "x"},
                {"peer_id": "peer-b", "text": "y"},
                # Unknown peer should be dropped.
                {"peer_id": "peer-ghost", "text": "q"},
            ]

            r = client.get("/clusters")
            assert r.status_code == 200
            body = r.json()

            edges = body["edges"]
            # Pairs: (a,b) appears in both jobs => weight 2
            # (a,c), (b,c) appear in job-1 only => weight 1 each
            # peer-ghost edges dropped.
            edge_map = {(e["source"], e["target"]): e for e in edges}
            assert (("peer-a", "peer-b") in edge_map)
            assert edge_map[("peer-a", "peer-b")]["weight"] == 2.0
            assert edge_map[("peer-a", "peer-b")]["kind"] == "co_job"
            assert edge_map[("peer-a", "peer-c")]["weight"] == 1.0
            assert edge_map[("peer-b", "peer-c")]["weight"] == 1.0
            assert not any("peer-ghost" in (e["source"], e["target"]) for e in edges)
            # Sorted by (source, target).
            sources_targets = [(e["source"], e["target"]) for e in edges]
            assert sources_targets == sorted(sources_targets)

            assert body["stats"]["total_peers"] == 3
            assert body["stats"]["total_clusters"] == 2
            assert body["stats"]["total_edges"] == len(edges)
            assert body["stats"]["total_jobs_observed"] == 2

    _clear_buffer()


def test_clusters_response_shape_matches_contract() -> None:
    _clear_buffer()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/signaling?workspace_id=local-dev&token=chorus-local-dev-token") as ws_a:
            _register(ws_a, "peer-a", model="llama3.1")
            r = client.get("/clusters")
            assert r.status_code == 200
            body = r.json()
            assert set(body.keys()) == {"clusters", "edges", "stats"}
            assert set(body["stats"].keys()) == {
                "total_peers",
                "total_clusters",
                "total_edges",
                "total_jobs_observed",
            }
            assert isinstance(body["clusters"], list)
            assert isinstance(body["edges"], list)
            cluster = body["clusters"][0]
            assert set(cluster.keys()) == {"id", "label", "kind", "peer_ids", "size"}
