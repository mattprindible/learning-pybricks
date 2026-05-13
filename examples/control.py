import asyncio
import json
import websockets

MAX_SPEED =  500   # deg/s at full range
MIN_DIST  =  100   # mm — stop below this
MAX_DIST  =  600   # mm — full speed beyond this


def dist_to_speed(dist):
    if dist >= 2000 or dist >= MAX_DIST:
        return MAX_SPEED
    if dist < MIN_DIST:
        return 0
    return int((dist - MIN_DIST) / (MAX_DIST - MIN_DIST) * MAX_SPEED)


async def send_cmd(ws, cmd):
    await ws.send(json.dumps({"target": "hub", "data": cmd}))
    while True:
        msg = json.loads(await ws.recv())
        data = msg.get("data", "")
        if data.startswith(">"):
            return data


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        print("Move your hand toward the sonic sensor to slow motor A.")
        print("Ctrl+C to stop.\n")

        current_speed = None
        try:
            while True:
                dist_resp = await send_cmd(ws, "sensor:E:distance")
                dist = int(dist_resp.split("=")[1])
                speed = dist_to_speed(dist)

                if speed != current_speed:
                    if speed == 0:
                        await send_cmd(ws, "motor:A:stop")
                    else:
                        await send_cmd(ws, f"motor:A:run:{speed}")
                    current_speed = speed
                    print(f"dist={dist:5d}mm  speed={speed:4d}°/s  ← changed")
                else:
                    print(f"dist={dist:5d}mm  speed={speed:4d}°/s")
        except KeyboardInterrupt:
            pass
        finally:
            try:
                await send_cmd(ws, "motor:A:stop")
            except Exception:
                pass
            print("\nStopped.")


asyncio.run(main())
