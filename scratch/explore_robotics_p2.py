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
    print(f"  >> {code[:120]}{'...' if len(code) > 120 else ''}")
    results, status = await send_exec(ws, code)
    for r in results:
        print(f"     {r}")
    print(f"     {status}")
    return results, status


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        print("Connected.\n")

        # Reuse existing db if valid; only create fresh if it's missing or None.
        # DriveBase takes exclusive ownership of its motors — EBUSY fires if you
        # try to create a second instance while the first is still alive in globals.
        await x(ws, "Reuse or create DriveBase",
                "from pybricks.robotics import DriveBase; "
                "_d = globals().get('db'); "
                "db = _d if hasattr(_d, 'reset') else DriveBase(motors['A'], motors['B'], wheel_diameter=56, axle_track=112); "
                "print('>db:ready')")

        # =======================================================================
        print("\n=== Phase 1: arc() ===\n")
        # =======================================================================

        # Call with wrong arg counts — exec:error reveals the signature
        await x(ws, "db.arc() — arity error tells us required args",
                "db.arc()")

        await x(ws, "db.arc(200) — one arg",
                "db.arc(200)")

        # arc(radius, angle) is the likely signature
        await x(ws, "db.reset()", "db.reset()")

        await x(ws, "db.arc(200, 90) — 200mm radius, 90° sweep",
                "db.arc(200, 90)")

        await x(ws, "state after arc(200, 90)",
                "print('>state:' + str(db.state()))")

        await x(ws, "db.arc(-200, -90) — mirror return",
                "db.arc(-200, -90)")

        await x(ws, "state after return",
                "print('>state:' + str(db.state()))")

        # =======================================================================
        print("\n\n=== Phase 2: curve() ===\n")
        # =======================================================================

        await x(ws, "db.curve() — arity error",
                "db.curve()")

        await x(ws, "db.curve(200) — one arg",
                "db.curve(200)")

        await x(ws, "db.reset()", "db.reset()")

        await x(ws, "db.curve(200, 90) — same as arc?",
                "db.curve(200, 90)")

        await x(ws, "state after curve(200, 90)",
                "print('>state:' + str(db.state()))")

        await x(ws, "db.curve(-200, -90) — return",
                "db.curve(-200, -90)")

        await x(ws, "state after return",
                "print('>state:' + str(db.state()))")

        # =======================================================================
        print("\n\n=== Phase 3: use_gyro() ===\n")
        # =======================================================================

        await x(ws, "db.use_gyro() — getter or requires arg?",
                "print('>use_gyro:' + str(db.use_gyro()))")

        await x(ws, "db.use_gyro(True) — enable",
                "db.use_gyro(True); print('>use_gyro:enabled')")

        await x(ws, "db.reset()", "db.reset()")

        await x(ws, "db.turn(90) with gyro on",
                "db.turn(90)")

        await x(ws, "angle after gyro turn(90)",
                "print('>angle:' + str(db.angle()))")

        await x(ws, "db.turn(-90) return",
                "db.turn(-90)")

        await x(ws, "angle after gyro return",
                "print('>angle:' + str(db.angle()))")

        await x(ws, "db.use_gyro(False) — disable",
                "db.use_gyro(False); print('>use_gyro:disabled')")

        # =======================================================================
        print("\n\n=== Phase 4: brake() vs stop() ===\n")
        # =======================================================================

        await x(ws, "db.drive(200, 0) — start moving",
                "db.drive(200, 0)")

        await asyncio.sleep(0.5)

        await x(ws, "db.brake() — active electrical brake",
                "db.brake()")

        await x(ws, "state after brake()",
                "print('>state:' + str(db.state()))")

        await x(ws, "db.drive(200, 0) — start again",
                "db.drive(200, 0)")

        await asyncio.sleep(0.5)

        await x(ws, "db.stop() — passive coast stop",
                "db.stop()")

        await x(ws, "state after stop()",
                "print('>state:' + str(db.state()))")

        # =======================================================================
        print("\n\n=== Phase 5: control sub-object values ===\n")
        # =======================================================================

        await x(ws, "db.heading_control.pid()",
                "print('>heading_pid:' + str(db.heading_control.pid()))")

        await x(ws, "db.heading_control.limits()",
                "print('>heading_limits:' + str(db.heading_control.limits()))")

        await x(ws, "db.heading_control.target_tolerances()",
                "print('>heading_tol:' + str(db.heading_control.target_tolerances()))")

        await x(ws, "db.distance_control.pid()",
                "print('>dist_pid:' + str(db.distance_control.pid()))")

        await x(ws, "db.distance_control.limits()",
                "print('>dist_limits:' + str(db.distance_control.limits()))")

        await x(ws, "db.distance_control.target_tolerances()",
                "print('>dist_tol:' + str(db.distance_control.target_tolerances()))")

        # =======================================================================
        print("\n\n=== Phase 6: done() and stalled() during motion ===\n")
        # =======================================================================

        await x(ws, "db.reset()", "db.reset()")

        await x(ws, "db.straight(1000, wait=False) — non-blocking",
                "db.straight(1000, wait=False); print('>launched:ok')")

        await x(ws, "db.done() while moving",
                "print('>done:' + str(db.done()))")

        await x(ws, "db.stalled() while moving",
                "print('>stalled:' + str(db.stalled()))")

        await x(ws, "db.stop()", "db.stop()")

        await x(ws, "db.done() after stop",
                "print('>done:' + str(db.done()))")

        print("\n=== Done ===")


asyncio.run(main())
