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

## Phone hardware protocol

`PhoneHardwareManager` (iOS) publishes phone sensor events to the server as JSON. Events arrive at the server as `phone_hardware` messages and are broadcast to all WebSocket clients.

**Battery** — emitted on server connect and on any change:
```json
{"type": "phone_hardware", "sensor": "battery", "level": 0.87, "state": "unplugged"}
```
- `level`: 0.0–1.0 (`-1.0` if unknown)
- `state`: `"charging"` | `"full"` | `"unplugged"` | `"unknown"`

**Adding new phone sensors**: add a method to `PhoneHardwareManager` that calls `events.send(...)` with `type: "phone_hardware"` and a `sensor` key. Wire notifications or timers in `init()`. No server or hub changes needed.

**Composition pattern**: `server.py` reacts to `phone_hardware` events and can emit hub commands in response. Example — battery state drives hub light color:
```python
if msg.get("type") == "phone_hardware" and msg.get("sensor") == "battery":
    color = {"charging": "GREEN", "full": "GREEN", "unplugged": "RED"}.get(msg.get("state"))
    if color:
        cmd = json.dumps({"target": "hub", "data": f"hub:light:on:{color}"})
        for c in connected_clients:
            c.send(cmd)
```

**Timing**: `emitCurrentState()` fires when the iOS app connects to the server. `server.py` caches the last-known payload per sensor and replays all cached states to clients that connect after the iOS app — late-joining agents always receive an initial snapshot without needing to coordinate timing.

## Connectivity state

The server tracks the live state of both hardware devices and includes it in every `hello` message:

```json
{"type": "hello", "ws_url": "ws://...", "hub_connected": true, "phone_connected": true}
```

**Events** — broadcast to all non-bridge clients as state changes:
```json
{"type": "phone_connected"}
{"type": "phone_disconnected"}
{"type": "hub_connected"}
{"type": "hub_disconnected"}
```

**iOS sends on server connect** (from `AppCoordinator`):
1. `{"type": "phone_connected"}` — identifies this client as the bridge
2. `{"type": "hub_connected"}` — only if `hub.isHubReady == true` at that moment
3. `phone.emitCurrentState()` — replays cached sensor state

**iOS sends on `isHubReady` change**: `hub_connected` when `>ready` arrives on hub stdout; `hub_disconnected` when BLE disconnects.

**`hub_connected` is tied to `>ready`**, not BLE connection — because the dispatch loop isn't accepting commands until `main.py` finishes startup. On hub reconnect, server calls `_recover_subscriptions()` to re-send `hub:stream:start:NAME:INTERVAL` for any active subscribers.

**Bridge identity**: the server marks the client that sends `phone_connected` as `bridge_client`. When that client disconnects, the server clears both `hub_connected` and `phone_connected` and broadcasts `hub_disconnected` + `phone_disconnected` to remaining clients.

## Key design decisions

**Auto-start via STATUS_REPORT**: On BLE subscribe, the hub sends a STATUS_REPORT. iOS reads the USER_PROGRAM_RUNNING flag and only sends START if the program isn't already running. This avoids a GATT BUSY error (0x81) that would disrupt the notification pipeline.

**`releasingForDeploy` vs `releaseBLE()`**: Two separate methods on `HubConnectionManager`.
- `releaseForDeploy()` — called by the `hub_disconnect` server command. Sets `releasingForDeploy = true` so `didDisconnectPeripheral` does not reconnect.
- `releaseBLE()` — called on app background/terminate. Sends STOP + disconnects but does NOT set the flag, so the app reconnects if it returns to foreground.

**iOS as BLE bridge**: Pybricks hubs only advertise BLE; an iPhone acts as an always-on relay to the IP network. The `bluetooth-central` background mode keeps the BLE connection alive even when the app is backgrounded — which is why the explicit release step is needed before pybricksdev can connect.

**Bonjour + cached direct URL**: `ServerConnectionManager` uses NWBrowser (Bonjour) only on first launch. Once the server sends its `hello` message containing `ws_url`, iOS caches that URL and uses URLSession WebSocket directly on all subsequent connects.

**iOS as hardware bridge**: The iOS app has no business logic — it is a hardware bridge. Hub stdout lines are forwarded to the server verbatim; server messages addressed `target: hub` are forwarded to hub stdin verbatim. Phone hardware events (battery, and future sensors) are forwarded as `phone_hardware` messages. All logic lives in `server.py`. The iOS app and hub should rarely need redeployment; only `server.py` changes during iteration.

