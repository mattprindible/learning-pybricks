#!/usr/bin/env python3
"""
seam_check.py — the system's living specification.

Running this file answers: "Is the system working, and what does it do?"
Each contract is a verifiable statement about system behaviour. Pass output
is written as capability statements, not test labels — the output IS the
documentation. A future agent reading this file can infer what the platform
guarantees without reading any other file.

Requires: server.py running, hub connected via iOS app, iPhone attached.

  Bus           — WebSocket greeting and protocol version
  Hub           — exec interface, stdout convention, sensor streams, battery telemetry
  Phone         — iOS sensor bridge: battery snapshot, camera stream
  Connectivity  — hardware presence state in hello; real-time connect/disconnect events

Usage: uv run python tests/seam_check.py
"""
import asyncio
import base64
import json
import sys
import websockets

WS_URL = "ws://localhost:8765/"
HUB_TIMEOUT = 15


# ── Helpers ──────────────────────────────────────────────────────────────────

async def recv_hub_stdout(ws, timeout=HUB_TIMEOUT):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("no hub_stdout within timeout")
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if msg.get("type") == "hub_stdout":
            return msg["data"]


async def send_exec(ws, code):
    await ws.send(json.dumps({"target": "hub", "data": "exec:" + code}))
    lines = []
    while True:
        line = await recv_hub_stdout(ws)
        if line.startswith(">exec:"):
            return lines, line
        lines.append(line)


def section(title):
    print(f"\n{title}")


def passed(label, detail=None):
    print(f"  pass  {label}")
    if detail:
        print(f"        {detail}")
    return True


def failed(label, reason):
    print(f"  FAIL  {label}")
    print(f"        {reason}")
    return False


# ── Bus ───────────────────────────────────────────────────────────────────────

async def contract_server_hello(ws):
    """
    Every connecting client receives {type:'hello', ws_url, version:N} as the
    first message. ws_url is the authoritative WebSocket address for reconnects.
    version is a monotonic integer agents use to gate on new capabilities.
    """
    try:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    except TimeoutError:
        return failed("server hello", "no message received within 5s")
    except json.JSONDecodeError as e:
        return failed("server hello", f"non-JSON first message: {e}")

    if msg.get("type") != "hello":
        return failed("server hello", f"expected type='hello', got {msg.get('type')!r}")
    if "ws_url" not in msg:
        return failed("server hello", "ws_url field missing")
    if not isinstance(msg.get("version"), int):
        return failed("server hello", f"version missing or not int: {msg.get('version')!r}")

    return passed(
        f"Server greets every connecting client with {{type:'hello', ws_url, version:{msg['version']}}}"
    )


# ── Hub ───────────────────────────────────────────────────────────────────────

async def contract_hub_exec_ok(ws):
    """
    exec: sends arbitrary Python to the hub over BLE and returns the result.
    Output lines arrive >-prefixed on hub_stdout. >exec:ok is the terminal marker.
    This contract proves the full path: server → iOS BLE bridge → hub → back.
    """
    try:
        lines, status = await send_exec(ws, "print('>ping')")
    except TimeoutError:
        return failed("hub exec", "timed out — is the hub connected via iOS app?")

    if ">ping" not in lines:
        return failed("hub exec", f"expected >ping in output, got {lines}")
    if status != ">exec:ok":
        return failed("hub exec", f"expected >exec:ok terminal, got {status!r}")

    return passed("exec: runs arbitrary Python on the hub; output arrives >-prefixed with >exec:ok terminal")


async def contract_hub_exec_error(ws):
    """
    exec: exceptions produce >exec:error:MESSAGE. The original error detail is
    preserved so agents can diagnose failures without a separate log channel.
    """
    try:
        _, status = await send_exec(ws, "raise ValueError('seam_test')")
    except TimeoutError:
        return failed("hub exec error", "timed out")

    if not status.startswith(">exec:error:"):
        return failed("hub exec error", f"expected >exec:error:..., got {status!r}")
    if "seam_test" not in status:
        return failed("hub exec error", f"error detail absent in {status!r}")

    return passed("exec: exceptions surface as >exec:error:MESSAGE — original error detail preserved")


