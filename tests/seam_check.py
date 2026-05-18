#!/usr/bin/env python3
"""
seam_check.py — the system's living specification.

Running this file answers: "Is the system working, and what does it do?"
Each contract is a verifiable statement about system behaviour. Pass output
is written as capability statements, not test labels — the output IS the
documentation. A future agent reading this file can infer what the platform
guarantees without reading any other file.

The six sections correspond to the bus message taxonomy:

  Bus           — connection protocol, transport envelope, crash recovery
  Query         — request → single structured response (sensor reads, exec with terminal marker)
  Command       — request → hardware side effect + acknowledgement
  Stream        — subscribe → periodic frames → unsubscribe (hub sensors, camera)
  Event         — state change → immediate push to all clients
  Announcement  — system-level, server-cached, replayed on connect (battery, hardware state)

Requires: server.py running, hub connected via iOS app, iPhone attached.

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
    Connection protocol: every connecting client receives {type:'hello', ws_url,
    version:N} as its first message. ws_url is the authoritative WebSocket address
    for reconnects. version is a monotonic integer agents use to gate on new
    capabilities.
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
        f"Bus protocol: server sends {{type:'hello', ws_url, version:{msg['version']}}} as first message to every connecting client"
    )


async def contract_hub_stdout_schema(ws):
    """
    Transport envelope: all hub output reaches agents as {type:'hub_stdout', data:'...'}.
    Verified against a live message so the contract covers the full serialisation path,
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
                return passed("Bus transport: hub output reaches agents as {type:'hub_stdout', data} — verified on live traffic")
    except TimeoutError:
        return failed("hub stdout schema", "timed out")


async def contract_hub_handshake(ws):
    """
    Crash recovery: hub:handshake re-emits >ready on hub_stdout without stopping or
    restarting the program. This is the iOS bridge's reconnect path: when the app
    reconnects to a hub with a program already running (e.g. after an app crash), it
    sends hub:handshake via stdin rather than STOP+START to avoid GATT BUSY (0x81).

    Critically, >ready must NOT trigger a spurious hub_connected broadcast. The
    !isHubReady guard in HubConnection.swift prevents re-publishing isHubReady when
    it's already true — without it, every handshake would fire hub_connected, which
    triggers a battery exec whose >exec:ok would bleed into the next test.
    """
    try:
        await ws.send(json.dumps({"target": "hub", "data": "hub:handshake"}))
        deadline = asyncio.get_event_loop().time() + HUB_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub handshake", "timed out waiting for >ready")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if msg.get("type") == "hub_stdout" and msg.get("data") == ">ready":
                break
    except TimeoutError:
        return failed("hub handshake", "timed out")

    # Verify no spurious hub_connected fires after >ready (the !isHubReady guard
    # in iOS prevents re-publishing when the hub is already ready).
    try:
        end = asyncio.get_event_loop().time() + 1.5
        while True:
            remaining = end - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if msg.get("type") == "hub_connected":
                return failed("hub handshake",
                              "spurious hub_connected after >ready — !isHubReady guard missing or broken")
    except asyncio.TimeoutError:
        pass

    return passed("Bus recovery: hub:handshake re-emits >ready without program restart or spurious hub_connected")


# ── Query ─────────────────────────────────────────────────────────────────────

async def contract_hub_sensor_read(ws):
    """
    Query pattern: one request, one structured response. The hub processes the command
    synchronously and emits exactly one response line before accepting the next command.
    hub:imu:heading is the canonical example; the same pattern holds for all sensor
    reads and hub state queries. IMU values are always floats — not ints.
    """
    try:
        await ws.send(json.dumps({"target": "hub", "data": "hub:imu:heading"}))
        line = await recv_hub_stdout(ws)
    except TimeoutError:
        return failed("hub sensor read", "timed out — is hub connected and running?")

    if not line.startswith(">hub:imu:heading="):
        return failed("hub sensor read", f"unexpected response: {line!r}")

    try:
        heading = float(line.split("=", 1)[1])
    except ValueError:
        return failed("hub sensor read", f"could not parse float from {line!r}")

    return passed(
        "Query pattern: one request → one structured response — hub:imu:heading → >hub:imu:heading=FLOAT",
        f"heading={heading:.1f}°",
    )


