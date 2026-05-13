"""
test_hardware_multi.py — multi-client + real hardware integration tests

Requires: server.py running, hub connected via iOS app, iPhone attached.

Scenarios:
  A. Real-hardware isolation  — fast + slow client both on hub:imu; slow drops, fast doesn't
  B. Cross-device interleave  — hub:imu stream + phone_hardware events arrive in the same client
  C. Command during stream    — hub:light:on while hub:imu is streaming; both succeed
  D. Crash isolation          — abrupt client close cleans up subscriptions; surviving client unaffected
  E. Hello accuracy           — hub_connected in hello confirmed by a live hub exec round-trip

Usage: uv run python tests/test_hardware_multi.py
"""

import asyncio
import json
import time
import websockets

URL = "ws://localhost:8765/"
TIMEOUT = 15.0


# ── helpers ──────────────────────────────────────────────────────────────────

async def connect():
    ws = await websockets.connect(URL)
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert hello["type"] == "hello", f"unexpected first message: {hello}"
    return ws


async def recv_until(ws, predicate, timeout=TIMEOUT):
    """Drain messages until predicate(parsed_msg) is True; return that message."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("no matching message within timeout")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if predicate(msg):
            return msg


def ok(label): print(f"  pass  {label}")
def fail(label, reason): print(f"  FAIL  {label}\n        {reason}"); return False


# ── scenario A: real-hardware isolation ──────────────────────────────────────

async def scenario_a():
    print("\nA. Real-hardware isolation (fast + slow client on hub:imu)")

    fast_times: list[float] = []
    slow_received = 0
    duration = 8.0

    async def fast(ws):
        await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg.get("type") == "hub_stream" and msg.get("sensor") == "hub:imu":
                    fast_times.append(time.monotonic())
            except asyncio.TimeoutError:
                pass

    async def slow(ws):
        nonlocal slow_received
        await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg.get("type") == "hub_stream" and msg.get("sensor") == "hub:imu":
                    slow_received += 1
                    await asyncio.sleep(0.5)  # intentionally slow
            except asyncio.TimeoutError:
                pass

    ws_fast, ws_slow = await asyncio.gather(connect(), connect())
    try:
        await asyncio.gather(fast(ws_fast), slow(ws_slow))
    finally:
        await ws_fast.close()
        await ws_slow.close()

    if len(fast_times) < 2:
        return fail("A", f"fast client received too few frames ({len(fast_times)})")

    gaps = [fast_times[i+1] - fast_times[i] for i in range(len(fast_times) - 1)]
    max_gap_ms = max(gaps) * 1000
    avg_gap_ms = (sum(gaps) / len(gaps)) * 1000
    expected = duration / 0.1  # frames at 100ms interval

    frame_pct = len(fast_times) / expected * 100
    print(f"     fast: {len(fast_times)} frames ({frame_pct:.0f}% of expected ~{expected:.0f}), "
          f"avg {avg_gap_ms:.1f}ms, max gap {max_gap_ms:.1f}ms")
    print(f"     slow: {slow_received} frames received (drops expected in server log)")
    # BLE delivers frames in bursts, so a single large gap is normal hardware jitter.
    # We care that the fast client received substantially all frames at the right average rate.
    if frame_pct < 85:
        return fail("A", f"fast client only received {frame_pct:.0f}% of expected frames")
    if avg_gap_ms > 150:
        return fail("A", f"fast client avg gap {avg_gap_ms:.1f}ms — well above 100ms target")

    ok(f"A. fast client unaffected by slow client ({frame_pct:.0f}% frames, {avg_gap_ms:.1f}ms avg)")
    return True


# ── scenario B: cross-device interleave ──────────────────────────────────────

async def scenario_b():
    print("\nB. Cross-device interleave (hub:imu stream + phone_hardware in same client)")

    ws = await connect()
    try:
        await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))

        imu_received = False
        phone_received = False
        deadline = time.monotonic() + TIMEOUT

        while not (imu_received and phone_received):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("type") == "hub_stream" and msg.get("sensor") == "hub:imu":
                    imu_received = True
                elif msg.get("type") == "phone_hardware" and msg.get("sensor") == "battery":
                    phone_received = True
                    print(f"     phone battery: level={msg.get('level'):.0%} state={msg.get('state')!r}")
            except asyncio.TimeoutError:
                break

        await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
    finally:
        await ws.close()

    if not imu_received:
        return fail("B", "no hub_stream events received — is hub streaming?")
    if not phone_received:
        return fail("B", "no phone_hardware:battery event — is iPhone connected?")

    ok("B. hub:imu stream and phone_hardware events interleave in the same client")
    return True


# ── scenario C: hub command during stream ─────────────────────────────────────

async def scenario_c():
    print("\nC. Hub command during stream (hub:light:on while hub:imu active)")

    ws = await connect()
    try:
        await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))

        # wait for first IMU frame to confirm stream is running
        await recv_until(ws, lambda m: m.get("type") == "hub_stream" and m.get("sensor") == "hub:imu")

        # send a blocking hub command while the stream is active
        t0 = time.monotonic()
        await ws.send(json.dumps({"target": "hub", "data": "hub:light:on:BLUE"}))

        try:
            response = await recv_until(
                ws,
                lambda m: m.get("type") == "hub_stdout" and ">hub:light:" in m.get("data", ""),
            )
        except TimeoutError:
            return fail("C", "hub:light command got no response while streaming")

        cmd_ms = (time.monotonic() - t0) * 1000
        print(f"     hub:light response: {response['data']!r} in {cmd_ms:.0f}ms")

        # confirm stream is still running after command
        try:
            await recv_until(
                ws,
                lambda m: m.get("type") == "hub_stream" and m.get("sensor") == "hub:imu",
                timeout=2.0,
            )
        except TimeoutError:
            return fail("C", "hub:imu stream stopped after hub command")

        # restore hub light
        await ws.send(json.dumps({"target": "hub", "data": "hub:light:off"}))
        await ws.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
    finally:
        await ws.close()

    ok(f"C. hub:light responded in {cmd_ms:.0f}ms; hub:imu stream continued uninterrupted")
    return True


# ── scenario D: crash isolation ───────────────────────────────────────────────

async def scenario_d():
    print("\nD. Crash isolation (abrupt close cleans up subscriptions; surviving client unaffected)")

    ws_x = await connect()
    ws_y = await connect()

    await ws_x.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))

    # confirm X is streaming before we kill it
    try:
        await recv_until(ws_x, lambda m: m.get("type") == "hub_stream", timeout=5.0)
    except TimeoutError:
        await ws_x.close(); await ws_y.close()
        return fail("D", "client X never received a stream frame before crash")

    # simulate crash: close transport without WebSocket close handshake
    ws_x.transport.close()

    # give server time to detect the disconnect and run _cleanup_subscriptions
    await asyncio.sleep(1.5)

    # Y subscribes — if X's subscription was properly cleaned up, Y is the first
    # subscriber and the server will send hub:stream:start:imu:100 to the hub,
    # which responds with >hub:stream:started:imu
    await ws_y.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
    try:
        await recv_until(
            ws_y,
            lambda m: m.get("type") == "hub_stdout" and ">hub:stream:started" in m.get("data", ""),
            timeout=5.0,
        )
    except TimeoutError:
        await ws_y.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
        await ws_y.close()
        return fail("D", ">hub:stream:started not received — X's subscription was not cleaned up on crash")

    # confirm Y is still receiving frames
    try:
        await recv_until(ws_y, lambda m: m.get("type") == "hub_stream", timeout=3.0)
    except TimeoutError:
        await ws_y.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
        await ws_y.close()
        return fail("D", "client Y not receiving hub_stream after X crash")

    await ws_y.send(json.dumps({"type": "unsubscribe", "sensor": "hub:imu"}))
    await ws_y.close()
    ok("D. X's subscriptions cleaned up on crash; Y is first subscriber on re-subscribe; Y functional")
    return True


# ── scenario E: hello accuracy ────────────────────────────────────────────────

async def scenario_e():
    print("\nE. Hello accuracy (hub_connected field confirmed by live hub exec round-trip)")

    ws = await websockets.connect(URL)
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert hello["type"] == "hello"

        if not hello.get("hub_connected"):
            return fail("E", "hub_connected=False in hello — is hub connected?")

        # hello claims hub is connected — verify with a live exec round-trip
        await ws.send(json.dumps({"target": "hub", "data": "exec:print('>E:alive')"}))
        try:
            await recv_until(
                ws,
                lambda m: m.get("type") == "hub_stdout" and ">E:alive" in m.get("data", ""),
                timeout=5.0,
            )
        except TimeoutError:
            return fail("E", "hello said hub_connected=True but hub did not respond to exec")
    finally:
        await ws.close()

    ok("E. hello hub_connected=True confirmed by successful hub exec round-trip")
    return True


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"\nConnecting to {URL} ...\n")
    results = [
        await scenario_a(),
        await scenario_b(),
        await scenario_c(),
        await scenario_d(),
        await scenario_e(),
    ]
    print()
    passed = sum(1 for r in results if r is True)
    total = len(results)
    if passed == total:
        print(f"All {total} scenarios passed.\n")
    else:
        print(f"{total - passed} of {total} scenario(s) failed.\n")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
