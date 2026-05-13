import asyncio
import json
import websockets

MAX_SPEED = 500
MIN_DIST  = 100
MAX_DIST  = 600


def dist_to_speed(dist):
    if dist >= 2000 or dist >= MAX_DIST:
        return MAX_SPEED
    if dist < MIN_DIST:
        return 0
    return int((dist - MIN_DIST) / (MAX_DIST - MIN_DIST) * MAX_SPEED)


async def send_exec(ws, code):
    await ws.send(json.dumps({"target": "hub", "data": "exec:" + code}))
    results = []
    while True:
        msg = json.loads(await ws.recv())
        data = msg.get("data", "")
        if data.startswith(">exec:"):
            return results
        if data.startswith(">"):
            results.append(data)


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        print("Move your hand toward the sonic sensor to slow motor A.")
        print("Ctrl+C to stop.\n")

        current_speed = None
        try:
            while True:
                out = await send_exec(ws, 'print(">d:" + str(sensors["E"][1].distance()))')
                dist = int(out[0].split(":")[1]) if out else 2000
                speed = dist_to_speed(dist)

                if speed != current_speed:
                    if speed == 0:
                        await send_exec(ws, 'motors["A"].stop()')
                    else:
                        await send_exec(ws, f'motors["A"].run({speed})')
                    current_speed = speed
                    print(f"dist={dist:5d}mm  speed={speed:4d}°/s  ← changed")
                else:
                    print(f"dist={dist:5d}mm  speed={speed:4d}°/s")
        except KeyboardInterrupt:
            pass
        finally:
            try:
                await send_exec(ws, 'motors["A"].stop()')
            except Exception:
                pass
            print("\nStopped.")


asyncio.run(main())
