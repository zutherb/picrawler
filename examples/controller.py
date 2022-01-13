import collections
import time

from evdev import InputDevice, categorize, ecodes
import json

gamepad = InputDevice('/dev/input/event1')

print(gamepad)
print(gamepad.capabilities(verbose=True))
#evdev takes care of polling the controller in a loop
x=0
y=0
timestamp=0

while True:
  event = gamepad.read_one()
  if event is not None:
    if event.type == ecodes.EV_ABS:
      if event.timestamp() - timestamp >= 1:
        if event.code == ecodes.ABS_X:
          x = event.value
        if event.code == ecodes.ABS_Y:
          y = event.value
        print("x,",x,"y",y)
        if x > 40 and y < 40 and y > -40:
          timestamp = event.timestamp()
          print("right")
        if x < -40 and y < 40 and y > -40:
          timestamp = event.timestamp()
          print("left")
        if y > 40 and x < 40 and x > -40:
          timestamp = event.timestamp()
          print("up")
        if y < -40 and x < 40 and x > -40:
          timestamp = event.timestamp()
          print("down")

