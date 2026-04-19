#!/usr/bin/env python3
"""
Run a full Chorus-style job locally: echo agent (subprocess) + orchestrator (in-process).

Usage (from repo root):

  python scripts/run_chorus_sim.py \\
    --context "Your task background here." \\
    --prompt "What you want every agent to do." \\
    --agents 3 \\
    --rounds 2 \\
    --payout 100

Options:
  --embedding hash|minilm   default hash (fast). minilm matches production kNN/watchdog.
  --temperature T           override ORC_TEMPERATURE for outbound chat completions (default 0.7).
  --json                    print final operator payload as JSON only.

Requires: same venv as the project (fastapi, httpx, uvicorn, orchestrator deps).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import httpx

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_tcp(host: str, port: int, *, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.05)
    raise SystemExit(f"timeout: nothing listening on {host}:{port}")


def _start_echo_agent(port: int, *, echo_mode: str | None) -> subprocess.Popen:
    prev_pp = os.environ.get("PYTHONPATH", "")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), prev_pp]) if prev_pp else str(ROOT),
    }
    if echo_mode:
        env["ECHO_AGENT_MODE"] = echo_mode
    return subprocess.Popen(
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
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _print_human(op: dict[str, Any]) -> None:
    print()
    print("=" * 72)
    print("CHORUS SIM - operator view")
    print("=" * 72)
    print(f"job_id:   {op.get('job_id')}")
    print(f"status:   {op.get('status')}")
    if op.get("error"):
        print(f"error:    {op['error']}")
    print()

    for rd in op.get("rounds") or []:
        rnum = rd.get("round")
        print("-" * 72)
        print(f"ROUND {rnum}")
        print("-" * 72)
        print(f"  nearest_edges:  {rd.get('nearest_edges') or []}")
        print(f"  furthest_edges: {rd.get('furthest_edges') or []}")
        slots = rd.get("slots") or {}
        for sid in sorted(slots.keys()):
            s = slots[sid]
            ps = s.get("prune_status", "?")
            notes = s.get("watchdog_notes") or []
            err = s.get("error")
            comp = s.get("completion") or ""
            snippet = (comp[:320] + "…") if len(comp) > 320 else comp
            print(f"\n  [{sid}]  prune={ps}")
            if notes:
                print(f"          watchdog: {notes}")
            if err:
                print(f"          invoke_error: {err}")
            print(f"          completion ({len(comp)} chars):")
            for line in snippet.splitlines()[:12]:
                print(f"            {line}")
            if len(snippet.splitlines()) > 12 or len(comp) > len(snippet):
                print("            …")
            print(f"          impact_c={s.get('impact_c', 0)!r}  impact_f={s.get('impact_f', 0)!r}")

    print()
    print("-" * 72)
    print("SETTLEMENT PREVIEW")
    print("-" * 72)
    sp = op.get("settlement_preview")
    if sp is None:
        print("  (none - job may not have finished settlement)")
    else:
        print(json.dumps(sp, indent=2))
    print()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    os.environ["ORC_EMBEDDING_BACKEND"] = args.embedding
    if args.temperature is not None:
        os.environ["ORC_TEMPERATURE"] = str(args.temperature)
    sys.path.insert(0, str(ROOT))
    from orchestrator.main import app

    echo_mode: str | None = None
    if args.demo_prune:
        echo_mode = "short"
    elif args.demo_refusal:
        echo_mode = "refusal"

    port = _free_port()
    proc = _start_echo_agent(port, echo_mode=echo_mode)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_tcp("127.0.0.1", port)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://chorus.test", timeout=60.0
        ) as client:
            cr = await client.post(
                "/jobs",
                json={
                    "context": args.context,
                    "prompt": args.prompt,
                    "agent_count": args.agents,
                    "rounds": args.rounds,
                    "payout": float(args.payout),
                    "embedding_model_version": f"sim-{args.embedding}",
                },
            )
            cr.raise_for_status()
            job_id = cr.json()["job_id"]
            if not args.json:
                print(f"Created job_id={job_id}")
                print(f"Echo agent: {base}  (all slots use this fleet URL)")

            slots_payload = {
                f"slot-{i}": {"completion_base_url": base} for i in range(args.agents)
            }
            reg = await client.post(f"/jobs/{job_id}/agents", json={"slots": slots_payload})
            reg.raise_for_status()

            deadline = time.monotonic() + float(args.timeout)
            last: dict[str, Any] = {}
            while time.monotonic() < deadline:
                gr = await client.get(f"/jobs/{job_id}")
                gr.raise_for_status()
                last = gr.json()
                st = last.get("status")
                if st == "completed":
                    break
                if st == "failed":
                    op = await client.get(f"/jobs/{job_id}/operator")
                    extra = op.json() if op.status_code == 200 else {}
                    raise SystemExit(f"job failed: {last!r}\noperator dump: {json.dumps(extra, indent=2)}")
                await anyio.sleep(0.08)

            if last.get("status") != "completed":
                raise SystemExit(f"timeout after {args.timeout}s; last={last!r}")

            op = await client.get(f"/jobs/{job_id}/operator")
            op.raise_for_status()
            body: dict[str, Any] = op.json()
            if args.json:
                print(json.dumps(body, indent=2))
            else:
                _print_human(body)
            return body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    p = argparse.ArgumentParser(description="Run a local Chorus orchestrator + echo-agent simulation.")
    p.add_argument("--context", "-C", required=True, help="Job context (embedded each round).")
    p.add_argument("--prompt", "-P", required=True, help="Task prompt (### Prompt).")
    p.add_argument("--agents", "-n", type=int, required=True, help="Number of agent slots.")
    p.add_argument("--rounds", "-r", type=int, default=2, help="Number of rounds (default 2).")
    p.add_argument("--payout", type=float, default=100.0, help="Numeric payout pool (default 100).")
    p.add_argument(
        "--embedding",
        choices=("hash", "minilm"),
        default="hash",
        help="Embedding backend for kNN + watchdog (default hash = fast).",
    )
    p.add_argument("--timeout", type=float, default=90.0, help="Max seconds to wait for job completion.")
    p.add_argument(
        "--temperature",
        type=float,
        default=None,
        metavar="T",
        help="Sampling temperature sent to agents (sets ORC_TEMPERATURE for this process; invoker default is 0.7).",
    )
    p.add_argument("--json", action="store_true", help="Print operator JSON only.")
    demo = p.add_mutually_exclusive_group()
    demo.add_argument(
        "--demo-prune",
        action="store_true",
        help="Echo agent returns too-short text → watchdog suspect/streak → prune (see notes per slot).",
    )
    demo.add_argument(
        "--demo-refusal",
        action="store_true",
        help="Echo agent returns a canned refusal phrase → possible_refusal / streak behavior.",
    )
    args = p.parse_args()
    if args.agents < 1 or args.rounds < 1:
        p.error("--agents and --rounds must be >= 1")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
