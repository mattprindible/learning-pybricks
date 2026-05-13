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
    print(f"  >> {code[:100]}{'...' if len(code) > 100 else ''}")
    results, status = await send_exec(ws, code)
    for r in results:
        print(f"     {r}")
    print(f"     {status}")
    return results, status


async def main():
    async with websockets.connect("ws://localhost:8765/") as ws:
        await ws.recv()  # hello
        print("Connected. Hub display will light up — exploration starting.\n")

        # --- API surface ---
        await x(ws, "dir(hub.display) — full API surface",
                "print('>dir:' + str(dir(hub.display)))")

        # --- pixel(row, col, brightness) ---
        await x(ws, "off() to clear",
                "hub.display.off()")

        await x(ws, "pixel(0, 0, 100) — top-left, full brightness",
                "hub.display.pixel(0, 0, 100)")
        await asyncio.sleep(0.5)

        await x(ws, "pixel(4, 4, 100) — bottom-right, full brightness",
                "hub.display.pixel(4, 4, 100)")
        await asyncio.sleep(0.5)

        await x(ws, "pixel(2, 2, 50) — center, half brightness",
                "hub.display.pixel(2, 2, 50)")
        await asyncio.sleep(0.5)

        await x(ws, "pixel brightness=0 — does it turn off that pixel?",
                "hub.display.pixel(0, 0, 0)")
        await asyncio.sleep(0.5)

        await x(ws, "pixel brightness=1 — dimmest visible?",
                "hub.display.pixel(0, 0, 1)")
        await asyncio.sleep(0.5)

        # draw an X
        await x(ws, "off(), then draw X across full 5x5",
                "hub.display.off(); "
                "[hub.display.pixel(i, i, 100) for i in range(5)]; "
                "[hub.display.pixel(i, 4 - i, 100) for i in range(5)]")
        await asyncio.sleep(2.0)

        # --- on(brightness) — all pixels at once ---
        await x(ws, "on(100) — all pixels full brightness",
                "hub.display.on(100)")
        await asyncio.sleep(1.0)

        await x(ws, "on(20) — all pixels dim",
                "hub.display.on(20)")
        await asyncio.sleep(1.0)

        await x(ws, "off()", "hub.display.off()")
        await asyncio.sleep(0.5)

        # --- Matrix (confirmed working) + pixel pattern ---
        await x(ws, "import Matrix from pybricks.tools",
                "from pybricks.tools import Matrix; print('>Matrix:ok')")

        await x(ws, "pixel() smiley — build row by row",
                "hub.display.off(); "
                "[hub.display.pixel(r, c, 100) for r, c in "
                "[(0,1),(0,3),(1,1),(1,3),(2,2),(3,0),(3,4),(4,1),(4,2),(4,3)]]")
        await asyncio.sleep(2.0)

        # --- icon() — predefined images ---
        # dir() showed 'icon' not 'image'; try passing a Matrix or string
        await x(ws, "icon() signature — what does dir show?",
                "print('>icon_type:' + str(type(hub.display.icon)))")

        await x(ws, "hub.display.icon(Matrix) — does icon() take a Matrix?",
                "from pybricks.tools import Matrix; "
                "hub.display.icon(Matrix([[0,100,0,100,0],[0,100,0,100,0],[0,0,0,0,0],[100,0,0,0,100],[0,100,100,100,0]]))")
        await asyncio.sleep(1.5)

        # --- orientation() ---
        await x(ws, "orientation() — what side values are valid?",
                "from pybricks.parameters import Side; print('>Side:' + str(dir(Side)))")

        await x(ws, "orientation(Side.LEFT) — rotate display 90°",
                "from pybricks.parameters import Side; hub.display.orientation(Side.LEFT)")
        await asyncio.sleep(0.5)

        await x(ws, "pixel(0, 0, 100) after orientation — which corner lights?",
                "hub.display.pixel(0, 0, 100)")
        await asyncio.sleep(1.0)

        await x(ws, "orientation(Side.TOP) — restore default",
                "from pybricks.parameters import Side; hub.display.orientation(Side.TOP)")
        await asyncio.sleep(0.3)

        # --- animate() — empirically find how to stop it ---
        # Known: animate() blocks exec() forever if it loops. Try a single-frame to probe.
        await x(ws, "animate() single frame — does it return immediately?",
                "from pybricks.tools import Matrix; "
                "hub.display.animate([Matrix([[100]*5]*5)], delay=200)")
        await asyncio.sleep(1.0)

        # --- what's on hub besides display? ---
        await x(ws, "dir(hub) — full hub API surface",
                "print('>dir_hub:' + str(dir(hub)))")

        await x(ws, "off()", "hub.display.off()")

        print("\n--- Done ---")


asyncio.run(main())
