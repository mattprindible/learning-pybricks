from pybricks.hubs import InventorHub
from pybricks.parameters import Color
from pybricks.tools import wait

hub = InventorHub()
hub.light.on(Color.GREEN)

count = 0
while True:
    count += 1
    print(">alive:" + str(count))
    wait(2000)
