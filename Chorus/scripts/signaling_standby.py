"""Keep a peer registered on DistLM signaling (LAN demos on PC2 / PC3).

Requires: pip install websockets
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _run(url: str, peer_id: str, model: str) -> None:
    try:
        import websockets
    except ImportError:
        print("Missing dependency. Run: pip install websockets", file=sys.stderr)
        raise SystemExit(1) from None

    async with websockets.connect(url) as ws:
        await ws.send(
            json.dumps({"type": "register", "peer_id": peer_id, "model": model})
        )
        first = json.loads(await ws.recv())
        print(first, file=sys.stderr)

        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "peer_count":
                continue
            print(json.dumps(msg, indent=2)[:4000])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--signal-url",
        required=True,
        help="Full WebSocket URL, e.g. ws://192.168.1.10:8000/ws/signaling",
    )
    p.add_argument("--peer-id", required=True, help="Stable id, unique per machine")
    p.add_argument("--model", default="echo-agent", help="Label shown in /peers")
    args = p.parse_args()
    asyncio.run(_run(args.signal_url, args.peer_id, args.model))


if __name__ == "__main__":
    main()