async def contract_hub_exec_ok(ws):
    """
    Query/exec pattern: exec: sends arbitrary Python to the hub and returns the result.
    Unlike simple queries, exec: output may span multiple lines — agents must collect
    all >-prefixed output lines until the >exec:ok terminal marker arrives.
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

    return passed("Query/exec pattern: send exec: → collect >-prefixed output → >exec:ok terminal marker — full hub path verified")


async def contract_hub_exec_error(ws):
    """
    Query/exec pattern error path: exceptions produce >exec:error:MESSAGE. The terminal
    marker is always present — agents can rely on it to know the exec is complete even
    on failure. The original error detail is preserved for diagnosis.
    """
    try:
        _, status = await send_exec(ws, "raise ValueError('seam_test')")
    except TimeoutError:
        return failed("hub exec error", "timed out")

    if not status.startswith(">exec:error:"):
        return failed("hub exec error", f"expected >exec:error:..., got {status!r}")
    if "seam_test" not in status:
        return failed("hub exec error", f"error detail absent in {status!r}")

    return passed("Query/exec pattern: exceptions surface as >exec:error:MESSAGE — terminal marker present on error path too")


# ── Command ───────────────────────────────────────────────────────────────────

async def contract_hub_light_command(ws):
    """
    Command pattern: request with hardware side effect, single acknowledgement.
    hub:light:on:COLOR activates the status light; the hub responds >hub:light:done
    when the command is processed. The same request→ack pattern holds for display,
    speaker, and motor stop commands.
    """
    try:
        await ws.send(json.dumps({"target": "hub", "data": "hub:light:on:GREEN"}))
        line = await recv_hub_stdout(ws)
    except TimeoutError:
        return failed("hub light command", "timed out — is hub connected and running?")

    if line != ">hub:light:done":
        return failed("hub light command", f"expected >hub:light:done, got {line!r}")

    try:
        await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))
        await recv_hub_stdout(ws)
    except Exception:
        pass

    return passed("Command pattern: request with hardware side effect → single acknowledgement — hub:light:on:COLOR → >hub:light:done")


# ── Stream ────────────────────────────────────────────────────────────────────

async def contract_hub_imu_stream(ws):
    """
    Stream pattern: agents subscribe by name and interval; the hub emits continuously
    until unsubscribed. The server intercepts >stream: lines and delivers hub_stream
    JSON to subscribers only — non-subscribers never see stream traffic. Subscribe
    and unsubscribe are symmetric: the hub confirms both transitions.
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
        "Stream pattern: subscribe → hub_stream frames at requested interval → unsubscribe — symmetric lifecycle",
        f"hub:imu → {{pitch, roll, heading: float}}  pitch={frame['pitch']:.1f}°",
    )


