#!/usr/bin/env python3
"""
Block until the requested components are confirmed connected.

Usage:
  wait_ready.py [timeout] [--phone] [--hub]

  --phone   wait for phone_connected only
  --hub     wait for hub_connected only
  (neither) wait for both (default)

Exit 0: ready. Exit 1: timeout — prints DEPLOY:ready:error to stdout.
"""

import asyncio
import json
import sys
import time

WS_URL = "ws://localhost:8765/"

args = sys.argv[1:]
want_phone = "--phone" in args or "--phone" not in args and "--hub" not in args
want_hub   = "--hub"   in args or "--phone" not in args and "--hub" not in args
timeout_args = [a for a in args if a.lstrip("-").isdigit()]
TIMEOUT = int(timeout_args[0]) if timeout_args else 30


async def wait() -> None:
    import websockets

    phone_ok = not want_phone
    hub_ok   = not want_hub
    start = time.monotonic()

    async with websockets.connect(WS_URL) as ws:
        while True:
            elapsed = time.monotonic() - start
            remaining = TIMEOUT - elapsed
            if remaining <= 0:
                missing = []
                if want_phone and not phone_ok:
                    missing.append("phone_connected")
                if want_hub and not hub_ok:
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
                if want_phone:
                    phone_ok = msg.get("phone_connected", False)
                if want_hub:
                    hub_ok = msg.get("hub_connected", False)
            elif t == "phone_connected":
                phone_ok = True
            elif t == "hub_connected":
                hub_ok = True

            if phone_ok and hub_ok:
                elapsed = time.monotonic() - start
                waiting = []
                if want_phone:
                    waiting.append("phone_connected=true")
                if want_hub:
                    waiting.append("hub_connected=true")
                print(f"DEPLOY:ready:ok {' '.join(waiting)} elapsed={int(elapsed)}s")
                return


asyncio.run(wait())
