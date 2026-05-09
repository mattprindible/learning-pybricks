# CLAUDE.md

Agentic coding eval platform. Physical LEGO hub is the ground truth — agents modify code, deploy, and observe real hardware behavior.

## Stack

```
main.py          Pybricks MicroPython on LEGO Inventor Hub
bricks/          SwiftUI iOS app — BLE bridge between hub and server
server.py        asyncio WebSocket server — observation + control point
deploy.sh        One-command deploy pipeline
```

## Running the system

The server must be running before deploy works:

```bash
uv run python server.py
```

Then deploy everything (hub code + iOS app) with:

```bash
./deploy.sh
```

## Deploy sequence

1. `deploy.sh` sends `hub_disconnect\n` to `localhost:8766` (control TCP port)
2. `server.py` receives it and broadcasts `{"type": "hub_disconnect"}` to all WebSocket clients
3. iOS app receives the message, calls `hub.releaseForDeploy()`: sends STOP_USER_PROGRAM (`0x00`) over BLE, then `cancelPeripheralConnection`. Sets `releasingForDeploy = true` so the normal reconnect loop does not fire.
4. `pybricksdev` uploads new `main.py` to hub over BLE
5. Xcode builds + installs new iOS app via `devicectl`
6. New iOS app launches with fresh state (`releasingForDeploy = false`), reconnects to hub, auto-starts the program

If `server.py` is not running when `deploy.sh` runs, it will warn and the pybricksdev step will likely fail because the hub is still BLE-connected to iOS.

## Hub protocol

Hub emits events via `print()` → Pybricks BLE WRITE_STDOUT notification (event type `0x01`) → iOS decodes UTF-8, strips whitespace → server receives:

```json
{"type": "hub_stdout", "data": "<line>"}
```

Pybricks BLE command/event characteristic (`c5f50002-...`):
- Event `0x00` = STATUS_REPORT: 4-byte LE flags, bit 6 = USER_PROGRAM_RUNNING
- Event `0x01` = WRITE_STDOUT: remaining bytes = UTF-8
- Command `0x00` = STOP_USER_PROGRAM
- Command `0x01` = START_USER_PROGRAM

## Key design decisions

**Auto-start via STATUS_REPORT**: On BLE subscribe, the hub sends a STATUS_REPORT. iOS reads the USER_PROGRAM_RUNNING flag and only sends START if the program isn't already running. This avoids a GATT BUSY error (0x81) that would disrupt the notification pipeline.

**`releasingForDeploy` vs `releaseBLE()`**: Two separate methods on `HubConnectionManager`.
- `releaseForDeploy()` — called by the `hub_disconnect` server command. Sets `releasingForDeploy = true` so `didDisconnectPeripheral` does not reconnect.
- `releaseBLE()` — called on app background/terminate. Sends STOP + disconnects but does NOT set the flag, so the app reconnects if it returns to foreground.

**iOS as BLE bridge**: Pybricks hubs only advertise BLE; an iPhone acts as an always-on relay to the IP network. The `bluetooth-central` background mode keeps the BLE connection alive even when the app is backgrounded — which is why the explicit release step is needed before pybricksdev can connect.

**Bonjour + cached direct URL**: `ServerConnectionManager` uses NWBrowser (Bonjour) only on first launch. Once the server sends its `hello` message containing `ws_url`, iOS caches that URL and uses URLSession WebSocket directly on all subsequent connects.

**iOS as thin BLE bridge**: The iOS app has no business logic — it is a hardware bridge. Hub stdout lines are forwarded to the server verbatim; server messages addressed `target: hub` are forwarded to hub stdin verbatim. All logic lives in `server.py`. The iOS app and hub should rarely need redeployment; only `server.py` and `main.py` change during iteration.

**Hub as hardware wrapper**: `main.py` is a stable device driver, not an application. It exposes the full Pybricks hardware API (motors, sensors, IMU, display, speaker, light) via a colon-delimited stdin/stdout protocol. Hub logic is limited to dispatching commands and reporting results — no decision-making. This means `main.py` rarely needs to change once the command vocabulary is established.

**BLE stdout line buffer**: iOS buffers WRITE_STDOUT BLE notifications and only emits a complete line when `\n` (0x0A) is received. This handles Pybricks fragmenting long `print()` calls across multiple notifications. Negotiated MTU is 182 bytes; any single line under that fits in one notification.

**`input()` is the only stdin mechanism**: `sys.stdin` is unavailable in Pybricks MicroPython. `input()` works but blocks until `\r\n` arrives and echoes the received line back to stdout at the firmware level. The server filters echoes via the `>` prefix convention (lines not starting with `>` are dropped).

**Safe state on client disconnect**: `server.py` broadcasts `exec:[m.stop() for m in motors.values()]` to all remaining clients whenever any client disconnects. This ensures hardware stops if a controller crashes or exits uncleanly. Control scripts should also send a stop in their `finally` block as a belt-and-suspenders measure.

**`exec()` works in Pybricks MicroPython**: `exec(code, globals())` executes arbitrary Python with full access to the hub's runtime globals. This makes `main.py` a live REPL over WebSocket — new hub behaviours can be sent as code strings without redeploying. Discovered empirically 2026-05-09.

## Hub command protocol

`main.py` accepts commands via stdin and responds via stdout. Commands are colon-delimited; responses are prefixed with `>`.

