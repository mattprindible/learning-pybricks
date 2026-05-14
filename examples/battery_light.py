#!/usr/bin/env python3
"""
battery_light.py — mirror iPhone battery state onto the hub status light.

  charging / full  →  GREEN
  unplugged        →  RED

Connects to the server, waits for phone_hardware:battery events (replayed
immediately on connect if the phone is already attached), and sends the
appropriate hub:light command on each state change.
"""
import asyncio
import json
import sys
import websockets

WS_URL = "ws://localhost:8765/"


async def main():
    try:
        async with websockets.connect(WS_URL) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if not hello.get("hub_connected"):
                print("Hub not connected — exiting")
                return
            print(f"Connected. hub={hello['hub_connected']} phone={hello['phone_connected']}")

            try:
                async for raw in ws:
                    msg = json.loads(raw)

                    if msg.get("type") == "hub_disconnected":
                        print("Hub disconnected — light commands paused")
                        continue

                    if msg.get("type") == "phone_hardware" and msg.get("sensor") == "battery":
                        state = msg.get("state")
                        color = {"charging": "GREEN", "full": "GREEN", "unplugged": "RED"}.get(state)
                        if color:
                            await ws.send(json.dumps({"target": "hub", "data": f"hub:light:on:{color}"}))
                            print(f"battery {state!r} → hub light {color}")
            finally:
                await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))

    except OSError as e:
        print(f"Cannot connect: {e}\nIs server.py running?")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


asyncio.run(main())