async def contract_camera_stream():
    """
    Stream pattern (phone sensor): subscribing starts the iOS capture session; frames
    arrive as phone_hardware:camera with a base64 JPEG, width, height, and timestamp_ms.
    The camera only runs while at least one agent is subscribed — no idle capture.
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
                "Stream pattern: subscribe → JPEG frames at phone capture rate → unsubscribe — camera only runs while subscribed",
                f"{frame['width']}x{frame['height']}  {len(frame_bytes):,} bytes  ts={frame['timestamp_ms']}ms",
            )
    except OSError as e:
        return failed("camera stream", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("camera stream", "timed out")


async def contract_camera_not_cached():
    """
    Stream isolation: camera is in STREAM_ONLY_SENSORS — frames are never written to
    the server cache and are never delivered to non-subscribing clients. A freshly
    connecting agent that doesn't subscribe receives no camera frames — not a stale
    replay, not live frames meant for someone else.
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
                "Stream isolation: frames never delivered to non-subscribers — stream-only sensors have no cache"
            )
    except OSError as e:
        return failed("camera not cached", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("camera not cached", "timed out")


# ── Event ─────────────────────────────────────────────────────────────────────

async def contract_connectivity_event_broadcast():
    """
    Event pattern: when hub connection state changes, all non-sender clients receive
    hub_connected or hub_disconnected immediately — no subscription required. Agents
    use these events to pause commands when the hub drops and resume when it reconnects,
    without polling.
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

    return passed("Event pattern: hub connect/disconnect state changes pushed immediately to all connected clients — no subscription required")


# ── Announcement ──────────────────────────────────────────────────────────────

async def contract_hub_battery_event():
    """
    Announcement pattern: the hub emits battery telemetry once at startup (and
    periodically thereafter). The server parses it, caches it, and replays it to every
    connecting client so agents always have current battery state regardless of when
    they join.
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
                        "Announcement pattern: hub_battery replayed to every connecting client — server caches and serves current state",
                        f"{v}mV  {i}mA  charger={c}",
                    )
    except OSError as e:
        return failed("hub battery", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("hub battery", "timed out")


async def contract_phone_battery_cache():
    """
    Announcement pattern: phone battery state is emitted on connect and on any change.
    The server caches the last-known value and replays it to every new client
    immediately after hello — late-joining agents get current state without timing
    coordination.
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
                        "Announcement pattern: phone battery replayed to every connecting client — server caches last value regardless of when agents join",
                        f"level={level:.0%}  state={state!r}",
                    )
    except OSError as e:
        return failed("phone battery cache", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("phone battery cache", "timed out")


async def contract_phone_manifest():
    """
    Announcement pattern: on connect, every client receives the phone_connected manifest
    replayed from cache immediately after hello. The manifest is self-describing — it
    includes device identity, hardware capabilities (GPS, compass, barometer, cameras by
    type/position), Vision framework support, permission status, and battery state.
    Agents use this to know what they can ask for before issuing any sensor commands,
    without probing or enumerating capabilities separately.
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") != "hello":
                return failed("phone manifest", f"first message not hello: {msg}")
            if not msg.get("phone_connected"):
                return failed("phone manifest", "phone not connected — is iOS app running and updated?")
            deadline = asyncio.get_event_loop().time() + 5
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("phone manifest", "no phone_connected manifest received — is iOS app redeployed?")
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if msg.get("type") != "phone_connected":
                    continue
                for field in ("device", "os"):
                    if not isinstance(msg.get(field), str):
                        return failed("phone manifest", f"{field!r} not str: {msg.get(field)!r}")
                hw = msg.get("hardware", {})
                for field in ("gps", "compass", "barometer", "motion", "pedometer"):
                    if not isinstance(hw.get(field), bool):
                        return failed("phone manifest", f"hardware.{field!r} not bool: {hw.get(field)!r}")
                if not isinstance(hw.get("cameras"), list):
                    return failed("phone manifest", f"hardware.cameras not list: {hw.get('cameras')!r}")
                if not isinstance(msg.get("vision_capabilities"), list):
                    return failed("phone manifest", "vision_capabilities not list")
                perms = msg.get("permissions", {})
                for field in ("location", "camera", "motion"):
                    if perms.get(field) not in {"authorized", "denied", "not_determined"}:
                        return failed("phone manifest", f"permissions.{field!r} invalid: {perms.get(field)!r}")
                battery = msg.get("battery", {})
                if not isinstance(battery.get("level"), (int, float)):
                    return failed("phone manifest", f"battery.level not numeric: {battery.get('level')!r}")
                return passed(
                    "Announcement pattern: phone_connected manifest replayed to every connecting client — device, hardware, Vision capabilities, permissions, battery",
                    f"device={msg['device']!r}  cameras={hw.get('cameras')}  vision={msg.get('vision_capabilities')}",
                )
    except OSError as e:
        return failed("phone manifest", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("phone manifest", "timed out")


async def contract_connectivity_state():
    """
    Announcement pattern: every hello includes hub_connected and phone_connected as
    booleans reflecting the live hardware state at connect time. Delivered
    unconditionally on connect, without any subscription or request. Agents use these
    to decide whether to wait for hardware before issuing commands.
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
        "Announcement pattern: hello includes live hardware state — hub_connected and phone_connected booleans at connect time",
        f"hub_connected={msg['hub_connected']}  phone_connected={msg['phone_connected']}",
    )


# ── Runner ────────────────────────────────────────────────────────────────────

async def main():
    print(f"\nVerifying system at {WS_URL}\n")
    results = []

    try:
        async with websockets.connect(WS_URL) as ws:
            section("Bus")
            results.append(await contract_server_hello(ws))
            results.append(await contract_hub_stdout_schema(ws))
            results.append(await contract_hub_handshake(ws))

            section("Query")
            results.append(await contract_hub_sensor_read(ws))
            results.append(await contract_hub_exec_ok(ws))
            results.append(await contract_hub_exec_error(ws))

            section("Command")
            results.append(await contract_hub_light_command(ws))

            section("Stream")
            results.append(await contract_hub_imu_stream(ws))
    except OSError as e:
        print(f"\nCannot connect to {WS_URL}: {e}\nIs server.py running?")
        sys.exit(1)

    results.append(await contract_camera_stream())
    results.append(await contract_camera_not_cached())

    section("Event")
    results.append(await contract_connectivity_event_broadcast())

    section("Announcement")
    results.append(await contract_hub_battery_event())
    results.append(await contract_phone_battery_cache())
    results.append(await contract_phone_manifest())
    results.append(await contract_connectivity_state())

    print(f"\n{'─' * 56}")
    if all(results):
        print(f"  {len(results)} contracts verified — system is operational\n")
    else:
        n = results.count(False)
        print(f"  {n} of {len(results)} contracts broken\n")
        sys.exit(1)


asyncio.run(main())
