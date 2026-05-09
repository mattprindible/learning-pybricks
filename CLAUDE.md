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
        await asyncio.gather(*(c.send(cmd) for c in connected_clients))
```

**Timing**: `emitCurrentState()` fires when the iOS app connects to the server, so agents always receive an initial snapshot. Subscribe before the iOS app connects, or trigger re-emission via a `{"type": "phone_state"}` command (planned).

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
```

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
  bricksApp.swift                    App entry; owns HubConnectionManager + ServerConnectionManager + PhoneHardwareManager
  ContentView.swift                  UI; wires hub stdout → server.send() and server commands → hub actions
  HubConnection.swift                CoreBluetooth hub manager
  ServerConnection.swift             WebSocket server manager
  PhoneHardware.swift                Phone sensor manager — publishes phone_hardware events to server
```
