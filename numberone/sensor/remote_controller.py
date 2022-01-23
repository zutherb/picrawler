import time
from threading import Thread

from evdev import categorize, InputDevice, ecodes


class RemoteController(Thread):
  def __init__(self):
    Thread.__init__(self)
    self.name ="Remote Controller"

  def run(self):
    try:
      gamepad = InputDevice('/dev/input/event1')
      print(gamepad)
      x = 0
      y = 0
      timestamp = 0

      for event in gamepad.read_loop():
        if event is not None:
          if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_A:
            print("A")
          if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_B:
            print("B")
          if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_C:
            print("X")
          if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_X:
            print("Y")

          if event.timestamp() - timestamp >= 0.5:
            if event.type == ecodes.EV_ABS:
              if event.code == ecodes.ABS_X:
                x = event.value
              if event.code == ecodes.ABS_Y:
                y = event.value
              if x > 40 and y < 40 and y > -40:
                print("right","x,",x,"y",y)
                timestamp = event.timestamp()
              if x < -40 and y < 40 and y > -40:
                print("left","x,",x,"y",y)
                timestamp = event.timestamp()
              if y > 40 and x < 40 and x > -40:
                print("up","x,",x,"y",y)
                timestamp = event.timestamp()
              if y < -40 and x < 40 and x > -40:
                print("down","x,",x,"y",y)
                timestamp = event.timestamp()
    except FileNotFoundError as error:
      print("No Controller found", error)
      time.sleep(5)
