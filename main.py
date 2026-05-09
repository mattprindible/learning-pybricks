from pybricks.hubs import InventorHub
from pybricks.parameters import Button, Color
from pybricks.tools import wait

hub = InventorHub()
hub.light.on(Color.GREEN)

count = 0
was_pressed = False

while True:
    pressed = Button.LEFT in hub.buttons.pressed()
    if pressed and not was_pressed:
        count += 1
        print(f"button:{count}")
        hub.light.on(Color.BLUE)
    elif not pressed and was_pressed:
        hub.light.on(Color.GREEN)
    was_pressed = pressed
    wait(20)