**External devices** (port-addressed):
```
motor:PORT:run:SPEED                →  >motor:PORT:running          (non-blocking, runs until stopped)
motor:PORT:run:SPEED:DURATION_MS   →  >motor:PORT:done:angle=INT   (blocking)
motor:PORT:run_angle:SPEED:ANGLE   →  >motor:PORT:done:angle=INT   (blocking; rotates by ANGLE° from current position; ±1° at 500°/s)
motor:PORT:run_target:SPEED:ANGLE  →  >motor:PORT:done:angle=INT   (blocking; absolute position; idempotent if already there)
motor:PORT:reset_angle:N           →  >motor:PORT:angle_reset
motor:PORT:angle                   →  >motor:PORT:angle=INT
motor:PORT:speed                   →  >motor:PORT:speed=INT        (reads ~0 for ~400ms after run(); may overshoot by ~10°/s)
motor:PORT:stop                    →  >motor:PORT:stopped
sensor:PORT:distance              →  >sensor:PORT:distance=INT   (2000 = nothing detected)
sensor:PORT:color                 →  >sensor:PORT:color=Color.NAME
```

**Hub internals:**
```
hub:imu:ready            →  >hub:imu:ready=True|False  (False until hub sits still ~2s after start)
hub:imu:tilt             →  >hub:imu:tilt:pitch=FLOAT:roll=FLOAT
hub:imu:heading          →  >hub:imu:heading=FLOAT
hub:imu:acceleration     →  >hub:imu:acceleration:x=FLOAT:y=FLOAT:z=FLOAT  (mm/s²; z≈9800 when flat)
hub:imu:angular_velocity →  >hub:imu:angular_velocity:x=FLOAT:y=FLOAT:z=FLOAT  (deg/s)
hub:imu:up               →  >hub:imu:up=Side.NAME
hub:imu:stationary       →  >hub:imu:stationary=True|False  (noisy — can read False even when still)
hub:battery:voltage      →  >hub:battery:voltage=INT  (mV)
hub:battery:current      →  >hub:battery:current=INT  (mA)
hub:buttons:pressed      →  >hub:buttons:pressed=none|BUTTON:BUTTON...
hub:system:info          →  >hub:system:info:name=STR:reset_reason=INT:host_connected=BOOL:start_type=INT
hub:ble:version          →  >hub:ble:version=STR
hub:display:number:N              →  >hub:display:done  (N: -99 to 99)
hub:display:char:C                →  >hub:display:done
hub:display:text:STR              →  >hub:display:done  (STR may contain colons)
hub:display:on:BRIGHTNESS         →  >hub:display:done  (all pixels; 0-100)
hub:display:pixel:ROW:COL:BRIGHTNESS  →  >hub:display:done  (row/col 0-4; brightness 0-100; 0 = off)
hub:display:orientation:SIDE      →  >hub:display:done  (TOP/BOTTOM/LEFT/RIGHT/FRONT/BACK; default TOP)
hub:display:off                   →  >hub:display:done
hub:speaker:beep:HZ:MS            →  >hub:speaker:done
hub:speaker:volume:PCT            →  >hub:speaker:done  (0–100)
hub:light:on:COLOR                →  >hub:light:done    (RED/GREEN/BLUE/YELLOW/ORANGE/CYAN/MAGENTA/VIOLET/WHITE/GRAY/BLACK)
hub:light:off                     →  >hub:light:done
```

**Display exec-only capabilities** (not in structured protocol):
- `hub.display.icon(Matrix([[...], ...]))` — display a custom 5x5 image; import `Matrix` from `pybricks.tools`
- `hub.display.animate(images, interval=MS)` — ⚠️ loops forever, permanently blocks the command loop; do not call from exec() without a way to stop the hub program externally

**Value types:** Motor and sensor values are integers. IMU values (tilt, heading, acceleration, angular_velocity) are floats. Use `float()` not `int()` when parsing IMU responses server-side.

**Startup events** (emitted once before `>ready`):
```
>mtu:INT           — negotiated BLE MTU payload size in bytes
>port:X=LABEL      — device on each port (or "none")
>ready             — hub is accepting commands
```

**Exec interface:**
```
exec:PYTHON_EXPRESSION  →  any print() output, then >exec:ok or >exec:error:MESSAGE
```
`exec` runs arbitrary Python in the hub's global namespace — `hub`, `motors`, `sensors`, `Color`, etc. are all in scope. Use for anything not covered by the structured protocol, or to avoid deploying new hub code for one-off operations.

Multi-output responses: collect lines until `>exec:` prefix appears (that's the terminal marker). Example: `exec:print(">d:" + str(sensors["E"][1].distance()))` emits `>d:288` then `>exec:ok`.

**Errors:** `>error:unknown:ORIGINAL_COMMAND`

## Agent integration points

- **Observe hub events**: Connect a WebSocket client to `ws://<server-ip>:8765/`
- **Trigger deploy**: Run `./deploy.sh`
- **Stop hub program only**: Send `hub_disconnect\n` to `localhost:8766`

## File structure

```
main.py                              Hub program (edit to change hub behavior)
server.py                            WebSocket + control server
deploy.sh                            Deploy pipeline
pyproject.toml                       Python deps
uv.lock                              Locked deps
bricks/bricks/
  bricksApp.swift                    App entry; owns HubConnectionManager + ServerConnectionManager
  ContentView.swift                  UI; wires hub stdout → server.send() and server commands → hub actions
  HubConnection.swift                CoreBluetooth hub manager
  ServerConnection.swift             WebSocket server manager
```
