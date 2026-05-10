from pybricks.hubs import InventorHub
from pybricks.iodevices import PUPDevice
from pybricks.pupdevices import Motor, UltrasonicSensor, ColorSensor
from pybricks.parameters import Axis, Color, Port, Side
from pybricks.tools import wait, run_task, multitask, read_input_byte

COLORS = {
    "RED": Color.RED, "GREEN": Color.GREEN, "BLUE": Color.BLUE,
    "YELLOW": Color.YELLOW, "ORANGE": Color.ORANGE, "CYAN": Color.CYAN,
    "MAGENTA": Color.MAGENTA, "VIOLET": Color.VIOLET, "WHITE": Color.WHITE,
    "GRAY": Color.GRAY, "BLACK": Color.BLACK, "NONE": Color.NONE,
}

SIDES = {
    "TOP": Side.TOP, "BOTTOM": Side.BOTTOM, "LEFT": Side.LEFT,
    "RIGHT": Side.RIGHT, "FRONT": Side.FRONT, "BACK": Side.BACK,
}

AXES = {
    "X": Axis.X, "Y": Axis.Y, "Z": Axis.Z,
}

DEVICE_NAMES = {
    1: "WeDo2Motor",
    2: "WeDo2Tilt",
    8: "ColorDist",
    34: "MotorL",
    38: "AngMotorM",
    46: "AngMotorL",
    48: "MotorM",
    49: "MotorL",
    61: "Color",
    62: "Sonic",
    63: "Force",
    75: "AngMotorM",
    76: "AngMotorL",
}

MOTOR_IDS = {34, 38, 46, 48, 49, 75, 76}
SONIC_IDS  = {62}
COLOR_IDS  = {61}

PORT_MAP = [("A", Port.A), ("B", Port.B), ("C", Port.C),
            ("D", Port.D), ("E", Port.E), ("F", Port.F)]

hub = InventorHub()
hub.light.on(Color.GREEN)

motors  = {}
sensors = {}

for name, port in PORT_MAP:
    try:
        dev = PUPDevice(port)
        type_id = dev.info()["id"]
        label = DEVICE_NAMES.get(type_id, "unknown:" + str(type_id))
        print(">port:" + name + "=" + label)
        if type_id in MOTOR_IDS:
            motors[name] = Motor(port)
        elif type_id in SONIC_IDS:
            sensors[name] = ("sonic", UltrasonicSensor(port))
        elif type_id in COLOR_IDS:
            sensors[name] = ("color", ColorSensor(port))
    except OSError:
        print(">port:" + name + "=none")

print(">ready")


def _emit_imu():
    pitch, roll = hub.imu.tilt()
    heading = hub.imu.heading()
    print(">stream:imu:pitch=" + str(pitch) + ":roll=" + str(roll) + ":heading=" + str(heading))


_STREAM_FNS = {"imu": _emit_imu}
_streams = {}


