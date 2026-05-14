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

This template demonstrates all five. It subscribes to hub:imu, lights the hub
BLUE while running, prints pitch/roll to the console, and cleans up on exit.
"""
import asyncio
import json
import sys
import websockets

WS_URL = "ws://localhost:8765/"


async def main():
    try:
        async with websockets.connect(WS_URL) as ws:

            # ── 1. Read hello and check hardware state ────────────────────────
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert hello.get("type") == "hello", f"unexpected first message: {hello}"

            if not hello.get("hub_connected"):
                print("Hub not connected — exiting")
                return
            if not hello.get("phone_connected"):
                print("Phone not connected — exiting")
                return

            print(f"Connected. hub={hello['hub_connected']} phone={hello['phone_connected']}")

            # ── 2. Register — introduce yourself to the bus ───────────────────
            await ws.send(json.dumps({
                "type": "register",
                "name": "agent_template",
                "description": "reference implementation of the agent contract",
            }))

            # ── 3. Subscribe only to what you need ────────────────────────────
            await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))

            # claim hub light so our cleanup is meaningful
            await ws.send(json.dumps({"target": "hub", "data": "hub:light:on:BLUE"}))
            print("hub light → BLUE (running)")

            try:
                # ── 6. Handle hub_disconnected during runtime ─────────────────
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
                # ── 4. Unsubscribe ────────────────────────────────────────────
                await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))

                # ── 5. Restore hardware state ─────────────────────────────────
                await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))
                print("hub light → off (cleaned up)")

    except OSError as e:
        print(f"Cannot connect: {e}\nIs server.py running?")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


asyncio.run(main())
