#!/usr/bin/python3
import sys
sys.path.append(r'/opt/ezblock')
from ezblock import __reset_mcu__
import time
__reset_mcu__()
time.sleep(0.01)
from spider import Spider
from evdev import categorize, InputDevice, ecodes

__SPIDER__ = Spider([10,11,12,4,5,6,1,2,3,7,8,9])

gamepad = InputDevice('/dev/input/event1')

x = 0
y = 0
timestamp = 0

for event in gamepad.read_loop():
  if event is not None:
    if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_A:
      print("A")
      __SPIDER__.do_action('stand', 1, 100)
    if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_B:
      print("B")
      __SPIDER__.do_action('sit', 1, 100)
    if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_C:
      print("X")
      __SPIDER__.do_action('dance', 1, 100)
    if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_X:
      print("Y")
      __SPIDER__.do_action('push up', 1, 100)

    if event.timestamp() - timestamp >= 1.5:
      if event.type == ecodes.EV_ABS:
        if event.code == ecodes.ABS_X:
          x = event.value
        if event.code == ecodes.ABS_Y:
          y = event.value
        if x > 40 and y < 40 and y > -40:
          print("right","x,",x,"y",y)
          __SPIDER__.do_action('turn right', 1, 80)
          timestamp = event.timestamp()
        if x < -40 and y < 40 and y > -40:
          print("left","x,",x,"y",y)
          __SPIDER__.do_action('turn left', 1, 80)
          timestamp = event.timestamp()
        if y > 40 and x < 40 and x > -40:
          print("up","x,",x,"y",y)
          __SPIDER__.do_action('forward', 1, 80)
          timestamp = event.timestamp()
        if y < -40 and x < 40 and x > -40:
          print("down","x,",x,"y",y)
          __SPIDER__.do_action('backward', 1, 80)
          timestamp = event.timestamp()

