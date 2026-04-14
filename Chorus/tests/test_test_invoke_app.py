"""Smoke tests for agent_backend.test_invoke (mock upstream HTTP)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class _FakeUpstreamResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"choices":[{"message":{"content":"mocked-reply"}}]}'

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "mocked-reply"}}]}


class _FakeAsyncClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> _FakeUpstreamResponse:
        return _FakeUpstreamResponse()


@pytest.fixture
def client() -> TestClient:
    from agent_backend.test_invoke import app as app_module

    return TestClient(app_module.app)


def test_build_payload_shape() -> None:
    from agent_backend.agent_invoke import _build_openai_payload, _build_user_message

    u = _build_user_message(context="C", prompt="Q", data=None)
    assert "### Context\nC" in u
    assert "### Prompt\nQ" in u
    assert "### Data" not in u

    u2 = _build_user_message(context="C", prompt="Q", data='{"x":1}')
    assert "### Data" in u2

    req = _build_openai_payload(
        persona="P",
        context="C",
        prompt="Q",
        data=None,
        policy="pol",
        model="m",
        max_tokens=10,
        temperature=0.5,
        job_id="test-job",
        slot_id="test-slot",
    )
    assert req["messages"][0]["role"] == "system"
    assert req["messages"][1]["role"] == "user"
    assert req["user"] == "test-job:test-slot"


@patch("agent_backend.agent_invoke.httpx.AsyncClient", _FakeAsyncClient)
def test_invoke_returns_content(client: TestClient) -> None:
    r = client.post(
        "/invoke",
        json={
            "persona": "You are terse.",
            "context": "ctx",
            "prompt": "Say hi.",
            "max_tokens": 32,
            "temperature": 0.1,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["content"] == "mocked-reply"
    assert data["raw"]["choices"][0]["message"]["content"] == "mocked-reply"


def test_index_ok(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Chorus invoke tester" in r.text
    assert "/chat" in r.text


def test_chat_page_ok(client: TestClient) -> None:
    r = client.get("/chat")
    assert r.status_code == 200
    assert "Chorus tester" in r.text
    assert "/invoke" in r.text
    assert "sendMessage" in r.text
