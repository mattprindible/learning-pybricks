#!/usr/bin/env python3
"""
vision_demo.py — live Vision framework output to your terminal

Subscribes to one camera Vision mode and pretty-prints every frame.
Point the phone at whatever the mode is designed to detect.

Usage:
  uv run python examples/vision_demo.py                  # saliency (default)
  uv run python examples/vision_demo.py --mode animals
  uv run python examples/vision_demo.py --mode text
  uv run python examples/vision_demo.py --mode pose
  uv run python examples/vision_demo.py --mode raw       # raw JPEG size

Available modes (declared in manifest vision_capabilities):
  saliency  — attention hotspots with bounding boxes
  animals   — detected animals with species labels
  text      — recognized text strings with locations
  pose      — human body joint positions (19 COCO keypoints)
  raw       — JPEG frame size/dimensions only (no Vision processing)

Ctrl-C to exit cleanly.
"""
import argparse
import asyncio
import base64
import json

import websockets

WS_URL = "ws://localhost:8765/"
RETRY_DELAY = 5.0


def fmt_bbox(bbox: dict) -> str:
    return f"({bbox['x']:.2f},{bbox['y']:.2f}) {bbox['w']:.2f}×{bbox['h']:.2f}"


def print_frame(msg: dict, mode: str) -> None:
    ts = msg.get("timestamp_ms", 0)

    if mode == "raw":
        data = base64.b64decode(msg.get("frame", ""))
        print(f"  [{ts}ms]  {msg.get('width')}×{msg.get('height')}  {len(data):,} bytes")

    elif mode == "saliency":
        objects = msg.get("salient_objects", [])
        if not objects:
            print(f"  [{ts}ms]  (nothing salient)")
            return
        for obj in objects:
            print(f"  [{ts}ms]  conf={obj['confidence']:.2f}  bbox={fmt_bbox(obj['bbox'])}")

    elif mode == "animals":
        animals = msg.get("animals", [])
        if not animals:
            print(f"  [{ts}ms]  (no animals)")
            return
        for a in animals:
            top = a["labels"][0] if a["labels"] else {}
            label = top.get("identifier", "?")
            lconf = top.get("confidence", 0)
            print(f"  [{ts}ms]  {label} ({lconf:.0%})  det={a['confidence']:.2f}  bbox={fmt_bbox(a['bbox'])}")

    elif mode == "text":
        texts = msg.get("texts", [])
        if not texts:
            print(f"  [{ts}ms]  (no text)")
            return
        for t in texts:
            snippet = t["text"][:60].replace("\n", " ")
            print(f"  [{ts}ms]  conf={t['confidence']:.2f}  \"{snippet}\"  bbox={fmt_bbox(t['bbox'])}")

    elif mode == "pose":
        bodies = msg.get("bodies", [])
        if not bodies:
            print(f"  [{ts}ms]  (no people)")
            return
        for i, body in enumerate(bodies):
            joints = body.get("joints", {})
            present = [name for name, j in joints.items() if j.get("confidence", 0) > 0.5]
            print(f"  [{ts}ms]  body[{i}] conf={body['confidence']:.2f}  "
                  f"{len(joints)} joints  high-conf: {', '.join(present[:6])}"
                  f"{'...' if len(present) > 6 else ''}")

    elif mode == "hand_pose":
        hands = msg.get("hands", [])
        if not hands:
            print(f"  [{ts}ms]  (no hands)")
            return
        for i, hand in enumerate(hands):
            joints = hand.get("joints", {})
            tips = {k: v for k, v in joints.items() if "Tip" in k or k == "wrist"}
            tip_str = "  ".join(f"{k}=({v['x']:.2f},{v['y']:.2f})" for k, v in tips.items())
            print(f"  [{ts}ms]  hand[{i}] {hand.get('chirality')}  conf={hand['confidence']:.2f}  "
                  f"{len(joints)} joints  {tip_str}")


async def session(ws: websockets.ClientConnection, mode: str) -> None:
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert hello.get("type") == "hello", f"unexpected first message: {hello}"

    if not hello.get("phone_connected"):
        print("Phone not connected — will retry")
        return

    manifest = None
    deadline = asyncio.get_event_loop().time() + 5
    while manifest is None:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1)))
        if msg.get("type") == "phone_connected":
            manifest = msg

    if manifest:
        caps = manifest.get("vision_capabilities", [])
        print(f"Device: {manifest.get('device')}  {manifest.get('os')}")
        print(f"  vision_capabilities: {caps}")
        if mode not in ("raw",) and mode not in caps:
            print(f"  WARNING: mode '{mode}' not in vision_capabilities — may not work")

    await ws.send(json.dumps({"type": "register", "name": "vision_demo"}))
    await ws.send(json.dumps({"type": "subscribe", "sensor": "camera", "mode": mode}))
    print(f"\nSubscribed: camera mode={mode!r}  Ctrl-C to exit.\n")

    try:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "phone_hardware" and msg.get("sensor") == "camera":
                if msg.get("mode") == mode:
                    print_frame(msg, mode)
    finally:
        try:
            await ws.send(json.dumps({"type": "unsubscribe", "sensor": "camera"}))
        except Exception:
            pass


async def main(mode: str) -> None:
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await session(ws, mode)
        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            print(f"Disconnected ({e}) — retrying in {RETRY_DELAY:.0f}s...")
            await asyncio.sleep(RETRY_DELAY)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="saliency",
                        choices=["raw", "saliency", "animals", "text", "pose", "hand_pose"],
                        help="Vision mode to subscribe to (default: saliency)")
    args = parser.parse_args()
    asyncio.run(main(args.mode))
