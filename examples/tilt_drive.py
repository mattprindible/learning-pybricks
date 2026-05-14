"""
Tilt-to-drive: iPhone pitch controls motor speed.
Tilt top of phone away from you → forward, toward you → reverse.
"""
import asyncio
import json
import sys

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
    try:
        async with websockets.connect("ws://localhost:8765/") as ws:

            # ── 1. Check hardware state before acting ─────────────────────────
            hello = json.loads(await ws.recv())
            if not hello.get("hub_connected"):
                print("Hub not connected — exiting")
                return
            if not hello.get("phone_connected"):
                print("Phone not connected — exiting")
                return

            # ── 2. Register ───────────────────────────────────────────────────
            await ws.send(json.dumps({
                "type": "register",
                "name": "tilt_drive",
                "description": f"iPhone tilt → motor {MOTOR_PORT} speed",
            }))

            # ── 3. Subscribe only to what we need ─────────────────────────────
            await ws.send(json.dumps({"type": "subscribe", "sensor": "imu"}))
            print(f"Tilt control active — motor {MOTOR_PORT}. Ctrl-C to stop.\n")

            last_speed = None
            hub_live = True

            try:
                async for raw in ws:
                    msg = json.loads(raw)

                    # ── 5. Handle hub_disconnected during runtime ─────────────
                    if msg.get("type") == "hub_disconnected":
                        hub_live = False
                        print("\nHub disconnected — waiting for reconnect")
                        continue

                    if msg.get("type") == "hub_connected":
                        hub_live = True
                        last_speed = None  # force re-send on resume
                        print("Hub reconnected — resuming")
                        continue

                    if not hub_live:
                        continue

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

            finally:
                # ── 5. Restore hardware state ─────────────────────────────────
                await ws.send(json.dumps({"target": "hub", "data": f"motor:{MOTOR_PORT}:stop"}))
                # ── 4. Unsubscribe ────────────────────────────────────────────
                await ws.send(json.dumps({"type": "unsubscribe", "sensor": "imu"}))
                print("\nStopped.")

    except OSError as e:
        print(f"Cannot connect: {e}\nIs server.py running?")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(run())
