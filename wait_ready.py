#!/usr/bin/env python3
"""
Block until both phone_connected and hub_connected.

Checks the hello message first (server sends current state on connect),
then listens for live events to cover the case where one or both aren't
ready yet. Called by deploy.sh as the final readiness gate.

Exit 0: system ready.
Exit 1: timeout — prints DEPLOY:ready:error reason=... to stdout.
"""

import asyncio
import json
import sys
import time

WS_URL = "ws://localhost:8765/"
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 30


async def wait() -> None:
    import websockets

    phone_ok = False
    hub_ok = False
    start = time.monotonic()

    async with websockets.connect(WS_URL) as ws:
        while True:
            elapsed = time.monotonic() - start
            remaining = TIMEOUT - elapsed
            if remaining <= 0:
                missing = []
                if not phone_ok:
                    missing.append("phone_connected")
                if not hub_ok:
                    missing.append("hub_connected")
                print(f"DEPLOY:ready:error reason=TIMEOUT waiting={','.join(missing)} elapsed={int(elapsed)}s")
                sys.exit(1)

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = msg.get("type")
            if t == "hello":
                phone_ok = msg.get("phone_connected", False)
                hub_ok = msg.get("hub_connected", False)
            elif t == "phone_connected":
                phone_ok = True
            elif t == "hub_connected":
                hub_ok = True

            if phone_ok and hub_ok:
                elapsed = time.monotonic() - start
                print(f"DEPLOY:ready:ok phone_connected=true hub_connected=true elapsed={int(elapsed)}s")
                return


asyncio.run(wait())