async def contract_hub_stdout_schema(ws):
    """
    All hub output reaches agents as {type:'hub_stdout', data:'...'}. Verified
    against a live message so the contract covers the full serialisation path,
    not just a schema definition.
    """
    try:
        await ws.send(json.dumps({"target": "hub", "data": "exec:print('>schema_check')"}))
        deadline = asyncio.get_event_loop().time() + HUB_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub stdout schema", "timed out")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if msg.get("type") == "hub_stdout" and msg.get("data") == ">schema_check":
                await recv_hub_stdout(ws)  # drain >exec:ok
                return passed("Hub output reaches agents as {type:'hub_stdout', data} — verified on live traffic")
    except TimeoutError:
        return failed("hub stdout schema", "timed out")


async def contract_hub_imu_stream(ws):
    """
    Agents subscribe to hub sensor streams by name and interval. The hub starts
    emitting, the server intercepts >stream: lines and delivers hub_stream JSON
    to subscribers only. Unsubscribe stops the hub stream when no subscribers remain.
    """
    await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
    deadline = asyncio.get_event_loop().time() + HUB_TIMEOUT
    started_confirmed = False
    frame = None

    try:
        while frame is None:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub imu stream", "timed out waiting for hub_stream frame")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if msg.get("type") == "hub_stdout" and ">hub:stream:started:" in msg.get("data", ""):
                started_confirmed = True
            elif msg.get("type") == "hub_stream" and msg.get("sensor") == "hub:imu":
                frame = msg
    except asyncio.TimeoutError:
        return failed("hub imu stream", "timed out")

    if not started_confirmed:
        return failed("hub imu stream", ">hub:stream:started:imu not received before first frame")

    for field in ("pitch", "roll", "heading"):
        if not isinstance(frame.get(field), float):
            return failed("hub imu stream", f"{field!r} missing or not float in {frame}")

    await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))

    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub imu stream", ">hub:stream:stopped:imu not received after unsubscribe")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if msg.get("type") == "hub_stdout" and ">hub:stream:stopped:" in msg.get("data", ""):
                break
    except asyncio.TimeoutError:
        return failed("hub imu stream", "timed out waiting for stream stopped confirmation")

    return passed(
        "Hub streams sensor data at subscriber-requested intervals; subscribe/unsubscribe lifecycle is symmetric",
        f"hub:imu → {{pitch, roll, heading: float}}  pitch={frame['pitch']:.1f}°",
    )


