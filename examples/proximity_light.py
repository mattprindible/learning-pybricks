#!/usr/bin/env python3
"""
proximity_light.py — camera proximity indicator on the hub status light.

Uses JPEG frame size as a proximity signal: objects close to the camera
lens produce smaller, less complex frames. Maps to a 3-state hub light:

  clear (>25KB)       →  GREEN
  approaching (>10KB) →  YELLOW
  close (<10KB)       →  RED

Discovered empirically: waving at arm's length is invisible; objects
within ~15-20cm of the lens produce reliable size drops. Full coverage
bottoms at ~8KB (skin texture at close range), not near-zero.
"""
import asyncio
import json
import base64

import websockets

WS_URL = "ws://localhost:8765/"
RETRY_DELAY = 5.0
WARMUP_FRAMES = 5  # skip while auto-exposure settles

THRESHOLDS = [
    (25_000, "GREEN",  "clear"),
    (10_000, "YELLOW", "approaching"),
    (0,      "RED",    "close"),
]


def classify(size: int) -> tuple[str, str]:
    for threshold, color, label in THRESHOLDS:
        if size >= threshold:
            return color, label
    return "RED", "close"


async def session(ws: websockets.ClientConnection) -> None:
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    if not hello.get("hub_connected"):
        print("Hub not connected — will retry")
        return

    await ws.send(json.dumps({
        "type": "register",
        "name": "proximity_light",
        "description": "camera JPEG size → hub light proximity indicator",
    }))
    await ws.send(json.dumps({"type": "subscribe", "sensor": "camera"}))
    print("Running — move hand toward/away from camera lens. Ctrl-C to stop.\n")
    print(f"  {'size':>8}   state")
    print(f"  {'-'*28}")

    current_color = None
    frame_count = 0

    try:
        async for raw in ws:
            msg = json.loads(raw)

            if msg.get("type") == "hub_disconnected":
                print("Hub disconnected — pausing")
                current_color = None
                continue

            if msg.get("type") == "hub_connected":
                print("Hub reconnected")
                continue

            if msg.get("sensor") != "camera":
                continue

            frame_count += 1
            if frame_count <= WARMUP_FRAMES:
                continue

            size = len(base64.b64decode(msg["frame"]))
            color, label = classify(size)

            if color != current_color:
                await ws.send(json.dumps({"target": "hub", "data": f"hub:light:on:{color}"}))
                print(f"  {size:>8}   {label}  [{color}]")
                current_color = color

    finally:
        try:
            await ws.send(json.dumps({"type": "unsubscribe", "sensor": "camera"}))
            await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))
        except Exception:
            pass


async def main() -> None:
    try:
        while True:
            try:
                async with websockets.connect(WS_URL) as ws:
                    await session(ws)
            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                print(f"Disconnected ({e}) — retrying in {RETRY_DELAY:.0f}s...")
                await asyncio.sleep(RETRY_DELAY)
    except KeyboardInterrupt:
        pass


asyncio.run(main())
