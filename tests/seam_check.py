#!/usr/bin/env python3
"""
seam_check.py — empirical contract tests for the interface seams.

The test IS the contract. If all pass, the system can function end-to-end.
Run with server.py running and hub connected via iOS app.

  Seam 1: Server WebSocket schema   (server.py ↔ agents/clients)
  Seam 2: Hub stdout convention     (hub/iOS ↔ server) — >prefix, exec handler, terminal markers
  Seam 3: Hub command routing       (server ↔ iOS ↔ hub) — commands reach the hub
  Seam 4: Hub streaming             (subscribe → started → hub_stream → unsubscribe → stopped)
  Seam 5: Server state delivery     (new clients receive cached phone_hardware state on connect)
  Seam 6: Connectivity state        (hello includes hub_connected and phone_connected booleans)
  Seam 7: Connectivity broadcast    (hub_disconnected/hub_connected events reach non-sender clients)
  Seam 8: Hub battery telemetry     (hub_battery cached and replayed to fresh clients; voltage/current/charger fields)

Usage: uv run python tests/seam_check.py
"""
import asyncio
import json
import sys
import websockets

WS_URL = "ws://localhost:8765/"
HUB_TIMEOUT = 15  # seconds to wait for hub response


async def recv_hub_stdout(ws, timeout=HUB_TIMEOUT):
    """Pull messages until we get a hub_stdout line."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("no hub_stdout within timeout")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("type") == "hub_stdout":
            return msg["data"]


async def send_exec(ws, code):
    """Send exec command; collect output lines until >exec: terminal."""
    await ws.send(json.dumps({"target": "hub", "data": "exec:" + code}))
    lines = []
    while True:
        line = await recv_hub_stdout(ws)
        if line.startswith(">exec:"):
            return lines, line
        lines.append(line)


def passed(label):
    print(f"  pass  {label}")
    return True


def failed(label, reason):
    print(f"  FAIL  {label}\n        {reason}")
    return False


# --- Seam 1: Server WebSocket schema ---

async def contract_server_hello(ws):
    """Server sends {type: 'hello', ws_url: '...'} as the first message on connect."""
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(raw)
    except TimeoutError:
        return failed("server hello", "no message received within 5s")
    except json.JSONDecodeError as e:
        return failed("server hello", f"non-JSON first message: {e}")

    if msg.get("type") != "hello":
        return failed("server hello", f"type={msg.get('type')!r}, expected 'hello'")
    if "ws_url" not in msg:
        return failed("server hello", "missing ws_url field")
    if not isinstance(msg.get("version"), int):
        return failed("server hello", f"version missing or not int: {msg.get('version')!r}")

    return passed(f"server hello — {{type: 'hello', ws_url, version: {msg['version']}}}")


# --- Seam 2: Hub stdout convention ---

async def contract_hub_exec_ok(ws):
    """
    Hub responds to exec with >-prefixed output then >exec:ok terminal.
    Proves: BLE bridge live, >prefix convention, exec handler present, terminal marker.
    """
    try:
        lines, status = await send_exec(ws, "print('>ping')")
    except TimeoutError:
        return failed("hub exec ok", "timed out — is the hub connected via iOS app?")

    if ">ping" not in lines:
        return failed("hub exec ok", f"expected >ping in output, got {lines}")
    if status != ">exec:ok":
        return failed("hub exec ok", f"expected >exec:ok terminal, got {status!r}")

    return passed("hub exec ok — BLE bridge + >prefix + exec handler + >exec:ok terminal")


async def contract_hub_exec_error(ws):
    """Exec errors produce >exec:error:MESSAGE (same terminal, different prefix variant)."""
    try:
        _, status = await send_exec(ws, "raise ValueError('seam_test')")
    except TimeoutError:
        return failed("hub exec error", "timed out")

    if not status.startswith(">exec:error:"):
        return failed("hub exec error", f"expected >exec:error:..., got {status!r}")
    if "seam_test" not in status:
        return failed("hub exec error", f"error detail absent in {status!r}")

    return passed("hub exec error — >exec:error:MESSAGE terminal")


# --- Seam 3: Hub command routing ---

async def contract_hub_stdout_schema(ws):
    """
    hub_stdout messages from the server carry {type, data} fields.
    Verified against a real message, not just a schema definition.
    """
    try:
        await ws.send(json.dumps({"target": "hub", "data": "exec:print('>schema_check')"}))
        deadline = asyncio.get_event_loop().time() + HUB_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub_stdout schema", "timed out")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("type") == "hub_stdout" and msg.get("data") == ">schema_check":
                # Drain the >exec:ok
                await recv_hub_stdout(ws)
                if "type" not in msg or "data" not in msg:
                    return failed("hub_stdout schema", f"missing fields in {msg}")
                return passed("hub_stdout schema — {type: 'hub_stdout', data: '...'}")
    except TimeoutError:
        return failed("hub_stdout schema", "timed out")


async def contract_hub_imu_stream(ws):
    """
    Subscribe to hub:imu → >hub:stream:started:imu → hub_stream frames arrive → unsubscribe → >hub:stream:stopped:imu.
    Proves: start/stop lifecycle, >stream: interception, hub_stream JSON schema.
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
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
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
            return failed("hub imu stream", f"field {field!r} missing or not float in {frame}")

    await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))

    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub imu stream", ">hub:stream:stopped:imu not received after unsubscribe")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("type") == "hub_stdout" and ">hub:stream:stopped:" in msg.get("data", ""):
                break
    except asyncio.TimeoutError:
        return failed("hub imu stream", "timed out waiting for stream stopped confirmation")

    return passed("hub imu stream — subscribe→started→hub_stream{pitch,roll,heading}→unsubscribe→stopped")


