import asyncio
import json
import websockets

COMMANDS = [
    "sensor:E:distance",
    "sensor:F:color",
    "motor:A:run:200:500",
    "motor:B:run:200:500",
    "motor:C:run:200:500",
    "motor:D:run:200:500",
    "hub:system:info",
    "hub:ble:version",
    "hub:imu:ready",
    "hub:imu:tilt",
    "hub:imu:heading",
    "hub:imu:acceleration",
    "hub:imu:angular_velocity",
    "hub:imu:up",
    "hub:imu:stationary",
    "hub:battery:voltage",
    "hub:battery:current",
    "hub:buttons:pressed",
    "hub:display:number:42",
    "hub:display:char:A",
    "hub:display:text:hello",
    "hub:display:off",
    "hub:speaker:beep:440:500",
    "hub:light:on:BLUE",
    "hub:light:on:GREEN",
]


async def send_and_receive(ws, cmd):
    await ws.send(json.dumps({"target": "hub", "data": cmd}))
    while True:
        msg = json.loads(await ws.recv())
        data = msg.get("data", "")
        if data.startswith(">"):
            return data


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        for cmd in COMMANDS:
            print(f"→ {cmd}")
            response = await send_and_receive(ws, cmd)
            print(f"← {response}\n")


asyncio.run(main())
