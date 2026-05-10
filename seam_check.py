#!/usr/bin/env python3
"""
seam_check.py — empirical contract tests for the three interface seams.

The test IS the contract. If all pass, the system can function end-to-end.
Run with server.py running and hub connected via iOS app.

  Seam 1: Server WebSocket schema   (server.py ↔ agents/clients)
  Seam 2: Hub stdout convention     (hub/iOS ↔ server) — >prefix, exec handler, terminal markers
  Seam 3: Hub command routing       (server ↔ iOS ↔ hub) — commands reach the hub
  Seam 4: Hub streaming             (subscribe → hub_stream events arrive with correct schema)

Usage: uv run python seam_check.py
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

    return passed("server hello — {type: 'hello', ws_url}")


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
    Subscribe to hub:imu → server sends stream:start to hub → hub_stream events arrive.
    Proves: hub: sensor routing, >stream: interception, hub_stream JSON schema.
    """
    await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
    deadline = asyncio.get_event_loop().time() + HUB_TIMEOUT
    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return failed("hub imu stream", "timed out — no hub_stream event received")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("type") == "hub_stream" and msg.get("sensor") == "hub:imu":
                await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
                for field in ("pitch", "roll", "heading"):
                    if not isinstance(msg.get(field), float):
                        return failed("hub imu stream", f"field {field!r} missing or not float in {msg}")
                return passed("hub imu stream — subscribe→hub_stream{pitch,roll,heading: float}")
    except TimeoutError:
        return failed("hub imu stream", "timed out")


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

    print()
    if all(results):
        print(f"All {len(results)} contracts intact.\n")
    else:
        n = results.count(False)
        print(f"{n} of {len(results)} contract(s) broken.\n")
        sys.exit(1)


asyncio.run(main())
