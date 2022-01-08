#!/usr/bin/python3
import sys
sys.path.append(r'/opt/ezblock')
from ezblock import Pin
from ezblock import delay

ledValue = 0

userBotton=Pin("SW")
led=Pin("LED")

def forever():
  global ledValue, userBotton, led
  if userBotton.value() == 0:
    ledValue = (ledValue+1) % 2
    print("Button pressed", ledValue)
    led.value(ledValue)
    delay(1000)


if __name__ == "__main__":
    while True:
        forever()
