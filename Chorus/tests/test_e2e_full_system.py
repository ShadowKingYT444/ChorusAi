"""
End-to-end: real HTTP agent (uvicorn subprocess) + orchestrator (ASGI in-process).

Default: hash embeddings (fast, no model download). For MiniLM parity with production:
  DISTLM_E2E_MINILM=1 pytest tests/test_e2e_full_system.py -v -m integration

Requires: free localhost ports, `uvicorn` on PATH (same venv as pytest).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import anyio
import httpx
import pytest

if os.getenv("DISTLM_E2E_MINILM") == "1":
    os.environ["ORC_EMBEDDING_BACKEND"] = "minilm"
else:
    os.environ["ORC_EMBEDDING_BACKEND"] = "hash"

from orchestrator.main import app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_tcp(host: str, port: int, *, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"nothing listening on {host}:{port} within {timeout_s}s")


@pytest.fixture(scope="module")
def echo_agent_base_url() -> Iterator[str]:
    port = _free_port()
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    prev_pp = os.environ.get("PYTHONPATH", "")
    # Prepend repo root so local `tests.*` wins over unrelated `tests` packages in site-packages.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([repo_root, prev_pp]) if prev_pp else repo_root}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.fixtures.echo_agent:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_tcp("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read() if proc.stderr else b""
        if proc.returncode not in (0, -15, -9) and err:
            print(err.decode("utf-8", errors="replace"), file=sys.stderr)


@pytest.mark.anyio
@pytest.mark.integration
async def test_full_job_two_slots_two_rounds(echo_agent_base_url: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
        cr = await client.post(
            "/jobs",
            json={
                "context": "You are participating in a distributed reasoning benchmark.",
                "prompt": "Propose a mitigation for cascading failures in a microservice mesh.",
                "agent_count": 2,
                "rounds": 2,
                "payout": 100.0,
                "embedding_model_version": "e2e",
            },
        )
        assert cr.status_code == 200, cr.text
        job_id = cr.json()["job_id"]

        reg = await client.post(
            f"/jobs/{job_id}/agents",
            json={
                "slots": {
                    "slot-a": {"completion_base_url": echo_agent_base_url},
                    "slot-b": {"completion_base_url": echo_agent_base_url},
                }
            },
        )
        assert reg.status_code == 200, reg.text

        deadline = time.monotonic() + 45.0
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            gr = await client.get(f"/jobs/{job_id}")
            assert gr.status_code == 200, gr.text
            last = gr.json()
            if last.get("status") == "completed":
                break
            if last.get("status") == "failed":
                op = await client.get(f"/jobs/{job_id}/operator")
                detail = op.text if op.status_code == 200 else ""
                raise AssertionError(f"job failed: {last!r} operator={detail}")
            await anyio.sleep(0.08)

        assert last.get("status") == "completed", f"timeout or bad state: {last!r}"
        assert last.get("settlement_preview") is not None

        op = await client.get(f"/jobs/{job_id}/operator")
        assert op.status_code == 200
        body = op.json()
        assert body["job_id"] == job_id
        assert len(body["rounds"]) == 2
        for rd in body["rounds"]:
            assert rd["round"] in (1, 2)
            assert "slot-a" in rd["slots"]
            assert "slot-b" in rd["slots"]


@pytest.mark.anyio
@pytest.mark.integration
async def test_full_job_demo_slots_complete() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
        cr = await client.post(
            "/jobs",
            json={
                "context": "You are participating in a distributed reasoning benchmark.",
                "prompt": "What's the most underrated approach to reducing AI hallucinations?",
                "agent_count": 3,
                "rounds": 3,
                "payout": 0.5,
                "embedding_model_version": "demo-e2e",
            },
        )
        assert cr.status_code == 200, cr.text
        job_id = cr.json()["job_id"]

        reg = await client.post(
            f"/jobs/{job_id}/agents",
            json={
                "slots": {
                    "chorus-skeptic-1": {"completion_base_url": "demo://chorus-skeptic-1"},
                    "chorus-optimist-2": {"completion_base_url": "demo://chorus-optimist-2"},
                    "chorus-analyst-3": {"completion_base_url": "demo://chorus-analyst-3"},
                }
            },
        )
        assert reg.status_code == 200, reg.text

        deadline = time.monotonic() + 30.0
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            gr = await client.get(f"/jobs/{job_id}")
            assert gr.status_code == 200, gr.text
            last = gr.json()
            if last.get("status") == "completed":
                break
            if last.get("status") == "failed":
                raise AssertionError(f"demo job failed: {last!r}")
            await anyio.sleep(0.08)

        assert last.get("status") == "completed", f"timeout or bad state: {last!r}"
        assert last.get("final_answer")
        assert last.get("settlement_preview") is not None

        op = await client.get(f"/jobs/{job_id}/operator")
        assert op.status_code == 200
        body = op.json()
        assert len(body["rounds"]) == 3
        for rd in body["rounds"]:
            assert "chorus-skeptic-1" in rd["slots"]
            assert "chorus-optimist-2" in rd["slots"]
            assert "chorus-analyst-3" in rd["slots"]