async def contract_hub_battery_event():
    """
    The hub emits battery telemetry once at startup. The server parses it,
    caches it, and replays it to every connecting client so agents always have
    current battery state regardless of when they join.
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") != "hello":
                return failed("hub battery", f"first message not hello: {msg}")
            deadline = asyncio.get_event_loop().time() + 5
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("hub battery", "no hub_battery — is hub connected and has it reported yet?")
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if msg.get("type") == "hub_battery":
                    v, i, c = msg.get("voltage"), msg.get("current"), msg.get("charger")
                    if not isinstance(v, int):
                        return failed("hub battery", f"voltage not int: {v!r}")
                    if not isinstance(i, int):
                        return failed("hub battery", f"current not int: {i!r}")
                    if not isinstance(c, bool):
                        return failed("hub battery", f"charger not bool: {c!r}")
                    return passed(
                        "Hub emits battery telemetry at startup; server caches and replays it to late-joining clients",
                        f"{v}mV  {i}mA  charger={c}",
                    )
    except OSError as e:
        return failed("hub battery", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("hub battery", "timed out")


# ── Phone ─────────────────────────────────────────────────────────────────────

async def contract_phone_battery_cache():
    """
    Phone battery state is a snapshot sensor: the iOS app emits it on connect
    and on any change. The server caches the last-known value and replays it to
    every new client immediately after hello — late-joining agents get current
    state without timing coordination.
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") != "hello":
                return failed("phone battery cache", f"first message not hello: {msg}")
            deadline = asyncio.get_event_loop().time() + 5
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("phone battery cache", "no phone_hardware:battery — is iPhone connected?")
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if msg.get("type") == "phone_hardware" and msg.get("sensor") == "battery":
                    level, state = msg.get("level"), msg.get("state")
                    if not isinstance(level, (int, float)):
                        return failed("phone battery cache", f"level not numeric: {level!r}")
                    if state not in {"charging", "full", "unplugged", "unknown"}:
                        return failed("phone battery cache", f"unexpected state: {state!r}")
                    return passed(
                        "Phone battery state is cached server-side and replayed to every new client after hello",
                        f"level={level:.0%}  state={state!r}",
                    )
    except OSError as e:
        return failed("phone battery cache", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("phone battery cache", "timed out")


async def contract_camera_stream():
    """
    Camera is a stream sensor: it only runs while at least one agent is subscribed.
    Subscribing starts the iOS capture session; each frame arrives as
    phone_hardware:camera carrying a base64 JPEG with width, height, and timestamp_ms.
    First 5 frames are skipped to allow auto-exposure to settle (~300ms).
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            if json.loads(await asyncio.wait_for(ws.recv(), timeout=5)).get("type") != "hello":
                return failed("camera stream", "first message not hello")

            await ws.send(json.dumps({"type": "subscribe", "sensor": "camera"}))

            frame, frames_seen = None, 0
            deadline = asyncio.get_event_loop().time() + 10
            while frame is None:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("camera stream", "timed out — is iOS app running with camera permission?")
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if msg.get("type") == "phone_hardware" and msg.get("sensor") == "camera":
                    frames_seen += 1
                    if frames_seen >= 5:
                        frame = msg

            for field, typ in (("frame", str), ("width", int), ("height", int), ("timestamp_ms", int)):
                if not isinstance(frame.get(field), typ):
                    return failed("camera stream", f"{field!r} missing or wrong type: {frame.get(field)!r}")

            frame_bytes = base64.b64decode(frame["frame"])
            if frame_bytes[:2] != b'\xff\xd8':
                return failed("camera stream", f"frame is not JPEG (magic: {frame_bytes[:2].hex()})")

            await ws.send(json.dumps({"type": "unsubscribe", "sensor": "camera"}))
            return passed(
                "Camera streams JPEG frames on subscribe; each frame carries width, height and timestamp_ms",
                f"{frame['width']}x{frame['height']}  {len(frame_bytes):,} bytes  ts={frame['timestamp_ms']}ms",
            )
    except OSError as e:
        return failed("camera stream", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("camera stream", "timed out")


async def contract_camera_not_cached():
    """
    Camera is in STREAM_ONLY_SENSORS: frames are never written to phone_state_cache
    and are never delivered to non-subscribing clients. A freshly connecting agent
    that doesn't subscribe to camera receives no camera frames — not a stale replay,
    not live frames meant for someone else.
    """
    try:
        async with websockets.connect(WS_URL) as ws_a:
            await asyncio.wait_for(ws_a.recv(), timeout=5)  # hello

            await ws_a.send(json.dumps({"type": "subscribe", "sensor": "camera"}))
            deadline = asyncio.get_event_loop().time() + 10
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("camera not cached", "camera stream never started — is iOS app running?")
                if json.loads(await asyncio.wait_for(ws_a.recv(), timeout=remaining)).get("sensor") == "camera":
                    break

            async with websockets.connect(WS_URL) as ws_b:
                await asyncio.wait_for(ws_b.recv(), timeout=5)  # hello
                got_camera = False
                try:
                    end = asyncio.get_event_loop().time() + 1.5
                    while True:
                        remaining = end - asyncio.get_event_loop().time()
                        if remaining <= 0:
                            break
                        msg = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=remaining))
                        if msg.get("type") == "phone_hardware" and msg.get("sensor") == "camera":
                            got_camera = True
                            break
                except asyncio.TimeoutError:
                    pass

                if got_camera:
                    return failed("camera not cached",
                                  "camera frame reached non-subscribing client — STREAM_ONLY_SENSORS broken")

            await ws_a.send(json.dumps({"type": "unsubscribe", "sensor": "camera"}))
            return passed(
                "Camera frames are never cached or delivered to non-subscribers — stream-only, no stale replay"
            )
    except OSError as e:
        return failed("camera not cached", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("camera not cached", "timed out")


# ── Connectivity ──────────────────────────────────────────────────────────────

async def contract_connectivity_state():
    """
    Every hello includes hub_connected and phone_connected as booleans reflecting
    the live hardware state at connect time. Agents use these to decide whether to
    wait for hardware before issuing commands.
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    except OSError as e:
        return failed("connectivity state", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("connectivity state", "timed out waiting for hello")

    if msg.get("type") != "hello":
        return failed("connectivity state", f"first message not hello: {msg}")
    if not isinstance(msg.get("hub_connected"), bool):
        return failed("connectivity state", f"hub_connected not bool: {msg.get('hub_connected')!r}")
    if not isinstance(msg.get("phone_connected"), bool):
        return failed("connectivity state", f"phone_connected not bool: {msg.get('phone_connected')!r}")

    return passed(
        "hello reflects live hardware state: hub_connected and phone_connected booleans",
        f"hub_connected={msg['hub_connected']}  phone_connected={msg['phone_connected']}",
    )


async def contract_connectivity_event_broadcast():
    """
    When hub connection state changes, all non-sender clients receive hub_connected
    or hub_disconnected immediately. Agents rely on these events to pause commands
    when the hub drops and resume when it reconnects — without polling.
    """
    try:
        async with websockets.connect(WS_URL) as ws_obs, \
                   websockets.connect(WS_URL) as ws_inj:
            await asyncio.wait_for(ws_obs.recv(), timeout=5)
            await asyncio.wait_for(ws_inj.recv(), timeout=5)

            await ws_inj.send(json.dumps({"type": "hub_disconnected"}))
            got_disconnected = False
            try:
                deadline = asyncio.get_event_loop().time() + 3
                while not got_disconnected:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    if json.loads(await asyncio.wait_for(ws_obs.recv(), timeout=remaining)).get("type") == "hub_disconnected":
                        got_disconnected = True
            except asyncio.TimeoutError:
                pass

            if not got_disconnected:
                return failed("connectivity broadcast", "hub_disconnected not broadcast to observer")

            await ws_inj.send(json.dumps({"type": "hub_connected"}))
            got_connected = False
            try:
                deadline = asyncio.get_event_loop().time() + 3
                while not got_connected:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    if json.loads(await asyncio.wait_for(ws_obs.recv(), timeout=remaining)).get("type") == "hub_connected":
                        got_connected = True
            except asyncio.TimeoutError:
                pass

            if not got_connected:
                return failed("connectivity broadcast", "hub_connected not broadcast to observer")

    except OSError as e:
        return failed("connectivity broadcast", f"connection failed: {e}")

    return passed("Hub connect/disconnect events propagate immediately to all non-sender clients")


# ── Runner ────────────────────────────────────────────────────────────────────

async def main():
    print(f"\nVerifying system at {WS_URL}\n")
    results = []

    try:
        async with websockets.connect(WS_URL) as ws:
            section("Bus")
            results.append(await contract_server_hello(ws))

            section("Hub")
            results.append(await contract_hub_exec_ok(ws))
            results.append(await contract_hub_exec_error(ws))
            results.append(await contract_hub_stdout_schema(ws))
            results.append(await contract_hub_imu_stream(ws))
    except OSError as e:
        print(f"\nCannot connect to {WS_URL}: {e}\nIs server.py running?")
        sys.exit(1)

    results.append(await contract_hub_battery_event())

    section("Phone")
    results.append(await contract_phone_battery_cache())
    results.append(await contract_camera_stream())
    results.append(await contract_camera_not_cached())

    section("Connectivity")
    results.append(await contract_connectivity_state())
    results.append(await contract_connectivity_event_broadcast())

    print(f"\n{'─' * 56}")
    if all(results):
        print(f"  {len(results)} contracts verified — system is operational\n")
    else:
        n = results.count(False)
        print(f"  {n} of {len(results)} contracts broken\n")
        sys.exit(1)


asyncio.run(main())
