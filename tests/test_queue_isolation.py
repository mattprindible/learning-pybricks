"""
Verify per-client queue isolation: a slow client must not delay a fast one.

Run with the server and hub connected:
    uv run python test_queue_isolation.py

Expected output:
  - Fast client receives IMU frames at ~100ms cadence throughout
  - Slow client's queue fills; drop warnings appear in server log
  - Fast client frame gap never exceeds ~300ms during slow-client backlog
"""

import asyncio
import json
import time
import websockets

URL = "ws://localhost:8765/"
DURATION = 10.0  # seconds to run the test


async def fast_client(results: list[float]) -> None:
    async with websockets.connect(URL) as ws:
        await ws.recv()  # hello
        await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
        deadline = time.monotonic() + DURATION
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg.get("type") == "hub_stream":
                    results.append(time.monotonic())
            except asyncio.TimeoutError:
                pass


async def slow_client(drop_count: list[int]) -> None:
    async with websockets.connect(URL) as ws:
        await ws.recv()  # hello
        await ws.send(json.dumps({"type": "subscribe", "sensor": "hub:imu"}))
        deadline = time.monotonic() + DURATION
        received = 0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg.get("type") == "hub_stream":
                    received += 1
                    await asyncio.sleep(0.5)  # intentionally slow — 500ms between reads
            except asyncio.TimeoutError:
                pass
        drop_count.append(received)


async def main() -> None:
    fast_times: list[float] = []
    slow_count: list[int] = []

    print(f"Running {DURATION}s isolation test...")
    await asyncio.gather(
        fast_client(fast_times),
        slow_client(slow_count),
    )

    if len(fast_times) < 2:
        print("FAIL: fast client received too few frames")
        return

    gaps = [fast_times[i + 1] - fast_times[i] for i in range(len(fast_times) - 1)]
    max_gap = max(gaps) * 1000
    avg_gap = (sum(gaps) / len(gaps)) * 1000

    print(f"\nFast client: {len(fast_times)} frames over {DURATION}s")
    print(f"  avg gap: {avg_gap:.1f}ms  max gap: {max_gap:.1f}ms")
    print(f"Slow client: received ~{slow_count[0] if slow_count else 0} frames (queue drop expected in server log)")

    if max_gap < 300:
        print("\nPASS: fast client unaffected by slow client (max gap < 300ms)")
    else:
        print(f"\nFAIL: max gap {max_gap:.0f}ms — slow client is blocking fast client")


if __name__ == "__main__":
    asyncio.run(main())