**Hub as hardware wrapper**: `main.py` is a stable device driver, not an application. It exposes the full Pybricks hardware API (motors, sensors, IMU, display, speaker, light) via a colon-delimited stdin/stdout protocol. Hub logic is limited to dispatching commands and reporting results — no decision-making. This means `main.py` rarely needs to change once the command vocabulary is established.

**BLE stdout line buffer**: iOS buffers WRITE_STDOUT BLE notifications and only emits a complete line when `\n` (0x0A) is received. This handles Pybricks fragmenting long `print()` calls across multiple notifications. Negotiated MTU is 182 bytes; any single line under that fits in one notification.

**Async stdin loop**: `main.py` uses `run_task(multitask(stdin_loop(), stream_loop()))` instead of `input()`. `stdin_loop` reads bytes via `read_input_byte()` (returns `None` when no byte available), assembles lines, and `await`s `dispatch()`. `stream_loop` ticks every 10ms and fires registered emit functions. Both tasks run concurrently via the Pybricks scheduler.

**Pybricks async runtime import**: `run_task`, `multitask`, and `read_input_byte` live in `pybricks.tools`. Import them with `from pybricks.tools import run_task, multitask, read_input_byte`. Do NOT `import pybricks` — that shadows the runtime module and raises `AttributeError: 'module' object has no attribute 'run_task'`. Confirmed empirically.

**`bytearray.decode()` unavailable**: Use `str(buf, "utf-8")` not `buf.decode("utf-8")` in Pybricks MicroPython.

**`>` prefix convention**: All hub output lines are prefixed with `>` by convention. `server.py` drops any `hub_stdout` line that doesn't start with `>` as a safety filter (originally guarded against `input()` echo; now just defensive).

**Blocking motor ops need `await`**: In Pybricks async, `motor.run_time(speed, ms)`, `motor.run_angle(speed, deg)`, `motor.run_target(speed, deg)`, and `hub.speaker.beep(hz, ms)` return awaitables. Calling without `await` starts the operation but returns immediately (non-blocking). Always use `await` in `dispatch()`. With `await`, the task suspends and `multitask` allows `stream_loop` to keep running during the motor op — verified empirically: 20 IMU stream events arrived during a 2-second `run_time`. Max gap was 179ms at 100ms stream interval.

