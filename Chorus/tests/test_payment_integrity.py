from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator.db import ChorusDB
from orchestrator.main import app

DEFAULT_WORKSPACE_ID = "local-dev"
DEFAULT_WORKSPACE_TOKEN = "chorus-local-dev-token"


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEFAULT_WORKSPACE_TOKEN}",
        "X-Chorus-Workspace": DEFAULT_WORKSPACE_ID,
    }


@pytest.mark.asyncio
async def test_finalize_payment_refuses_unfunded_quote(tmp_path) -> None:
    db = ChorusDB()
    await db.connect(str(tmp_path / "payments.db"))
    try:
        await db.insert_payment("job-unfunded", quoted_amount_uc=123, status="quoted")

        finalized = await db.finalize_payment(
            "job-unfunded",
            final_amount_uc=99,
            platform_fee_uc=1,
        )

        payment = await db.fetch_payment("job-unfunded")
        assert finalized is False
        assert payment is not None
        assert payment["status"] == "quoted"
        assert payment["funded_at"] is None
        assert payment["settled_at"] is None
        assert payment["final_amount_uc"] is None
    finally:
        await db.close()


def test_paid_job_requires_matching_funded_quote(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CHORUS_DB_PATH", str(tmp_path / "chorus.db"))
    monkeypatch.setenv("ORC_REQUIRE_JOB_PAYMENT", "1")

    with TestClient(app) as client:
        quote = client.post(
            "/jobs/quote",
            headers=_auth_headers(),
            json={"prompt": "review this launch plan", "agent_count": 2, "models": ["default"]},
        )
        assert quote.status_code == 200, quote.text
        quote_body = quote.json()
        payment_job_id = quote_body["job_id"]

        paid_payload = {
            "context": "ctx",
            "prompt": "review this launch plan",
            "agent_count": 2,
            "rounds": 1,
            "payout": quote_body["total_uc"] / 1_000_000,
            "payment_job_id": payment_job_id,
        }

        unfunded = client.post("/jobs", headers=_auth_headers(), json=paid_payload)
        assert unfunded.status_code == 402
        assert unfunded.json()["detail"] == "payment_not_funded"

        db = app.state.db
        client.portal.call(
            lambda: db.mark_payment_funded(
                payment_job_id,
                payer_wallet="payer-wallet",
                tx_deposit="tx-deposit",
            )
        )

        underpaid_payload = {**paid_payload, "payout": 0.000001}
        underpaid = client.post("/jobs", headers=_auth_headers(), json=underpaid_payload)
        assert underpaid.status_code == 400
        assert underpaid.json()["detail"] == "payment_amount_mismatch"

        funded = client.post("/jobs", headers=_auth_headers(), json=paid_payload)
        assert funded.status_code == 200, funded.text
        assert funded.json()["job_id"] == payment_job_id

        reused = client.post("/jobs", headers=_auth_headers(), json=paid_payload)
        assert reused.status_code == 409
        assert reused.json()["detail"] == "job already exists"
