# learning-pybricks

An agentic coding evaluation platform built around a physical LEGO MINDSTORMS Inventor Hub running Pybricks MicroPython.

The core idea: AI agents can write code, deploy it to real hardware, and observe the results automatically — closing the loop between code generation and physical evaluation.

## Architecture

```
[LEGO Hub] <--BLE--> [iOS App] <--WebSocket--> [Python Server] <-- agents / tooling
  main.py              bricks/                    server.py
```

- **Hub** (`main.py`): Pybricks MicroPython. Outputs events via `print()`, which the firmware forwards over BLE.
- **iOS app** (`bricks/`): SwiftUI app on iPhone. BLE bridge — connects to the hub via CoreBluetooth and relays hub events to the server over WebSocket.
- **Server** (`server.py`): asyncio WebSocket server, discoverable via Bonjour/mDNS. The observation and control point: hub stdout arrives as structured JSON. Also exposes a TCP control port (8766) used by the deploy pipeline.

## Requirements

- LEGO MINDSTORMS Inventor Hub (51515) running [Pybricks firmware](https://pybricks.com)
- iPhone with Bluetooth
- Mac with Xcode
- Python 3.10+ and [uv](https://docs.astral.sh/uv/)

## Running

Start the server (keep it running):

```bash
uv run python server.py
```

The iOS app discovers the server automatically via Bonjour on first launch and caches the direct URL for fast reconnects.

## Deploying

```bash
./deploy.sh
```

Requires the server to be running and the iOS app to be connected. The script:
1. Signals the iOS app via the server to stop the hub program and release BLE
2. Uploads new hub code via `pybricksdev`
3. Builds and installs the new iOS app via Xcode + `devicectl`
4. Launches the app — it reconnects to the hub and auto-starts the program

## Observing

Connect any WebSocket client to `ws://<server-ip>:8765/` to receive hub events:

```json
{"type": "hub_stdout", "data": "button:1"}
```