**Safe state on agent exit**: Each agent is responsible for stopping its own hardware in a `finally` block. The server does not intervene — it can't know which motors belong to which agent, and a blanket stop would silently interrupt other agents running concurrently. Example: `await ws.send(json.dumps({"target": "hub", "data": "exec:[m.stop() for m in motors.values()]"}))`

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
motor:PORT:done                    →  >motor:PORT:done=True|False  (True when no async operation is running)
motor:PORT:load                    →  >motor:PORT:load=INT         (mNm; 0 when stopped)
motor:PORT:dc:POWER                →  >motor:PORT:running          (non-blocking; POWER -100 to 100%; no encoder control)
motor:PORT:brake                   →  >motor:PORT:braked           (active electrical braking; resists motion)
motor:PORT:hold                    →  >motor:PORT:held             (PID position hold at current angle)
motor:PORT:stop                    →  >motor:PORT:stopped
sensor:PORT:distance               →  >sensor:PORT:distance=INT    (sonic; 2000 = nothing detected)
sensor:PORT:presence               →  >sensor:PORT:presence=BOOL   (sonic; True if another Pybricks hub is broadcasting ultrasonically)
sensor:PORT:color                  →  >sensor:PORT:color=Color.NAME
sensor:PORT:ambient                →  >sensor:PORT:ambient=FLOAT   (color; ambient light level)
sensor:PORT:reflection             →  >sensor:PORT:reflection=INT  (color; surface reflectivity 0-100)
sensor:PORT:hsv                    →  >sensor:PORT:hsv:h=INT:s=INT:v=INT  (color; hue 0-359, saturation 0-100, value 0-100)
sensor:PORT:lights:on              →  >sensor:PORT:lights:done     (sensor housing LEDs on at full brightness; sonic and color)
sensor:PORT:lights:on:BRIGHTNESS   →  >sensor:PORT:lights:done     (BRIGHTNESS 0-100)
sensor:PORT:lights:off             →  >sensor:PORT:lights:done
```

**Hub internals:**
```
hub:imu:ready                →  >hub:imu:ready=True|False  (False until hub sits still ~2s after start)
hub:imu:tilt                 →  >hub:imu:tilt:pitch=FLOAT:roll=FLOAT
hub:imu:heading              →  >hub:imu:heading=FLOAT
hub:imu:rotation:AXIS        →  >hub:imu:rotation=FLOAT  (AXIS: X/Y/Z; unbounded cumulative degrees, unlike heading)
hub:imu:reset_heading:ANGLE  →  >hub:imu:heading_reset   (sets heading reference to ANGLE degrees)
hub:imu:acceleration         →  >hub:imu:acceleration:x=FLOAT:y=FLOAT:z=FLOAT  (mm/s²; z≈9800 when flat)
hub:imu:angular_velocity     →  >hub:imu:angular_velocity:x=FLOAT:y=FLOAT:z=FLOAT  (deg/s)
hub:imu:up                   →  >hub:imu:up=Side.NAME
hub:imu:stationary           →  >hub:imu:stationary=True|False  (noisy — can read False even when still)
hub:battery:voltage          →  >hub:battery:voltage=INT         (mV)
hub:battery:current          →  >hub:battery:current=INT         (mA)
hub:battery:temperature      →  >hub:battery:temperature=INT     (millidegrees C; divide by 1000 for °C)
hub:battery:type             →  >hub:battery:type=STR            (e.g. "Li-ion")
hub:charger:connected        →  >hub:charger:connected=INT       (0 = not connected, 1 = connected)
hub:charger:current          →  >hub:charger:current=INT         (mA; small residual even when unplugged)
hub:charger:status           →  >hub:charger:status=INT          (0 = not charging; other values TBD)
hub:buttons:pressed          →  >hub:buttons:pressed=none|BUTTON:BUTTON...
hub:system:info              →  >hub:system:info:name=STR:reset_reason=INT:host_connected=BOOL:start_type=INT
hub:system:name              →  >hub:system:name=STR             (hub's Bluetooth display name)
hub:ble:version              →  >hub:ble:version=STR
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
hub:light:blink:COLOR:ON_MS:OFF_MS →  >hub:light:done   (non-blocking, loops until on/off called)
hub:light:off                     →  >hub:light:done
hub:stream:start:NAME:INTERVAL_MS →  >hub:stream:started:NAME
hub:stream:stop:NAME              →  >hub:stream:stopped:NAME
```

**Command channel concurrency**: The hub processes one command at a time. Blocking commands (`run:SPEED:DURATION`, `run_angle`, `run_target`, `speaker:beep`) suspend `stdin_loop` until they finish — no further commands are processed during that window. Stream subscriptions are unaffected (they run in a separate `stream_loop` task). If you need concurrent motor activity and new commands, use non-blocking forms: `motor:PORT:run:SPEED` to start, then `motor:PORT:stop` or `motor:PORT:run_angle` later to change behavior.

**Hub streaming**: `hub:stream:start:NAME:INTERVAL_MS` registers an emit function that fires every INTERVAL_MS. Output lines are `>stream:NAME:key=val:key=val...` on hub stdout. The server intercepts these (they never reach the generic broadcast), parses them into `hub_stream` JSON, and routes to subscribers. Subscribe with `{"type": "subscribe", "sensor": "hub:NAME"}`. Available streams:
- `imu` — `pitch`, `roll`, `heading` (floats, degrees); emits `>stream:imu:pitch=F:roll=F:heading=F`

Server routing: `{"type": "subscribe", "sensor": "hub:imu"}` → server sends `hub:stream:start:imu:100` to hub. Hub starts emitting. Server parses `>stream:imu:...` lines and sends `{"type": "hub_stream", "sensor": "hub:imu", "pitch": F, "roll": F, "heading": F}` to subscribers only.

**DriveBase** (exec-only — not in structured protocol):
```python
from pybricks.robotics import DriveBase
# Guard against EBUSY: DriveBase takes exclusive motor ownership; reuse existing instance if present
_d = globals().get('db')
db = _d if hasattr(_d, 'reset') else DriveBase(motors['LEFT'], motors['RIGHT'], wheel_diameter=MM, axle_track=MM)

db.straight(distance_mm)           # blocking; ±1mm accuracy empirically
db.turn(angle_deg)                 # blocking; ±0.5° with wheel odometry
db.arc(radius_mm, angle=deg)       # blocking; arc by angle (keyword-only)
db.arc(radius_mm, distance=mm)     # blocking; arc by distance (keyword-only); mutually exclusive with angle=
db.curve(radius_mm, angle_deg)     # blocking; positional args; legacy alias for arc(radius, angle=angle)
db.drive(speed_mm_s, turn_rate)    # non-blocking continuous drive
db.brake()                         # non-blocking; active electrical braking (sheds speed faster than stop)
db.stop()                          # non-blocking; passive coast
db.reset()                         # zero odometry counters
db.distance()                      # → int mm (cumulative, signed)
db.angle()                         # → float deg (cumulative, signed)
db.state()                         # → (distance_mm, speed_mm_s, angle_deg, turn_rate_deg_s)
db.done()                          # → bool; False while wait=False command is running
db.stalled()                       # → bool; False in all tested cases
db.settings()                      # → (straight_speed, straight_accel, turn_rate, turn_accel) = (300,500,180,360) defaults
db.settings(straight_speed=N, straight_acceleration=N, turn_rate=N, turn_acceleration=N)
db.use_gyro(True)                  # enable IMU-based heading; setter only (no getter)
                                   # ⚠️ check hub.imu.ready() first — inaccurate for ~2s after hub restart
db.heading_control                 # Control object (same API as motor.control): pid(), limits(), etc.
db.distance_control                # Control object for the straight-line PID
```
**DriveBase gotchas:**
- `import pybricks.robotics; dir(pybricks.robotics)` fails in MicroPython — use `from pybricks.robotics import DriveBase` directly
- `GyroDriveBase` does not exist as a separate class; use `db.use_gyro(True)` instead
- Motor ownership is exclusive: creating a second DriveBase with the same motors raises `[Errno 16] EBUSY`. Always use the `globals().get()` guard above when creating from exec()
- `straight(wait=False)` launches non-blocking; read `db.done()` in a subsequent exec() to poll completion

**Car** (Ackermann/car-style steering — exec-only):
```python
from pybricks.robotics import Car
# drive_motors can be a single Motor or a list of Motors (multi-motor rear axle)
car = Car(steer_motor=motors['A'], drive_motors=motors['B'])
car = Car(steer_motor=motors['A'], drive_motors=[motors['B'], motors['C']])

car.steer(angle)          # set steering angle
car.drive_speed(speed)    # set drive speed
car.drive_power(power)    # set drive power
```
**Car gotcha:** Calibrates steering on construction by running the steer motor to both hard stops to find center. Raises `"The steering mechanism has no end stop. Did you build a car yet?"` if the motor has no physical end stops. Requires a real car build — cannot be tested with free-hanging motors.

**SpikeBase** (SPIKE Prime / Inventor Hub differential drive — exec-only):
```python
from pybricks.robotics import SpikeBase
sb = SpikeBase(left_motor=motors['A'], right_motor=motors['B'])

# Tank mode — independent left/right speed control (power-based, no odometry)
sb.tank_move_forever(speed_left, speed_right)
sb.tank_move_for_time(speed_left, speed_right, time_ms)
sb.tank_move_for_degrees(speed_left, speed_right, angle)

# Steering mode — unified speed + differential steering
sb.steering_move_forever(speed, steering)
sb.steering_move_for_time(speed, steering, time_ms)
sb.steering_move_for_degrees(speed, steering, angle)

sb.stop()
```
**SpikeBase vs DriveBase:** No wheel geometry, no odometry, no PID closed-loop. Pure power commands (percentages, not mm/s). `steering` maps to left/right speed differential. Simpler — use when you don't know or don't care about wheel geometry.

**Exec-only capabilities** (not in structured protocol):
- `hub.display.icon(Matrix([[...], ...]))` — display a custom 5x5 image; import `Matrix` from `pybricks.tools`
- `hub.display.animate(images, interval=MS)` — ⚠️ loops forever, permanently blocks the command loop
- `hub.light.animate(colors, interval=MS)` — non-blocking (unlike display.animate); loops until stopped with on()/off()
- `hub.speaker.play_notes(notes, tempo)` — blocking; e.g. `play_notes(['C4/4', 'E4/4', 'G4/4'], tempo=120)`
- `hub.imu.orientation()` — returns a 3×3 rotation Matrix; ⚠️ Matrix.__str__ is multiline and will truncate through the exec pipeline (only first line survives). Use individual tilt/heading/up instead.
- `hub.imu.settings()` — returns calibration tuple; read-only diagnostics
- `motor.control` — Control object with pid(), limits(), stall_tolerances(), target_tolerances(), trajectory(); use for PID tuning via exec()
- `motor.settings()` — returns (max_voltage_mV,) tuple; e.g. (9000,) = 9 V cap
- `motor.run_until_stalled(speed)` — runs until motor stalls; useful for finding mechanical limits
- `sensor.detectable_colors()` — returns tuple of Color constants the color sensor will classify
- `hub.system.storage(offset, read=N)` / `storage(offset, write=b'...')` — raw persistent byte buffer (flash). First arg is a byte offset, not a key. Read N bytes: returns bytes object. Write: value must be bytes (use `struct.pack`). String values and None are rejected. No delete — overwrite with zeros to clear. Useful for persisting calibration across restarts.
- `hub.ble.signal_strength(channel)` — requires a BLE observe/broadcast channel to be configured first via `observe_enable()`; not useful standalone
- `hub.ble.broadcast/observe/observe_enable` — multi-hub communication; unexplored

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

## Testing

```bash
./tests/run_integration.sh
```

Requires `server.py` running, hub connected via iOS app, iPhone attached. Runs seam contracts first (fast structural check), then the multi-client hardware suite.

**`tests/seam_check.py`** — contract tests, one per interface seam. The test IS the contract: if all pass, the full stack can function end-to-end.

| Seam | What it proves |
|------|----------------|
| 1. Server hello | `{type: "hello", ws_url}` is the first message on connect |
| 2. Hub exec ok | BLE bridge live, `>` prefix, exec handler, `>exec:ok` terminal |
| 2. Hub exec error | `>exec:error:MESSAGE` terminal on raised exception |
| 3. Hub stdout schema | `{type: "hub_stdout", data}` field contract |
| 4. Hub streaming | subscribe → `>hub:stream:started` → `hub_stream{pitch,roll,heading: float}` → unsubscribe → `>hub:stream:stopped` |
| 5. Server state delivery | Fresh client receives cached `phone_hardware:battery` immediately after hello |

**`tests/test_hardware_multi.py`** — multi-client behavioral scenarios (~30s).

| Scenario | What it proves |
|----------|----------------|
| A. Queue isolation | Slow client cannot delay fast client; ≥85% frames at target rate |
| B. Cross-device interleave | `hub_stream` and `phone_hardware` arrive in the same client |
| C. Command during stream | `hub:light` responds while `hub:imu` streaming; stream continues |
| D. Crash recovery | Abrupt client close triggers safe-state stop to surviving clients |

**Adding new tests**: add to `seam_check.py` when a new protocol boundary is established (new message type, new field, new subscription behavior). Add to `test_hardware_multi.py` when the behavior requires concurrent clients or timing measurement. Run `./tests/run_integration.sh` before merging any change to `server.py` or `main.py`.

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
tests/
  seam_check.py                      Contract tests for all 5 interface seams
  test_hardware_multi.py             Multi-client + real-hardware integration scenarios
  test_queue_isolation.py            Per-client queue isolation stress test
  run_integration.sh                 Single-command runner (seam_check → test_hardware_multi)
examples/
  tilt_drive.py                      iPhone tilt → motor speed control script
  control.py                         Basic hub control example
  control_exec.py                    Exec-based control example
  diagnose.py                        Hardware diagnostic script
scratch/
  explore_*.py                       Exploratory one-off scripts (not tests)
bricks/bricks/
  bricksApp.swift                    App entry; owns HubConnectionManager + ServerConnectionManager + PhoneHardwareManager
  ContentView.swift                  UI; wires hub stdout → server.send() and server commands → hub actions
  HubConnection.swift                CoreBluetooth hub manager
  ServerConnection.swift             WebSocket server manager
  PhoneHardware.swift                Phone sensor manager — publishes phone_hardware events to server
```

Control scripts (like `examples/tilt_drive.py`) are the natural unit of agent behavior: connect to `ws://localhost:8765/`, subscribe to sensor streams, run logic, send hub commands, clean up on exit. No deploy needed — pure server-side Python.
