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

## Setup

One-time after clone:

```bash
./install_hooks.sh
```

## Deploying

The deploy pipeline is automatic. Commit to `main.py`, `server.py`, or `bricks/**` and the system is ready before `git commit` returns:

```
DEPLOY:start changed=main.py
...
DEPLOY:success elapsed=20s
```

To bring everything up from scratch (e.g. after a reboot):

```bash
./deploy.sh
```

`deploy.sh` manages the full lifecycle — server restart, hub upload, iOS build and install — in the right order, and blocks until both the phone and hub confirm they're connected.

## Observing

Connect any WebSocket client to `ws://<server-ip>:8765/` to receive hub events:

```json
{"type": "hub_stdout", "data": "button:1"}
```