# --- Seam 5: Server state delivery ---

async def contract_phone_state_cache():
    """
    Fresh connection receives cached phone_hardware:battery immediately after hello.
    Proves: server replays last-known phone state to late-joining clients.
    Opens its own connection so the cached state arrives cold (not replayed from earlier recv).
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("type") != "hello":
                return failed("phone state cache", f"first message was not hello: {msg}")

            deadline = asyncio.get_event_loop().time() + 5
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("phone state cache", "no phone_hardware:battery — is iPhone connected?")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("type") == "phone_hardware" and msg.get("sensor") == "battery":
                    level = msg.get("level")
                    state = msg.get("state")
                    if not isinstance(level, (int, float)):
                        return failed("phone state cache", f"battery level not numeric: {level!r}")
                    if state not in {"charging", "full", "unplugged", "unknown"}:
                        return failed("phone state cache", f"battery state unexpected: {state!r}")
                    print(f"     phone battery: level={level:.0%} state={state!r}")
                    return passed("phone state cache — battery replayed to fresh client on connect")
    except OSError as e:
        return failed("phone state cache", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("phone state cache", "timed out")


# --- Seam 6: Connectivity state ---

async def contract_connectivity_state():
    """
    Fresh hello includes hub_connected (bool) and phone_connected (bool).
    Both must be present; values may be True or False depending on hardware state.
    Opens its own connection so it reads a cold hello with current state.
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
    except OSError as e:
        return failed("connectivity state", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("connectivity state", "timed out waiting for hello")

    if msg.get("type") != "hello":
        return failed("connectivity state", f"first message was not hello: {msg}")
    if not isinstance(msg.get("hub_connected"), bool):
        return failed("connectivity state", f"hub_connected missing or not bool: {msg.get('hub_connected')!r}")
    if not isinstance(msg.get("phone_connected"), bool):
        return failed("connectivity state", f"phone_connected missing or not bool: {msg.get('phone_connected')!r}")

    hc = msg["hub_connected"]
    pc = msg["phone_connected"]
    print(f"     hub_connected={hc} phone_connected={pc}")
    return passed("connectivity state — hello includes hub_connected + phone_connected booleans")


async def contract_connectivity_event_broadcast():
    """
    hub_disconnected and hub_connected events are broadcast to non-sender clients.
    Uses synthetic injection — any client can send these events; the server routes
    them to all other connected clients and updates its own state.
    Opens its own connections and restores server state when done.
    """
    try:
        async with websockets.connect(WS_URL) as ws_obs, \
                   websockets.connect(WS_URL) as ws_inj:
            # drain hellos (and any cached phone_hardware state)
            await asyncio.wait_for(ws_obs.recv(), timeout=5)
            await asyncio.wait_for(ws_inj.recv(), timeout=5)

            # inject hub_disconnected; observer should receive the broadcast
            await ws_inj.send(json.dumps({"type": "hub_disconnected"}))
            got_disconnected = False
            try:
                deadline = asyncio.get_event_loop().time() + 3
                while not got_disconnected:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(ws_obs.recv(), timeout=remaining)
                    if json.loads(raw).get("type") == "hub_disconnected":
                        got_disconnected = True
            except asyncio.TimeoutError:
                pass

            if not got_disconnected:
                return failed("connectivity broadcast", "hub_disconnected not broadcast to observer")

            # restore: inject hub_connected; observer should receive it
            await ws_inj.send(json.dumps({"type": "hub_connected"}))
            got_connected = False
            try:
                deadline = asyncio.get_event_loop().time() + 3
                while not got_connected:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(ws_obs.recv(), timeout=remaining)
                    if json.loads(raw).get("type") == "hub_connected":
                        got_connected = True
            except asyncio.TimeoutError:
                pass

            if not got_connected:
                return failed("connectivity broadcast", "hub_connected not broadcast to observer")

    except OSError as e:
        return failed("connectivity broadcast", f"connection failed: {e}")

    return passed("connectivity broadcast — hub_disconnected + hub_connected reach non-sender clients")


async def contract_hub_battery_event():
    """
    Fresh connection receives cached hub_battery immediately after hello.
    Proves: hub battery telemetry is emitted by main.py, parsed by server, cached, and replayed.
    """
    try:
        async with websockets.connect(WS_URL) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("type") != "hello":
                return failed("hub battery event", f"first message not hello: {msg}")
            deadline = asyncio.get_event_loop().time() + 5
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return failed("hub battery event", "no hub_battery — is hub connected and has it reported yet?")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("type") == "hub_battery":
                    voltage = msg.get("voltage")
                    current = msg.get("current")
                    charger = msg.get("charger")
                    if not isinstance(voltage, int):
                        return failed("hub battery event", f"voltage not int: {voltage!r}")
                    if not isinstance(current, int):
                        return failed("hub battery event", f"current not int: {current!r}")
                    if not isinstance(charger, bool):
                        return failed("hub battery event", f"charger not bool: {charger!r}")
                    print(f"     hub battery: {voltage}mV {current}mA charger={charger}")
                    return passed("hub battery event — voltage/current/charger replayed to fresh client on connect")
    except OSError as e:
        return failed("hub battery event", f"connection failed: {e}")
    except asyncio.TimeoutError:
        return failed("hub battery event", "timed out")


async def main():
    print(f"\nConnecting to {WS_URL} ...\n")
    try:
        async with websockets.connect(WS_URL) as ws:
            print("Contract checks:\n")
            results = [
                await contract_server_hello(ws),
                await contract_hub_exec_ok(ws),
                await contract_hub_exec_error(ws),
                await contract_hub_stdout_schema(ws),
                await contract_hub_imu_stream(ws),
            ]
    except OSError as e:
        print(f"Cannot connect: {e}\nIs server.py running?")
        sys.exit(1)

    # Seams 5, 6, 7, and 8 open their own connections
    results.append(await contract_phone_state_cache())
    results.append(await contract_connectivity_state())
    results.append(await contract_connectivity_event_broadcast())
    results.append(await contract_hub_battery_event())

    print()
    if all(results):
        print(f"All {len(results)} contracts intact.\n")
    else:
        n = results.count(False)
        print(f"{n} of {len(results)} contract(s) broken.\n")
        sys.exit(1)


asyncio.run(main())
