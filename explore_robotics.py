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

        # =======================================================================
        print("=== Phase 1: Module surface ===\n")
        # =======================================================================

        await x(ws, "dir(pybricks.robotics) — what's in the module?",
                "import pybricks.robotics; print('>robotics:' + str(dir(pybricks.robotics)))")

        await x(ws, "dir(DriveBase class) — class-level API",
                "from pybricks.robotics import DriveBase; print('>DriveBase:' + str(dir(DriveBase)))")

        # Check for GyroDriveBase
        await x(ws, "GyroDriveBase available?",
                "from pybricks.robotics import DriveBase; "
                "import pybricks.robotics as r; "
                "print('>has_gyro:' + str(hasattr(r, 'GyroDriveBase')))")

        # =======================================================================
        print("\n\n=== Phase 2: DriveBase instantiation ===\n")
        # =======================================================================

        # Instantiate with motors A (left) and B (right).
        # Wheel diameter and axle track are physical — use LEGO medium wheel (56mm)
        # and a rough axle track (112mm = ~4.5 studs). These are approximate;
        # we care about the API surface, not accuracy yet.
        await x(ws, "DriveBase(A, B, wheel_diameter=56, axle_track=112) — instantiate",
                "from pybricks.robotics import DriveBase; "
                "db = DriveBase(motors['A'], motors['B'], wheel_diameter=56, axle_track=112); "
                "print('>db_ok:created')")

        await x(ws, "dir(db) — instance API surface",
                "print('>db:' + str(dir(db)))")

        # =======================================================================
        print("\n\n=== Phase 3: Read current state ===\n")
        # =======================================================================

        await x(ws, "db.distance() — odometry distance (mm)",
                "print('>distance:' + str(db.distance()))")

        await x(ws, "db.angle() — odometry heading (deg)",
                "print('>angle:' + str(db.angle()))")

        await x(ws, "db.state() — full odometry state",
                "print('>state:' + str(db.state()))")

        await x(ws, "db.settings() — speed/accel parameters",
                "print('>settings:' + str(db.settings()))")

        await x(ws, "db.done() — is any motion command pending?",
                "print('>done:' + str(db.done()))")

        await x(ws, "db.stalled() — is it stalled?",
                "print('>stalled:' + str(db.stalled()))")

        # =======================================================================
        print("\n\n=== Phase 4: Motion — straight() ===\n")
        # =======================================================================

        await x(ws, "db.reset() — zero odometry",
                "db.reset()")

        await x(ws, "db.straight(200) — drive 200mm forward (blocking)",
                "db.straight(200)")

        await x(ws, "distance after straight(200)",
                "print('>distance:' + str(db.distance()))")

        await x(ws, "db.straight(-200) — return to start",
                "db.straight(-200)")

        await x(ws, "distance after return",
                "print('>distance:' + str(db.distance()))")

        # =======================================================================
        print("\n\n=== Phase 5: Motion — turn() ===\n")
        # =======================================================================

        await x(ws, "db.reset()",
                "db.reset()")

        await x(ws, "db.turn(90) — turn 90° clockwise (blocking)",
                "db.turn(90)")

        await x(ws, "angle after turn(90)",
                "print('>angle:' + str(db.angle()))")

        await x(ws, "db.turn(-90) — return to 0°",
                "db.turn(-90)")

        await x(ws, "angle after return",
                "print('>angle:' + str(db.angle()))")

        # =======================================================================
        print("\n\n=== Phase 6: drive() — non-blocking continuous ===\n")
        # =======================================================================

        await x(ws, "db.drive(200, 0) — drive straight at 200mm/s",
                "db.drive(200, 0)")

        await asyncio.sleep(1.0)

        await x(ws, "db.drive(0, 90) — spin at 90°/s",
                "db.drive(0, 90)")

        await asyncio.sleep(1.0)

        await x(ws, "db.stop() — stop",
                "db.stop()")

        # =======================================================================
        print("\n\n=== Phase 7: settings() — read then mutate ===\n")
        # =======================================================================

        await x(ws, "db.settings() — baseline",
                "print('>settings:' + str(db.settings()))")

        await x(ws, "db.settings(straight_speed=300, straight_acceleration=500, "
                    "turn_rate=180, turn_acceleration=360) — set explicit values",
                "db.settings(straight_speed=300, straight_acceleration=500, "
                "turn_rate=180, turn_acceleration=360)")

        await x(ws, "db.settings() — confirm new values",
                "print('>settings:' + str(db.settings()))")

        # Restore defaults
        await x(ws, "db.settings() — restore defaults (no args = reset?)",
                "db.settings()")

        await x(ws, "db.settings() — after reset call",
                "print('>settings:' + str(db.settings()))")

        # =======================================================================
        print("\n\n=== Phase 8: GyroDriveBase (if available) ===\n")
        # =======================================================================

        results, status = await send_exec(
            ws, "import pybricks.robotics as r; print('>has_gyro:' + str(hasattr(r, 'GyroDriveBase')))")
        has_gyro = any("True" in r for r in results)

        if has_gyro:
            await x(ws, "dir(GyroDriveBase class)",
                    "from pybricks.robotics import GyroDriveBase; "
                    "print('>GyroDriveBase:' + str(dir(GyroDriveBase)))")

            await x(ws, "GyroDriveBase(A, B, hub, 56, 112) — instantiate",
                    "from pybricks.robotics import GyroDriveBase; "
                    "gdb = GyroDriveBase(motors['A'], motors['B'], hub.imu, "
                    "wheel_diameter=56, axle_track=112); "
                    "print('>gdb_ok:created')")

            await x(ws, "dir(gdb) — does it add anything over DriveBase?",
                    "print('>gdb:' + str(dir(gdb)))")

            await x(ws, "gdb.settings() — any extra gyro parameters?",
                    "print('>gdb_settings:' + str(gdb.settings()))")
        else:
            print("\n  [GyroDriveBase not available in this firmware]")

        # =======================================================================
        print("\n\n=== Phase 9: control sub-object ===\n")
        # =======================================================================

        await x(ws, "dir(db.heading_control) — if it exists",
                "print('>heading_ctrl:' + str(dir(db.heading_control))) "
                "if hasattr(db, 'heading_control') else print('>heading_ctrl:none')")

        await x(ws, "dir(db.distance_control) — if it exists",
                "print('>distance_ctrl:' + str(dir(db.distance_control))) "
                "if hasattr(db, 'distance_control') else print('>distance_ctrl:none')")

        print("\n=== Done ===")


asyncio.run(main())