async def dispatch(cmd):
    if not cmd:
        return
    parts = cmd.split(":")

    kind = parts[0] if len(parts) > 0 else ""
    port = parts[1] if len(parts) > 1 else ""

    if kind == "motor" and port in motors:
        action = parts[2] if len(parts) > 2 else ""
        m = motors[port]
        if action == "run" and len(parts) == 4:
            m.run(int(parts[3]))
            print(">motor:" + port + ":running")
        elif action == "run" and len(parts) == 5:
            speed    = int(parts[3])
            duration = int(parts[4])
            m.reset_angle(0)
            await m.run_time(speed, duration)
            print(">motor:" + port + ":done:angle=" + str(m.angle()))
        elif action == "run_angle" and len(parts) == 5:
            await m.run_angle(int(parts[3]), int(parts[4]))
            print(">motor:" + port + ":done:angle=" + str(m.angle()))
        elif action == "run_target" and len(parts) == 5:
            await m.run_target(int(parts[3]), int(parts[4]))
            print(">motor:" + port + ":done:angle=" + str(m.angle()))
        elif action == "reset_angle" and len(parts) == 4:
            m.reset_angle(int(parts[3]))
            print(">motor:" + port + ":angle_reset")
        elif action == "angle":
            print(">motor:" + port + ":angle=" + str(m.angle()))
        elif action == "speed":
            print(">motor:" + port + ":speed=" + str(m.speed()))
        elif action == "done":
            print(">motor:" + port + ":done=" + str(m.done()))
        elif action == "load":
            print(">motor:" + port + ":load=" + str(m.load()))
        elif action == "dc" and len(parts) == 4:
            m.dc(int(parts[3]))
            print(">motor:" + port + ":running")
        elif action == "brake":
            m.brake()
            print(">motor:" + port + ":braked")
        elif action == "hold":
            m.hold()
            print(">motor:" + port + ":held")
        elif action == "stop":
            m.stop()
            print(">motor:" + port + ":stopped")
        else:
            print(">error:unknown:" + cmd)

    elif kind == "sensor" and port in sensors:
        stype, dev = sensors[port]
        action = parts[2] if len(parts) > 2 else ""
        if stype == "sonic":
            if action == "presence":
                print(">sensor:" + port + ":presence=" + str(dev.presence()))
            elif action == "lights" and len(parts) > 3 and parts[3] == "on":
                dev.lights.on(int(parts[4]) if len(parts) == 5 else 100)
                print(">sensor:" + port + ":lights:done")
            elif action == "lights" and len(parts) > 3 and parts[3] == "off":
                dev.lights.off()
                print(">sensor:" + port + ":lights:done")
            else:
                print(">sensor:" + port + ":distance=" + str(dev.distance()))
        elif stype == "color":
            if action == "ambient":
                print(">sensor:" + port + ":ambient=" + str(dev.ambient()))
            elif action == "reflection":
                print(">sensor:" + port + ":reflection=" + str(dev.reflection()))
            elif action == "hsv":
                c = dev.hsv()
                print(">sensor:" + port + ":hsv:h=" + str(c.h) + ":s=" + str(c.s) + ":v=" + str(c.v))
            elif action == "lights" and len(parts) > 3 and parts[3] == "on":
                dev.lights.on(int(parts[4]) if len(parts) == 5 else 100)
                print(">sensor:" + port + ":lights:done")
            elif action == "lights" and len(parts) > 3 and parts[3] == "off":
                dev.lights.off()
                print(">sensor:" + port + ":lights:done")
            else:
                print(">sensor:" + port + ":color=" + str(dev.color()))

    elif kind == "hub":
        subsystem = parts[1] if len(parts) > 1 else ""
        action    = parts[2] if len(parts) > 2 else ""

        if subsystem == "system":
            if action == "info":
                info = hub.system.info()
                print(">hub:system:info:name=" + str(info.get("name", "")) +
                      ":reset_reason=" + str(info.get("reset_reason", "")) +
                      ":host_connected=" + str(info.get("host_connected_ble", "")) +
                      ":start_type=" + str(info.get("program_start_type", "")))
            elif action == "name":
                print(">hub:system:name=" + str(hub.system.name()))
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "ble":
            if action == "version":
                print(">hub:ble:version=" + str(hub.ble.version()))
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "imu":
            if action == "ready":
                print(">hub:imu:ready=" + str(hub.imu.ready()))
            elif action == "tilt":
                pitch, roll = hub.imu.tilt()
                print(">hub:imu:tilt:pitch=" + str(pitch) + ":roll=" + str(roll))
            elif action == "heading":
                print(">hub:imu:heading=" + str(hub.imu.heading()))
            elif action == "acceleration":
                x, y, z = hub.imu.acceleration()
                print(">hub:imu:acceleration:x=" + str(x) + ":y=" + str(y) + ":z=" + str(z))
            elif action == "angular_velocity":
                x, y, z = hub.imu.angular_velocity()
                print(">hub:imu:angular_velocity:x=" + str(x) + ":y=" + str(y) + ":z=" + str(z))
            elif action == "up":
                print(">hub:imu:up=" + str(hub.imu.up()))
            elif action == "stationary":
                print(">hub:imu:stationary=" + str(hub.imu.stationary()))
            elif action == "rotation" and len(parts) == 4:
                axis = AXES.get(parts[3].upper())
                if axis is not None:
                    print(">hub:imu:rotation=" + str(hub.imu.rotation(axis)))
                else:
                    print(">error:unknown_axis:" + parts[3])
            elif action == "reset_heading" and len(parts) == 4:
                hub.imu.reset_heading(float(parts[3]))
                print(">hub:imu:heading_reset")
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "battery":
            if action == "voltage":
                print(">hub:battery:voltage=" + str(hub.battery.voltage()))
            elif action == "current":
                print(">hub:battery:current=" + str(hub.battery.current()))
            elif action == "temperature":
                print(">hub:battery:temperature=" + str(hub.battery.temperature()))
            elif action == "type":
                print(">hub:battery:type=" + str(hub.battery.type()))
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "buttons":
            if action == "pressed":
                pressed = hub.buttons.pressed()
                val = ":".join(str(b) for b in pressed) if pressed else "none"
                print(">hub:buttons:pressed=" + val)
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "display":
            if action == "number" and len(parts) == 4:
                hub.display.number(int(parts[3]))
                print(">hub:display:done")
            elif action == "char" and len(parts) == 4:
                hub.display.char(parts[3])
                print(">hub:display:done")
            elif action == "text" and len(parts) >= 4:
                hub.display.text(":".join(parts[3:]))
                print(">hub:display:done")
            elif action == "on" and len(parts) == 4:
                hub.display.on(int(parts[3]))
                print(">hub:display:done")
            elif action == "pixel" and len(parts) == 6:
                hub.display.pixel(int(parts[3]), int(parts[4]), int(parts[5]))
                print(">hub:display:done")
            elif action == "orientation" and len(parts) == 4:
                side = SIDES.get(parts[3].upper())
                if side is not None:
                    hub.display.orientation(side)
                    print(">hub:display:done")
                else:
                    print(">error:unknown_side:" + parts[3])
            elif action == "off":
                hub.display.off()
                print(">hub:display:done")
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "speaker":
            if action == "beep" and len(parts) == 5:
                await hub.speaker.beep(int(parts[3]), int(parts[4]))
                print(">hub:speaker:done")
            elif action == "volume" and len(parts) == 4:
                hub.speaker.volume(int(parts[3]))
                print(">hub:speaker:done")
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "light":
            if action == "on" and len(parts) == 4:
                color = COLORS.get(parts[3].upper())
                if color is not None:
                    hub.light.on(color)
                    print(">hub:light:done")
                else:
                    print(">error:unknown_color:" + parts[3])
            elif action == "blink" and len(parts) == 6:
                color = COLORS.get(parts[3].upper())
                if color is not None:
                    hub.light.blink(color, [int(parts[4]), int(parts[5])])
                    print(">hub:light:done")
                else:
                    print(">error:unknown_color:" + parts[3])
            elif action == "off":
                hub.light.off()
                print(">hub:light:done")
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "charger":
            if action == "connected":
                print(">hub:charger:connected=" + str(hub.charger.connected()))
            elif action == "current":
                print(">hub:charger:current=" + str(hub.charger.current()))
            elif action == "status":
                print(">hub:charger:status=" + str(hub.charger.status()))
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "stream":
            if action == "start" and len(parts) == 5:
                name = parts[3]
                interval = int(parts[4])
                fn = _STREAM_FNS.get(name)
                if fn:
                    _streams[name] = {"interval": interval, "ticks": interval, "fn": fn}
                    print(">hub:stream:started:" + name)
                else:
                    print(">error:unknown_stream:" + name)
            elif action == "stop" and len(parts) == 4:
                name = parts[3]
                _streams.pop(name, None)
                print(">hub:stream:stopped:" + name)
            else:
                print(">error:unknown:" + cmd)

        else:
            print(">error:unknown:" + cmd)

    elif kind == "exec":
        try:
            exec(cmd[5:], globals())
            print(">exec:ok")
        except Exception as e:
            print(">exec:error:" + str(e))

    else:
        print(">error:unknown:" + cmd)


async def stdin_loop():
    buf = bytearray()
    while True:
        b = read_input_byte()
        if b is None:
            await wait(5)
            continue
        if b in (10, 13):
            if buf:
                cmd = str(buf, "utf-8").strip()
                buf = bytearray()
                try:
                    await dispatch(cmd)
                except Exception as e:
                    print(">error:exception:" + str(e))
        else:
            buf.append(b)


async def stream_loop():
    while True:
        await wait(10)
        for name in list(_streams):
            s = _streams[name]
            s["ticks"] += 10
            if s["ticks"] >= s["interval"]:
                s["ticks"] = 0
                try:
                    s["fn"]()
                except Exception as e:
                    print(">stream:error:" + name + ":" + str(e))


run_task(multitask(stdin_loop(), stream_loop()))
