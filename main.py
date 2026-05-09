from pybricks.hubs import InventorHub
from pybricks.iodevices import PUPDevice
from pybricks.parameters import Color, Port
from pybricks.tools import wait

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

hub = InventorHub()
hub.light.on(Color.GREEN)

for name, port in [("A", Port.A), ("B", Port.B), ("C", Port.C),
                    ("D", Port.D), ("E", Port.E), ("F", Port.F)]:
    try:
        dev = PUPDevice(port)
        type_id = dev.info()["id"]
        label = DEVICE_NAMES.get(type_id, "unknown:" + str(type_id))
        print(">port:" + name + "=" + label)
    except OSError:
        print(">port:" + name + "=none")

count = 0
while True:
    count += 1
    print(">alive:" + str(count))
    wait(2000)
