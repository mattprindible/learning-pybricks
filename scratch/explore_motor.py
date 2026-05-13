import asyncio
import json
import websockets


async def send_exec(ws, code):
    await ws.send(json.dumps({"target": "hub", "data": "exec:" + code}))
    results = []
    while True:
        msg = json.loads(await ws.recv())
        data = msg.get("data", "")
        if data.startswith(">exec:"):
            return results, data
        if data.startswith(">"):
            results.append(data)


async def x(ws, label, code):
    print(f"\n[{label}]")
    print(f"  >> {code}")
    results, status = await send_exec(ws, code)
    for r in results:
        print(f"     {r}")
    print(f"     {status}")
    return results, status


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        print("Connected. Motor A will move — clear some space.\n")

        # --- reset_angle ---
        await x(ws, "reset_angle(0)", "motors['A'].reset_angle(0)")
        await x(ws, "angle() after reset — expect 0", "print('>angle:' + str(motors['A'].angle()))")

        # --- run_angle, blocking ---
        await x(ws, "run_angle(500, 90) blocking — expect ~90°",
                "motors['A'].reset_angle(0); motors['A'].run_angle(500, 90); print('>angle:' + str(motors['A'].angle()))")

        await x(ws, "run_angle(500, -90) blocking — expect ~-90°",
                "motors['A'].reset_angle(0); motors['A'].run_angle(500, -90); print('>angle:' + str(motors['A'].angle()))")

        await x(ws, "run_angle(500, 360) — full rotation",
                "motors['A'].reset_angle(0); motors['A'].run_angle(500, 360); print('>angle:' + str(motors['A'].angle()))")

        # --- run_angle, non-blocking ---
        await x(ws, "run_angle(500, 180, wait=False) — non-blocking, read angle immediately",
                "motors['A'].reset_angle(0); motors['A'].run_angle(500, 180, wait=False); print('>angle_mid:' + str(motors['A'].angle()))")
        await asyncio.sleep(1.0)
        await x(ws, "angle() after 1s — motor should have finished",
                "print('>angle_final:' + str(motors['A'].angle()))")

        # --- run_target ---
        await x(ws, "reset_angle(0), run_target(500, 180) — go to 180°",
                "motors['A'].reset_angle(0); motors['A'].run_target(500, 180); print('>angle:' + str(motors['A'].angle()))")

        await x(ws, "run_target(500, 0) — return to 0°",
                "motors['A'].run_target(500, 0); print('>angle:' + str(motors['A'].angle()))")

        await x(ws, "run_target(500, 180) again — idempotent from same position?",
                "motors['A'].run_target(500, 180); print('>angle:' + str(motors['A'].angle()))")

        await x(ws, "run_target(500, 180) again — already there, does it block?",
                "motors['A'].run_target(500, 180); print('>angle:' + str(motors['A'].angle()))")

        # --- speed() ---
        await x(ws, "speed() while stopped — expect ~0",
                "print('>speed:' + str(motors['A'].speed()))")

        await x(ws, "run(300), then speed() immediately",
                "motors['A'].run(300); print('>speed_immediate:' + str(motors['A'].speed()))")
        await asyncio.sleep(0.4)
        await x(ws, "speed() after 400ms — expect ~300",
                "print('>speed_settled:' + str(motors['A'].speed()))")
        await x(ws, "stop()", "motors['A'].stop()")

        # --- stalled() ---
        await x(ws, "stalled() while stopped",
                "print('>stalled:' + str(motors['A'].stalled()))")

        await x(ws, "run(300), stalled() immediately",
                "motors['A'].run(300); print('>stalled_running:' + str(motors['A'].stalled()))")
        await asyncio.sleep(0.3)
        await x(ws, "stop()", "motors['A'].stop()")

        print("\n--- Done ---")


asyncio.run(main())
