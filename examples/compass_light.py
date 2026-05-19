#!/usr/bin/env python3
"""
compass_light.py — phone compass heading → hub status light color

Demonstrates the phone sensor manifest and heading/altimeter sensors.
Reads the manifest on connect to verify hardware before subscribing —
the right pattern for any agent that needs optional phone capabilities.

Rotate the phone to watch the hub light change:
  N → RED    NE → ORANGE   E → YELLOW   SE → GREEN
  S → CYAN   SW → BLUE     W → VIOLET   NW → MAGENTA

Prefers true heading (requires GPS lock); falls back to magnetic heading.
Prints altimeter readings to the terminal if a barometer is present.
"""
import asyncio
import json

import websockets

WS_URL = "ws://localhost:8765/"
RETRY_DELAY = 5.0

_COMPASS_COLORS = [
    (22.5,  "RED"),
    (67.5,  "ORANGE"),
    (112.5, "YELLOW"),
    (157.5, "GREEN"),
    (202.5, "CYAN"),
    (247.5, "BLUE"),
    (292.5, "VIOLET"),
    (337.5, "MAGENTA"),
]

def heading_to_color(degrees: float) -> str:
    d = degrees % 360
    for threshold, color in _COMPASS_COLORS:
        if d < threshold:
            return color
    return "RED"


async def session(ws: websockets.ClientConnection) -> None:
    # 1. Hello
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert hello.get("type") == "hello", f"unexpected first message: {hello}"

    if not hello.get("hub_connected"):
        print("Hub not connected — will retry")
        return
    if not hello.get("phone_connected"):
        print("Phone not connected — will retry")
        return

    # 2. Read manifest — check what this device actually has before subscribing
    manifest = None
    deadline = asyncio.get_event_loop().time() + 5
    while manifest is None:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            print("No phone manifest — is the iOS app redeployed?")
            return
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if msg.get("type") == "phone_connected":
            manifest = msg

    hw = manifest.get("hardware", {})
    has_compass   = hw.get("compass", False)
    has_barometer = hw.get("barometer", False)
    print(f"Device: {manifest.get('device')}  {manifest.get('os')}")
    print(f"  compass={has_compass}  barometer={has_barometer}")
    print(f"  cameras={hw.get('cameras', [])}")
    print(f"  vision={manifest.get('vision_capabilities', [])}")
    print(f"  permissions={manifest.get('permissions', {})}")

    if not has_compass:
        print("No compass on this device — exiting")
        return

    # 3. Register
    await ws.send(json.dumps({
        "type": "register",
        "name": "compass_light",
        "description": "phone compass heading → hub status light color",
    }))

    # 4. Subscribe to what's available
    await ws.send(json.dumps({"type": "subscribe", "sensor": "heading"}))
    if has_barometer:
        await ws.send(json.dumps({"type": "subscribe", "sensor": "altimeter"}))
        print("Subscribed: heading + altimeter")
    else:
        print("Subscribed: heading")
    print("Rotate the phone — hub light follows compass heading. Ctrl-C to exit.\n")

    hub_live = True
    current_color = None  # only send when color sector changes
    try:
        async for raw in ws:
            msg = json.loads(raw)

            if msg.get("type") == "hub_disconnected":
                hub_live = False
                print("Hub disconnected — pausing")
                continue

            if msg.get("type") == "hub_connected":
                hub_live = True
                current_color = None  # hub restarted, re-apply on next update
                print("Hub reconnected — resuming")
                continue

            if msg.get("type") != "phone_hardware":
                continue

            sensor = msg.get("sensor")

            if sensor == "heading" and hub_live:
                true_h = msg.get("true_heading", -1)
                mag_h  = msg.get("magnetic_heading", 0.0)
                heading = true_h if true_h >= 0 else mag_h
                label   = "true" if true_h >= 0 else "magnetic"
                accuracy = msg.get("accuracy", -1)
                color = heading_to_color(heading)
                print(f"  {heading:6.1f}° {label}  ±{accuracy:.1f}°  → {color}")
                if color != current_color:
                    await ws.send(json.dumps({"target": "hub", "data": f"hub:light:on:{color}"}))
                    current_color = color

            elif sensor == "altimeter":
                altitude = msg.get("altitude", 0.0)
                pressure = msg.get("pressure", 0.0)
                print(f"  alt={altitude:+.2f}m  pressure={pressure:.3f}kPa")

    finally:
        # 5. Unsubscribe + restore hardware
        try:
            await ws.send(json.dumps({"type": "unsubscribe", "sensor": "heading"}))
            if has_barometer:
                await ws.send(json.dumps({"type": "unsubscribe", "sensor": "altimeter"}))
            await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))
            print("\nCleaned up — hub light off")
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
