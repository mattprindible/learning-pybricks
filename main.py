from pybricks.hubs import InventorHub
from pybricks.parameters import Color

hub = InventorHub()
hub.light.on(Color.GREEN)

while True:
    line = input()
    if line:
        print(f">{line}")
