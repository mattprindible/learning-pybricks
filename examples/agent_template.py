#!/usr/bin/env python3
"""
agent_template.py — reference implementation of the agent contract.

Every agent on this platform must:
  1. Read hello and check hardware state before acting
  2. Register — send name + description so the bus knows who you are
  3. Subscribe only to what it needs
  4. Unsubscribe in a finally block
  5. Restore hardware state in a finally block
  6. Handle hub_disconnected events during runtime

Reconnection is built in: if the server restarts or the connection drops,
the agent waits 5s and reconnects automatically. Only Ctrl-C exits cleanly.
"""
import asyncio
import json
import sys

import websockets

WS_URL = "ws://localhost:8765/"
RETRY_DELAY = 5.0


async def session(ws: websockets.ClientConnection) -> None:
    # ── 1. Read hello and check hardware state ────────────────────────────────
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert hello.get("type") == "hello", f"unexpected first message: {hello}"

    if not hello.get("hub_connected"):
        print("Hub not connected — will retry")
        return
    if not hello.get("phone_connected"):
        print("Phone not connected — will retry")
        return

    print(f"Connected. hub={hello['hub_connected']} phone={hello['phone_connected']}")

    # ── 2. Register ───────────────────────────────────────────────────────────
    await ws.send(json.dumps({
        "type": "register",
        "name": "agent_template",
        "description": "reference implementation of the agent contract",
    }))

    # ── 3. Subscribe only to what you need ────────────────────────────────────
    await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
    await ws.send(json.dumps({"target": "hub", "data": "hub:light:on:BLUE"}))
    print("hub light → BLUE (running)")

    try:
        # ── 6. Handle hub_disconnected during runtime ─────────────────────────
        hub_live = True

        async for raw in ws:
            msg = json.loads(raw)

            if msg.get("type") == "hub_disconnected":
                hub_live = False
                print("Hub disconnected — pausing")
                continue

            if msg.get("type") == "hub_connected":
                hub_live = True
                print("Hub reconnected — resuming")
                continue

            if msg.get("type") == "hub_stream" and msg.get("sensor") == "hub:imu":
                if hub_live:
                    pitch = msg.get("pitch", 0.0)
                    roll  = msg.get("roll",  0.0)
                    print(f"  pitch={pitch:+.1f}°  roll={roll:+.1f}°")

    finally:
        # ── 4. Unsubscribe + 5. Restore hardware ─────────────────────────────
        # Wrapped in try/except: if the server is gone, sends fail silently.
        try:
            await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
            await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))
            print("hub light → off (cleaned up)")
        except Exception:
            pass


async def main():
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await session(ws)
        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            print(f"Disconnected ({e}) — retrying in {RETRY_DELAY:.0f}s...")
            await asyncio.sleep(RETRY_DELAY)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    asyncio.run(main())
