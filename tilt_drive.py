"""
Tilt-to-drive: iPhone pitch controls motor speed.
Tilt top of phone away from you → forward, toward you → reverse.
"""
import asyncio
import json

import websockets

MOTOR_PORT = "A"
MAX_SPEED   = 500   # deg/s at full tilt
DEAD_ZONE   = 0.08  # radians (~5°) — no-op band around flat
SCALE       = MAX_SPEED / 0.4   # 0.4 rad (~23°) of tilt = full speed


def _pitch_to_speed(pitch: float) -> int:
    if abs(pitch) < DEAD_ZONE:
        return 0
    return max(-MAX_SPEED, min(MAX_SPEED, int(pitch * SCALE)))


async def run():
    async with websockets.connect("ws://localhost:8765/") as ws:
        json.loads(await ws.recv())  # hello

        await ws.send(json.dumps({"type": "subscribe", "sensor": "imu"}))
        print(f"Tilt control active — motor {MOTOR_PORT}. Ctrl-C to stop.\n")

        last_speed = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "phone_hardware" or msg.get("sensor") != "imu":
                    continue

                pitch = msg["attitude"]["pitch"]
                speed = _pitch_to_speed(pitch)

                if last_speed is None or abs(speed - last_speed) > 15:
                    cmd = f"motor:{MOTOR_PORT}:stop" if speed == 0 else f"motor:{MOTOR_PORT}:run:{speed}"
                    await ws.send(json.dumps({"target": "hub", "data": cmd}))

                    bar = ("+" if speed > 0 else "-") * (abs(speed) // 50) if speed != 0 else "|"
                    print(f"  pitch={pitch:+.3f} rad  speed={speed:+5d}  {bar}", flush=True)
                    last_speed = speed

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await ws.send(json.dumps({"target": "hub", "data": f"motor:{MOTOR_PORT}:stop"}))
            await ws.send(json.dumps({"type": "unsubscribe", "sensor": "imu"}))
            print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(run())
