#!/usr/bin/python3
import sys
sys.path.append(r'/opt/ezblock')
from ezblock import Pin

ledValue = 0

userBotton=Pin("SW")
led=Pin("LED")

def forever():
  global ledValue, userBotton, led
  if userBotton.value() == 0:
    ledValue = (ledValue+1) % 2
    print("Button pressed")
    led.value(ledValue)


if __name__ == "__main__":
    while True:
        forever()
