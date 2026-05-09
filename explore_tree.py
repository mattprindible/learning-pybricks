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


async def d(ws, label, expr):
    """dir() a single expression and print the result."""
    code = f"print('>{label}:' + str(dir({expr})))"
    results, status = await send_exec(ws, code)
    for r in results:
        print(f"  {r}")
    if status != ">exec:ok":
        print(f"  {status}")


async def v(ws, label, expr):
    """Evaluate an expression and print its value."""
    code = f"print('>{label}:' + str({expr}))"
    results, status = await send_exec(ws, code)
    for r in results:
        print(f"  {r}")
    if status != ">exec:ok":
        print(f"  {status}")


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        print("=== Hub API tree ===\n")

        # --- hub subsystems ---
        print("[hub]")
        await d(ws, "hub", "hub")

        print("\n[hub.charger]  (unexplored)")
        await d(ws, "charger", "hub.charger")

        print("\n[hub.ble]")
        await d(ws, "ble", "hub.ble")

        print("\n[hub.system]")
        await d(ws, "system", "hub.system")

        print("\n[hub.battery]")
        await d(ws, "battery", "hub.battery")

        print("\n[hub.buttons]")
        await d(ws, "buttons", "hub.buttons")

        print("\n[hub.imu]")
        await d(ws, "imu", "hub.imu")

        print("\n[hub.light]")
        await d(ws, "light", "hub.light")

        print("\n[hub.speaker]")
        await d(ws, "speaker", "hub.speaker")

        print("\n[hub.display]  (already explored — confirm)")
        await d(ws, "display", "hub.display")

        # --- connected devices ---
        print("\n=== Connected devices ===\n")

        print("[motors keys]")
        await v(ws, "motors", "list(motors.keys())")

        print("\n[sensors keys]")
        await v(ws, "sensors", "list(sensors.keys())")

        # motor — pick first available
        results, _ = await send_exec(ws, "print('>mk:' + str(list(motors.keys())))")
        motor_keys = []
        for r in results:
            if r.startswith(">mk:"):
                import ast
                motor_keys = ast.literal_eval(r[4:])

        if motor_keys:
            k = motor_keys[0]
            print(f"\n[Motor '{k}']")
            await d(ws, f"motor_{k}", f"motors['{k}']")

        # sensors — dir each type
        results, _ = await send_exec(ws, "print('>sk:' + str(list(sensors.keys())))")
        sensor_keys = []
        for r in results:
            if r.startswith(">sk:"):
                import ast
                sensor_keys = ast.literal_eval(r[4:])

        for k in sensor_keys:
            results2, _ = await send_exec(ws, f"print('>st:' + str(sensors['{k}'][0]))")
            stype = ""
            for r in results2:
                if r.startswith(">st:"):
                    stype = r[4:]
            print(f"\n[Sensor '{k}' — {stype}]")
            await d(ws, f"sensor_{k}", f"sensors['{k}'][1]")

        # =====================================================================
        print("\n\n=== Phase 2: Values and behavior ===\n")

        # --- hub.charger ---
        print("[hub.charger]")
        await v(ws, "charger.connected", "hub.charger.connected()")
        await v(ws, "charger.current",   "hub.charger.current()")
        await v(ws, "charger.status",    "hub.charger.status()")

        # --- hub.battery extras ---
        print("\n[hub.battery — temperature, type]")
        await v(ws, "battery.temperature", "hub.battery.temperature()")
        await v(ws, "battery.type",        "hub.battery.type()")

        # --- hub.ble extras ---
        print("\n[hub.ble — signal_strength]")
        await v(ws, "ble.signal_strength", "hub.ble.signal_strength()")

        # --- hub.system extras ---
        print("\n[hub.system — name, storage]")
        await v(ws, "system.name", "hub.system.name()")
        await d(ws, "system.storage", "hub.system.storage")

        # --- hub.imu extras ---
        print("\n[hub.imu — orientation, rotation, settings]")
        await v(ws, "imu.orientation", "hub.imu.orientation()")
        # rotation() needs an Axis — can't inline import in str(), use send_exec
        results, status = await send_exec(
            ws, "from pybricks.parameters import Axis; "
                "print('>imu.rotation.Z:' + str(hub.imu.rotation(Axis.Z)))")
        for r in results:
            print(f"  {r}")
        if status != ">exec:ok":
            print(f"  {status}")
        await v(ws, "imu.settings", "hub.imu.settings()")

        # --- hub.light extras ---
        print("\n[hub.light — blink, animate]")
        # blink is non-blocking and loops; stop it with on()
        _, status = await send_exec(ws, "hub.light.blink(Color.ORANGE, [200, 200])")
        print(f"  >light.blink: {status}")
        await asyncio.sleep(1.0)
        await send_exec(ws, "hub.light.on(Color.GREEN)")
        await asyncio.sleep(0.2)
        # animate similarly
        _, status = await send_exec(ws, "hub.light.animate([Color.RED, Color.GREEN, Color.BLUE], interval=200)")
        print(f"  >light.animate: {status}")
        await asyncio.sleep(1.0)
        await send_exec(ws, "hub.light.on(Color.GREEN)")

        # --- hub.speaker extras ---
        print("\n[hub.speaker — play_notes (blocking)]")
        _, status = await send_exec(ws, "hub.speaker.play_notes(['C4/4', 'E4/4', 'G4/4'], tempo=120)")
        print(f"  >speaker.play_notes: {status}")

        # --- motor sub-objects and extra methods ---
        if motor_keys:
            k = motor_keys[0]
            print(f"\n[Motor '{k}' — control, settings, done, load]")
            await d(ws, f"motor_{k}.control", f"motors['{k}'].control")
            await v(ws, f"motor_{k}.settings()", f"motors['{k}'].settings()")
            await v(ws, f"motor_{k}.done()",     f"motors['{k}'].done()")
            await v(ws, f"motor_{k}.load()",     f"motors['{k}'].load()")

            print(f"\n[Motor '{k}' — dc(), brake(), hold()]")
            _, s = await send_exec(ws, f"motors['{k}'].dc(30)")
            print(f"  >dc(30): {s}")
            await asyncio.sleep(0.5)
            _, s = await send_exec(ws, f"motors['{k}'].brake()")
            print(f"  >brake(): {s}")
            await asyncio.sleep(0.5)
            _, s = await send_exec(ws, f"motors['{k}'].dc(30)")
            await asyncio.sleep(0.5)
            _, s = await send_exec(ws, f"motors['{k}'].hold()")
            print(f"  >hold(): {s}")
            await asyncio.sleep(0.5)
            await send_exec(ws, f"motors['{k}'].stop()")

        # --- sensor extras ---
        for k in sensor_keys:
            results2, _ = await send_exec(ws, f"print('>st:' + str(sensors['{k}'][0]))")
            stype = ""
            for r in results2:
                if r.startswith(">st:"):
                    stype = r[4:]

            if stype == "sonic":
                print(f"\n[Sensor '{k}' (sonic) — presence, lights]")
                await v(ws, f"sensor_{k}.presence()", f"sensors['{k}'][1].presence()")
                await d(ws, f"sensor_{k}.lights",     f"sensors['{k}'][1].lights")

            elif stype == "color":
                print(f"\n[Sensor '{k}' (color) — ambient, reflection, hsv, detectable_colors, lights]")
                await v(ws, f"sensor_{k}.ambient()",            f"sensors['{k}'][1].ambient()")
                await v(ws, f"sensor_{k}.reflection()",         f"sensors['{k}'][1].reflection()")
                await v(ws, f"sensor_{k}.hsv()",                f"sensors['{k}'][1].hsv()")
                await v(ws, f"sensor_{k}.detectable_colors()",  f"sensors['{k}'][1].detectable_colors()")
                await d(ws, f"sensor_{k}.lights",               f"sensors['{k}'][1].lights")

        # =====================================================================
        print("\n\n=== Phase 3: Closing gaps ===\n")

        # --- hub.ble.signal_strength — needs unknown second arg ---
        print("[hub.ble.signal_strength — probe arg]")
        # Try channel 0, then 1
        for ch in [0, 1, 37, 38, 39]:
            results, status = await send_exec(ws, f"print('>ble.signal_strength({ch}):' + str(hub.ble.signal_strength({ch})))")
            for r in results:
                print(f"  {r}")
            if status != ">exec:ok":
                print(f"  {status}")
                break

        # --- hub.system.storage — probe read/write API ---
        print("\n[hub.system.storage — probe API]")
        # Try calling with a key to read
        await v(ws, "storage('test_key')?", "hub.system.storage('test_key')")
        # Try calling with key+value to write
        results, status = await send_exec(ws, "hub.system.storage('test_key', 42); print('>storage:write:ok')")
        for r in results:
            print(f"  {r}")
        print(f"  write status: {status}")
        # Read it back
        await v(ws, "storage('test_key') after write", "hub.system.storage('test_key')")
        # Clean up
        await send_exec(ws, "hub.system.storage('test_key', None)")

        # --- sensor.lights — probe on() args ---
        for k in sensor_keys:
            results2, _ = await send_exec(ws, f"print('>st:' + str(sensors['{k}'][0]))")
            stype = ""
            for r in results2:
                if r.startswith(">st:"):
                    stype = r[4:]

            print(f"\n[Sensor '{k}' ({stype}) lights — probe on() args]")
            # Try on() with no args
            results, status = await send_exec(ws, f"sensors['{k}'][1].lights.on()")
            print(f"  on() no args: {status}")
            await asyncio.sleep(0.3)
            # Try on() with a brightness int
            results, status = await send_exec(ws, f"sensors['{k}'][1].lights.on(100)")
            print(f"  on(100): {status}")
            await asyncio.sleep(0.3)
            # Try on() with per-LED tuple (sonic has 4 LEDs, color has 3)
            results, status = await send_exec(ws, f"sensors['{k}'][1].lights.on(100, 0, 100, 0)")
            print(f"  on(100,0,100,0): {status}")
            await asyncio.sleep(0.3)
            await send_exec(ws, f"sensors['{k}'][1].lights.off()")

        print("\n=== Done ===")


asyncio.run(main())
