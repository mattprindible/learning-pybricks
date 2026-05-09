from pybricks.hubs import InventorHub
from pybricks.iodevices import PUPDevice
from pybricks.pupdevices import Motor, UltrasonicSensor, ColorSensor
from pybricks.parameters import Color, Port

COLORS = {
    "RED": Color.RED, "GREEN": Color.GREEN, "BLUE": Color.BLUE,
    "YELLOW": Color.YELLOW, "ORANGE": Color.ORANGE, "CYAN": Color.CYAN,
    "MAGENTA": Color.MAGENTA, "VIOLET": Color.VIOLET, "WHITE": Color.WHITE,
    "GRAY": Color.GRAY, "BLACK": Color.BLACK, "NONE": Color.NONE,
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

while True:
    cmd = input()
    if not cmd:
        continue
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
            m.run_time(speed, duration, wait=True)
            print(">motor:" + port + ":done:angle=" + str(m.angle()))
        elif action == "run_angle" and len(parts) == 5:
            m.run_angle(int(parts[3]), int(parts[4]), wait=True)
            print(">motor:" + port + ":done:angle=" + str(m.angle()))
        elif action == "run_target" and len(parts) == 5:
            m.run_target(int(parts[3]), int(parts[4]), wait=True)
            print(">motor:" + port + ":done:angle=" + str(m.angle()))
        elif action == "reset_angle" and len(parts) == 4:
            m.reset_angle(int(parts[3]))
            print(">motor:" + port + ":angle_reset")
        elif action == "angle":
            print(">motor:" + port + ":angle=" + str(m.angle()))
        elif action == "speed":
            print(">motor:" + port + ":speed=" + str(m.speed()))
        elif action == "stop":
            m.stop()
            print(">motor:" + port + ":stopped")
        else:
            print(">error:unknown:" + cmd)

    elif kind == "sensor" and port in sensors:
        stype, dev = sensors[port]
        if stype == "sonic":
            print(">sensor:" + port + ":distance=" + str(dev.distance()))
        elif stype == "color":
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
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "battery":
            if action == "voltage":
                print(">hub:battery:voltage=" + str(hub.battery.voltage()))
            elif action == "current":
                print(">hub:battery:current=" + str(hub.battery.current()))
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
            elif action == "off":
                hub.display.off()
                print(">hub:display:done")
            else:
                print(">error:unknown:" + cmd)

        elif subsystem == "speaker":
            if action == "beep" and len(parts) == 5:
                hub.speaker.beep(int(parts[3]), int(parts[4]))
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
            elif action == "off":
                hub.light.off()
                print(">hub:light:done")
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
